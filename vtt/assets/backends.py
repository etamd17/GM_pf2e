"""Where asset bytes live (M6, handoff 053).

Two backends behind one small interface, chosen by the environment:

- ``LocalBackend``: files under a directory (``DATA_DIR/vtt-assets`` in the
  app), for development, tests and a single-volume deploy. Writes are atomic.
- ``S3Backend``: any S3-compatible store (MinIO locally, object storage at
  staging). Implemented on the standard library with AWS Signature Version 4,
  so the production requirements gain no SDK; the key is the content hash, so
  a write is naturally idempotent.

Keys are ``<campaign id>/<sha256>``. Both parts are validated before they touch
a path or a URL. Nothing here authorizes anything: the ``vtt_assets`` rows do.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Protocol

_CAMPAIGN = re.compile(r"[A-Za-z0-9_-]{1,64}")
_SHA = re.compile(r"[0-9a-f]{64}")


class AssetBackendError(RuntimeError):
    """The backend could not be reached or refused the operation."""


def key_for(campaign_id: str, sha256: str) -> str:
    if not _CAMPAIGN.fullmatch(campaign_id or "") or not _SHA.fullmatch(sha256 or ""):
        raise ValueError("bad asset key")
    return f"{campaign_id}/{sha256}"


class AssetBackend(Protocol):
    def put(self, key: str, data: bytes, media_type: str) -> None: ...
    def get(self, key: str) -> bytes | None: ...


class LocalBackend:
    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        campaign_id, sha = key.split("/", 1)
        key_for(campaign_id, sha)                      # validates both parts
        return self.root / campaign_id / sha

    # Disk trouble (full, unwritable, unreadable) is an AssetBackendError like
    # an S3 outage, so routes answer a retryable 503 and the import refuses
    # the scene, instead of a raw 500 (handoff 055). A bad key stays ValueError.
    def put(self, key: str, data: bytes, media_type: str) -> None:
        path = self._path(key)
        tmp: str | None = None
        try:
            if path.is_file() and path.stat().st_size == len(data):
                return                                  # same content hash: already stored
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".upload")
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
            tmp = None
        except OSError as exc:
            raise AssetBackendError(f"asset store write failed: {type(exc).__name__}") from exc
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass                                # best effort; the error above is what matters

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise AssetBackendError(f"asset store read failed: {type(exc).__name__}") from exc


class S3Backend:
    """S3 requests signed with SigV4. ``url_style`` is ``path`` (MinIO's and
    SeaweedFS's default: ``endpoint/bucket/key``) or ``virtual`` (the bucket as
    a subdomain: ``bucket.endpoint/key``, which new Railway buckets require)."""

    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str, region: str = "us-east-1",
                 timeout: float = 10.0, now=None, url_style: str = "path"):
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("VTT_ASSETS_S3_ENDPOINT must be an http(s) URL")
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket or ""):
            raise ValueError("bad bucket name")
        if url_style not in ("path", "virtual"):
            raise ValueError("VTT_ASSETS_S3_URL_STYLE must be path or virtual")
        if url_style == "virtual" and "." in bucket:
            raise ValueError("a virtual-hosted bucket name cannot contain dots (TLS would not match)")
        self.scheme, self.url_style = parsed.scheme, url_style
        self.host = f"{bucket}.{parsed.netloc}" if url_style == "virtual" else parsed.netloc
        self.bucket, self.access_key, self.secret_key = bucket, access_key, secret_key
        self.region, self.timeout = region, timeout
        self._now = now or (lambda: _dt.datetime.now(_dt.timezone.utc))

    def _signed(self, method: str, key: str, body: bytes, headers: dict[str, str]) -> urllib.request.Request:
        campaign_id, sha = key.split("/", 1)
        key_for(campaign_id, sha)
        object_path = key if self.url_style == "virtual" else f"{self.bucket}/{key}"
        return self._request(method, "/" + urllib.parse.quote(object_path, safe="/"), body, headers)

    def _request(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> urllib.request.Request:
        """A SigV4-signed request for an already-encoded absolute path."""
        stamp = self._now().strftime("%Y%m%dT%H%M%SZ")
        day = stamp[:8]
        payload_hash = hashlib.sha256(body).hexdigest()
        all_headers = {"host": self.host, "x-amz-content-sha256": payload_hash, "x-amz-date": stamp,
                       **{k.lower(): v for k, v in headers.items()}}
        names = sorted(all_headers)
        canonical = "\n".join([
            method, path, "",
            "".join(f"{n}:{all_headers[n].strip()}\n" for n in names),
            ";".join(names), payload_hash,
        ])
        scope = f"{day}/{self.region}/s3/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        signing = f"AWS4{self.secret_key}".encode()
        for part in (day, self.region, "s3", "aws4_request"):
            signing = hmac.new(signing, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(signing, to_sign.encode(), hashlib.sha256).hexdigest()
        request = urllib.request.Request(f"{self.scheme}://{self.host}{path}", data=body or None, method=method)
        for name in names:
            if name != "host":
                request.add_header(name, all_headers[name])
        request.add_header("Authorization", f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
                                            f"SignedHeaders={';'.join(names)}, Signature={signature}")
        return request

    def put(self, key: str, data: bytes, media_type: str) -> None:
        request = self._signed("PUT", key, data, {"content-type": media_type})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - fixed scheme
                response.read()
        except (urllib.error.URLError, OSError) as exc:
            raise AssetBackendError(f"asset store write failed: {type(exc).__name__}") from exc

    def get(self, key: str) -> bytes | None:
        request = self._signed("GET", key, b"", {})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - fixed scheme
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise AssetBackendError(f"asset store read failed: HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise AssetBackendError(f"asset store read failed: {type(exc).__name__}") from exc


def from_env(environ: dict[str, str] | None = None, data_dir: str | None = None) -> AssetBackend:
    """``VTT_ASSETS_BACKEND`` is ``local`` (default) or ``s3``."""
    env = environ if environ is not None else os.environ
    kind = env.get("VTT_ASSETS_BACKEND", "local")
    if kind == "s3":
        missing = [n for n in ("VTT_ASSETS_S3_ENDPOINT", "VTT_ASSETS_S3_BUCKET", "VTT_ASSETS_S3_ACCESS_KEY",
                               "VTT_ASSETS_S3_SECRET_KEY") if not env.get(n)]
        if missing:
            raise ValueError("VTT_ASSETS_BACKEND=s3 needs " + ", ".join(missing))
        return S3Backend(env["VTT_ASSETS_S3_ENDPOINT"], env["VTT_ASSETS_S3_BUCKET"], env["VTT_ASSETS_S3_ACCESS_KEY"],
                         env["VTT_ASSETS_S3_SECRET_KEY"], env.get("VTT_ASSETS_S3_REGION", "us-east-1"),
                         url_style=env.get("VTT_ASSETS_S3_URL_STYLE", "path"))
    if kind != "local":
        raise ValueError("VTT_ASSETS_BACKEND must be local or s3")
    root = env.get("VTT_ASSETS_DIR") or os.path.join(data_dir or env.get("DATA_DIR") or ".", "vtt-assets")
    return LocalBackend(root)
