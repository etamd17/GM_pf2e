"""Framework-light web-security primitives shared by app startup and requests.

The Flask application is intentionally large and still supports two authority
models (accounts/campaigns and the legacy single-table password).  This module
keeps security decisions that do not require Flask isolated and deterministic:

* production-mode detection and fail-closed configuration validation;
* canonical Host/Origin parsing for proxy, Host, and CSRF checks;
* a bounded, thread-safe sliding-window rate limiter; and
* conservative response-header construction.

Nothing in this module reads process globals at import time.  Callers pass an
environment mapping and request values explicitly, which keeps startup checks
testable and prevents tests from accidentally inheriting the host environment.
"""

from __future__ import annotations

import ipaddress
import math
import os
import re
import threading
import time
from collections import deque
from collections.abc import Hashable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import SplitResult, urlsplit


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_PRODUCTION_NAMES = frozenset({"prod", "production", "stage", "staging"})
_RAILWAY_MARKERS = (
    "RAILWAY_DEPLOYMENT_ID",
    "RAILWAY_ENVIRONMENT_ID",
    "RAILWAY_PROJECT_ID",
    "RAILWAY_SERVICE_ID",
    "RAILWAY_GIT_COMMIT_SHA",
)
_SECRET_PLACEHOLDERS = frozenset(
    {
        "change-me",
        "changeme",
        "development",
        "example",
        "password",
        "secret",
        "test",
        "test-only",
    }
)
_DEFAULT_PORTS = {"http": 80, "https": 443}
_FETCH_SITES = frozenset({"cross-site", "same-origin", "same-site", "none"})
_CSP_DIRECTIVE_RE = re.compile(r"^[a-z][a-z0-9-]*$")


# ---------------------------------------------------------------------------
# Production configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfigurationIssue:
    """One stable, machine-testable production-configuration problem."""

    code: str
    message: str


class ProductionConfigurationError(RuntimeError):
    """Raised when a production process would start with unsafe configuration."""

    def __init__(self, issues: Iterable[ConfigurationIssue]):
        self.issues = tuple(issues)
        detail = "; ".join(issue.message for issue in self.issues)
        super().__init__(detail or "production security configuration is invalid")


def _env_value(environ: Mapping[str, object], name: str) -> str:
    value = environ.get(name, "")
    return value if isinstance(value, str) else str(value or "")


def _is_railway_runtime(environ: Mapping[str, object]) -> bool:
    return any(_env_value(environ, name).strip() for name in _RAILWAY_MARKERS)


def is_production_mode(environ: Mapping[str, object] | None = None) -> bool:
    """Return whether ``environ`` identifies a production-like deployment.

    A platform marker always wins over a local-looking ``APP_ENV`` value.  This
    prevents a stale ``APP_ENV=development`` variable from silently disabling
    hardening on Railway. Non-Railway deployments opt in explicitly with
    ``APP_ENV=production`` or ``ENVIRONMENT=production``
    (``prod``/``stage``/``staging`` are accepted too).  A production value in
    either variable wins over a stale local-looking value in the other; mixed
    deployment metadata must never downgrade the security boundary.
    """

    source = os.environ if environ is None else environ
    if _is_railway_runtime(source):
        return True
    environment_values = (
        _env_value(source, "APP_ENV"),
        _env_value(source, "ENVIRONMENT"),
    )
    return any(value.strip().lower() in _PRODUCTION_NAMES for value in environment_values)


def resolve_public_base_url(environ: Mapping[str, object] | None = None) -> str:
    """Resolve the configured public origin candidate without trusting a request.

    An explicit ``PUBLIC_BASE_URL`` always wins, including when it is invalid so
    validation fails closed instead of silently replacing a typo. Railway
    supplies the service's exact public host as ``RAILWAY_PUBLIC_DOMAIN``; use
    that host with HTTPS only when independent Railway runtime markers prove
    this is actually a Railway deployment.
    """

    source = os.environ if environ is None else environ
    explicit = _env_value(source, "PUBLIC_BASE_URL").strip()
    if explicit:
        return explicit
    railway_domain = _env_value(source, "RAILWAY_PUBLIC_DOMAIN").strip()
    if railway_domain and _is_railway_runtime(source):
        return f"https://{railway_domain}"
    return ""


def _secret_is_strong(value: str, *, minimum_length: int) -> bool:
    """Apply enforceable strength checks without pretending to measure entropy."""

    if len(value.encode("utf-8")) < minimum_length:
        return False
    normalized = re.sub(r"[\s_.-]+", "-", value.strip().lower()).strip("-")
    if normalized in _SECRET_PLACEHOLDERS:
        return False
    compact = re.sub(r"[^a-z0-9]+", "", normalized)
    for placeholder in _SECRET_PLACEHOLDERS:
        marker = re.sub(r"[^a-z0-9]+", "", placeholder)
        if marker and re.fullmatch(rf"(?:{re.escape(marker)})+[0-9]*", compact):
            return False
    # Preserve compatibility with generated hex/base64 values and passphrases,
    # while rejecting low-variety padding used to evade the length check.
    return len(set(value)) >= 8


def parse_trust_proxy_hops(value: object, *, default: int = 0) -> int:
    """Parse a bounded proxy trust count suitable for ``ProxyFix`` arguments."""

    raw = "" if value is None else str(value).strip()
    if not raw:
        if isinstance(default, bool) or not isinstance(default, int) or not 0 <= default <= 3:
            raise ValueError("default proxy hops must be an integer from 0 through 3")
        return default
    try:
        parsed = int(raw, 10)
    except ValueError as exc:
        raise ValueError("proxy hops must be an integer from 0 through 3") from exc
    if str(parsed) != raw or not 0 <= parsed <= 3:
        raise ValueError("proxy hops must be an integer from 0 through 3")
    return parsed


def _canonical_path(value: str | os.PathLike[str]) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _paths_are_same(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return os.path.normcase(str(left)) == os.path.normcase(str(right))


def validate_production_config(
    environ: Mapping[str, object] | None,
    *,
    base_dir: str | os.PathLike[str],
    bootstrap_pristine: bool,
    data_dir: str | os.PathLike[str] | None = None,
    data_dir_writable: bool | None = None,
    persisted_secret_key: str | None = None,
) -> tuple[ConfigurationIssue, ...]:
    """Return every unsafe production setting; local development returns ``()``.

    ``bootstrap_pristine`` must come from the strict account-store readiness
    check.  A missing/corrupt initialized store is *not* pristine and should be
    rejected by that existing fail-closed path rather than reopened as setup.

    ``data_dir_writable`` can consume the application's existing boot probe.  If
    omitted, this helper performs a non-mutating directory/access check.

    ``persisted_secret_key`` is an effective key the caller has already proved
    was read from or written to durable storage. It preserves established
    volume-backed deployments without accepting an in-memory per-boot fallback.
    A non-empty configured ``SECRET_KEY`` always wins, including when it is
    weak; an empty value is treated as unset, matching application startup.
    """

    source = os.environ if environ is None else environ
    if not is_production_mode(source):
        return ()

    issues: list[ConfigurationIssue] = []

    configured_secret_key = _env_value(source, "SECRET_KEY")
    secret_key = configured_secret_key or str(persisted_secret_key or "")
    if not secret_key:
        issues.append(
            ConfigurationIssue(
                "secret_key_missing",
                "SECRET_KEY is required in production.",
            )
        )
    elif not _secret_is_strong(secret_key, minimum_length=32):
        issues.append(
            ConfigurationIssue(
                "secret_key_weak",
                "SECRET_KEY must be at least 32 UTF-8 bytes and non-placeholder.",
            )
        )

    configured_data_dir = str(data_dir or _env_value(source, "DATA_DIR")).strip()
    resolved_data_dir: Path | None = None
    if not configured_data_dir:
        issues.append(
            ConfigurationIssue(
                "data_dir_missing",
                "DATA_DIR must identify the mounted persistent volume in production.",
            )
        )
    else:
        resolved_data_dir = _canonical_path(configured_data_dir)
        resolved_base_dir = _canonical_path(base_dir)
        if _paths_are_same(resolved_data_dir, resolved_base_dir):
            issues.append(
                ConfigurationIssue(
                    "data_dir_not_distinct",
                    "DATA_DIR must not resolve to the application checkout.",
                )
            )

        if data_dir_writable is None:
            writable = resolved_data_dir.is_dir() and os.access(resolved_data_dir, os.W_OK)
        else:
            writable = bool(data_dir_writable)
        if not writable:
            issues.append(
                ConfigurationIssue(
                    "data_dir_unwritable",
                    "DATA_DIR must exist and be writable before production starts.",
                )
            )

    if _env_value(source, "FLASK_DEBUG").strip().lower() in _TRUE_VALUES:
        issues.append(
            ConfigurationIssue(
                "flask_debug_enabled",
                "FLASK_DEBUG must be disabled in production.",
            )
        )

    public_base_url = resolve_public_base_url(source)
    if not public_base_url:
        issues.append(
            ConfigurationIssue(
                "public_base_url_missing",
                "PUBLIC_BASE_URL is required in production.",
            )
        )
    else:
        try:
            public_origin = canonical_origin(public_base_url)
        except ValueError:
            issues.append(
                ConfigurationIssue(
                    "public_base_url_invalid",
                    "PUBLIC_BASE_URL must be an HTTP(S) origin without credentials, "
                    "a query, fragment, or non-root path.",
                )
            )
        else:
            if public_origin.scheme != "https":
                issues.append(
                    ConfigurationIssue(
                        "public_base_url_insecure",
                        "PUBLIC_BASE_URL must use HTTPS in production.",
                    )
                )

    proxy_hops = _env_value(source, "TRUST_PROXY_HOPS").strip()
    if proxy_hops:
        try:
            parse_trust_proxy_hops(proxy_hops)
        except ValueError:
            issues.append(
                ConfigurationIssue(
                    "trust_proxy_hops_invalid",
                    "TRUST_PROXY_HOPS must be an integer from 0 through 3.",
                )
            )

    configured_hosts = [
        item.strip()
        for item in _env_value(source, "ALLOWED_HOSTS").split(",")
        if item.strip()
    ]
    configured_hosts.extend(
        value
        for name in ("RAILWAY_PUBLIC_DOMAIN", "RAILWAY_PRIVATE_DOMAIN")
        if (value := _env_value(source, name).strip())
    )
    try:
        parse_allowed_hosts(configured_hosts)
    except ValueError:
        issues.append(
            ConfigurationIssue(
                "allowed_hosts_invalid",
                "ALLOWED_HOSTS and Railway domain values must contain exact hosts only.",
            )
        )

    if bootstrap_pristine:
        setup_token = _env_value(source, "SETUP_TOKEN")
        if not setup_token:
            issues.append(
                ConfigurationIssue(
                    "setup_token_missing",
                    "SETUP_TOKEN is required while first-admin setup is available.",
                )
            )
        elif not _secret_is_strong(setup_token, minimum_length=24):
            issues.append(
                ConfigurationIssue(
                    "setup_token_weak",
                    "SETUP_TOKEN must be at least 24 UTF-8 bytes and non-placeholder.",
                )
            )

    return tuple(issues)


def require_production_config(
    environ: Mapping[str, object] | None,
    **kwargs: object,
) -> None:
    """Raise :class:`ProductionConfigurationError` for unsafe production config."""

    issues = validate_production_config(environ, **kwargs)
    if issues:
        raise ProductionConfigurationError(issues)


# ---------------------------------------------------------------------------
# Canonical Host and Origin handling
# ---------------------------------------------------------------------------


def _normalize_hostname(hostname: str) -> str:
    value = hostname.strip().rstrip(".")
    if not value or any(ord(char) < 33 for char in value):
        raise ValueError("host is empty or contains whitespace/control characters")
    if "*" in value or "\\" in value:
        raise ValueError("wildcard and backslash hosts are not allowed")
    try:
        return ipaddress.ip_address(value).compressed.lower()
    except ValueError:
        try:
            ascii_host = value.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("host is not valid IDNA") from exc
        if not ascii_host or len(ascii_host) > 253:
            raise ValueError("host is too long")
        labels = ascii_host.split(".")
        if any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not re.fullmatch(r"[a-z0-9-]+", label)
            for label in labels
        ):
            raise ValueError("host contains an invalid DNS label")
        return ascii_host


def _validated_port(parts: SplitResult) -> int | None:
    authority = parts.netloc.rsplit("@", 1)[-1]
    if authority.endswith(":"):
        raise ValueError("port is empty")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("port is invalid") from exc
    if port is not None and not (1 <= port <= 65535):
        raise ValueError("port is outside 1..65535")
    return port


@dataclass(frozen=True, slots=True, order=True)
class CanonicalHost:
    host: str
    port: int | None = None

    @property
    def authority(self) -> str:
        rendered = f"[{self.host}]" if ":" in self.host else self.host
        return f"{rendered}:{self.port}" if self.port is not None else rendered

    def __str__(self) -> str:
        return self.authority


def canonical_host(value: str) -> CanonicalHost:
    """Parse an exact Host value; schemes, paths, credentials and wildcards fail."""

    raw = str(value or "").strip()
    if not raw or "://" in raw or any(delimiter in raw for delimiter in "/?#"):
        raise ValueError("host must not be empty or include a scheme")
    parts = urlsplit(f"//{raw}", allow_fragments=True)
    if (
        not parts.netloc
        or parts.path
        or parts.query
        or parts.fragment
        or parts.username is not None
        or parts.password is not None
        or parts.hostname is None
    ):
        raise ValueError("host must contain only hostname and optional port")
    return CanonicalHost(_normalize_hostname(parts.hostname), _validated_port(parts))


def parse_allowed_hosts(raw: str | Iterable[str] | None) -> frozenset[CanonicalHost]:
    """Parse a comma-separated or iterable exact-host allowlist.

    Wildcards are deliberately unsupported: the production app has a small,
    known set of public names, and wildcard subdomains recreate the sibling-host
    trust problem this guard is intended to prevent.
    """

    if raw is None:
        return frozenset()
    values = raw.split(",") if isinstance(raw, str) else raw
    parsed: set[CanonicalHost] = set()
    for value in values:
        item = str(value).strip()
        if item:
            parsed.add(canonical_host(item))
    return frozenset(parsed)


def host_is_allowed(
    request_host: str,
    allowed_hosts: Iterable[CanonicalHost | str],
    *,
    scheme: str | None = None,
) -> bool:
    """Check an exact request Host, treating an omitted default port as equal."""

    candidate = canonical_host(request_host)
    parsed = {
        host if isinstance(host, CanonicalHost) else canonical_host(host)
        for host in allowed_hosts
    }
    if candidate in parsed:
        return True
    normalized_scheme = str(scheme or "").lower()
    default_port = _DEFAULT_PORTS.get(normalized_scheme)
    if default_port is None:
        return False
    candidate_port = candidate.port or default_port
    return any(
        allowed.host == candidate.host
        and (allowed.port or default_port) == candidate_port
        for allowed in parsed
    )


@dataclass(frozen=True, slots=True, order=True)
class CanonicalOrigin:
    scheme: str
    host: str
    port: int

    @property
    def authority(self) -> str:
        rendered = f"[{self.host}]" if ":" in self.host else self.host
        default = _DEFAULT_PORTS[self.scheme]
        return rendered if self.port == default else f"{rendered}:{self.port}"

    @property
    def value(self) -> str:
        return f"{self.scheme}://{self.authority}"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class PublicOriginPolicy:
    """Canonical public origin and its exact request-Host allowlist."""

    origin: CanonicalOrigin
    allowed_hosts: frozenset[CanonicalHost]


def canonical_origin(value: str, *, allow_path: bool = False) -> CanonicalOrigin:
    """Parse an HTTP(S) Origin, optionally extracting it from a Referer URL."""

    raw = str(value or "").strip()
    if not raw or raw.lower() == "null" or any(char in raw for char in "\r\n"):
        raise ValueError("origin is empty, opaque, or malformed")
    parts = urlsplit(raw, allow_fragments=True)
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise ValueError("only http and https origins are supported")
    if parts.hostname is None or parts.username is not None or parts.password is not None:
        raise ValueError("origin must not contain credentials")
    if not allow_path and (
        parts.path not in ("", "/")
        or "?" in raw
        or "#" in raw
    ):
        raise ValueError("Origin header must not contain path, query, or fragment")
    port = _validated_port(parts) or _DEFAULT_PORTS[scheme]
    return CanonicalOrigin(scheme, _normalize_hostname(parts.hostname), port)


def public_origin_policy(
    public_base_url: str,
    *,
    require_https: bool = True,
) -> PublicOriginPolicy:
    """Derive exact origin/Host policy from a configured public base URL.

    The default port is omitted from the Host allowlist so both an omitted and
    explicit default port compare equal through :func:`host_is_allowed`.
    """

    origin = canonical_origin(public_base_url)
    if require_https and origin.scheme != "https":
        raise ValueError("public base URL must use HTTPS")
    host_port = None if origin.port == _DEFAULT_PORTS[origin.scheme] else origin.port
    return PublicOriginPolicy(
        origin=origin,
        allowed_hosts=frozenset({CanonicalHost(origin.host, host_port)}),
    )


def request_origin(scheme: str, host: str) -> CanonicalOrigin:
    """Build a canonical origin from trusted request scheme plus Host."""

    normalized_scheme = str(scheme or "").strip().lower()
    if normalized_scheme not in _DEFAULT_PORTS:
        raise ValueError("request scheme must be http or https")
    parsed_host = canonical_host(host)
    return CanonicalOrigin(
        normalized_scheme,
        parsed_host.host,
        parsed_host.port or _DEFAULT_PORTS[normalized_scheme],
    )


@dataclass(frozen=True, slots=True)
class OriginDecision:
    allowed: bool
    reason: str
    source: str | None = None
    origin: CanonicalOrigin | None = None


def validate_request_origin(
    *,
    request_scheme: str,
    request_host: str,
    origin_header: str | None = None,
    referer_header: str | None = None,
    sec_fetch_site: str | None = None,
    allowed_hosts: Iterable[CanonicalHost | str] | None = None,
    allowed_origins: Iterable[CanonicalOrigin | str] = (),
    allow_missing: bool = False,
) -> OriginDecision:
    """Validate the browser provenance inputs for a state-changing request.

    The safe default is exact same-origin.  ``allowed_origins`` exists for an
    explicitly reviewed canonical public origin, not wildcard CORS.  Missing
    provenance fails closed unless the caller has independently authenticated a
    non-cookie integration and sets ``allow_missing=True``.
    """

    try:
        target = request_origin(request_scheme, request_host)
    except ValueError:
        return OriginDecision(False, "invalid_request_origin")

    if allowed_hosts is not None:
        try:
            if not host_is_allowed(request_host, allowed_hosts, scheme=request_scheme):
                return OriginDecision(False, "request_host_not_allowed")
        except ValueError:
            return OriginDecision(False, "invalid_request_host")

    trusted = {target}
    try:
        trusted.update(
            origin if isinstance(origin, CanonicalOrigin) else canonical_origin(origin)
            for origin in allowed_origins
        )
    except ValueError:
        return OriginDecision(False, "invalid_allowed_origin")

    fetch_site = str(sec_fetch_site or "").strip().lower()
    if fetch_site and fetch_site not in _FETCH_SITES:
        return OriginDecision(False, "invalid_fetch_site")

    source: str | None
    candidate: CanonicalOrigin | None
    if origin_header is not None:
        source = "origin"
        try:
            candidate = canonical_origin(origin_header)
        except ValueError:
            return OriginDecision(False, "invalid_origin", source=source)
    elif referer_header is not None:
        source = "referer"
        try:
            candidate = canonical_origin(referer_header, allow_path=True)
        except ValueError:
            return OriginDecision(False, "invalid_referer", source=source)
    else:
        source = None
        candidate = None

    if candidate is None:
        if fetch_site in {"cross-site", "same-site"}:
            return OriginDecision(False, "cross_origin_fetch")
        return OriginDecision(bool(allow_missing), "missing_allowed" if allow_missing else "missing_origin")

    if candidate not in trusted:
        return OriginDecision(False, "origin_mismatch", source=source, origin=candidate)

    # Exact-origin headers and Fetch Metadata should agree.  An explicitly
    # trusted extra origin is still rejected here when the browser labels it
    # cross-site: cookie-authenticated mutations are not a CORS surface.
    if fetch_site in {"cross-site", "same-site"}:
        return OriginDecision(False, "cross_origin_fetch", source=source, origin=candidate)
    return OriginDecision(True, "same_origin", source=source, origin=candidate)


# ---------------------------------------------------------------------------
# Bounded sliding-window limiter
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int

    @property
    def retry_after_header(self) -> str | None:
        return str(self.retry_after) if not self.allowed else None


class SlidingWindowLimiter:
    """Thread-safe, bounded per-key sliding-window limiter.

    At most ``max_keys * limit`` ordinary timestamps are retained.  Once the
    named-key budget is full, unseen keys share one bounded overflow bucket
    rather than allocating unbounded attacker-controlled dictionaries or
    evicting active keys to bypass their limits.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        *,
        max_keys: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if not math.isfinite(window_seconds) or window_seconds <= 0:
            raise ValueError("window_seconds must be finite and positive")
        if isinstance(max_keys, bool) or not isinstance(max_keys, int) or max_keys < 1:
            raise ValueError("max_keys must be a positive integer")
        self.limit = limit
        self.window_seconds = float(window_seconds)
        self.max_keys = max_keys
        self._clock = clock
        self._events: dict[Hashable, deque[float]] = {}
        self._overflow: deque[float] = deque()
        self._lock = threading.RLock()

    @staticmethod
    def _prune_queue(events: deque[float], cutoff: float) -> None:
        while events and events[0] <= cutoff:
            events.popleft()

    def _prune_locked(self, now: float) -> int:
        cutoff = now - self.window_seconds
        removed = 0
        for key, events in tuple(self._events.items()):
            before = len(events)
            self._prune_queue(events, cutoff)
            removed += before - len(events)
            if not events:
                del self._events[key]
        before = len(self._overflow)
        self._prune_queue(self._overflow, cutoff)
        return removed + before - len(self._overflow)

    def prune(self, *, now: float | None = None) -> int:
        """Remove expired timestamps and keys; return timestamp count removed."""

        current = self._clock() if now is None else float(now)
        if not math.isfinite(current):
            raise ValueError("clock must return a finite timestamp")
        with self._lock:
            return self._prune_locked(current)

    def _queue_for_key(self, key: Hashable, now: float) -> deque[float]:
        try:
            hash(key)
        except TypeError as exc:
            raise TypeError("rate-limit key must be hashable") from exc
        events = self._events.get(key)
        if events is not None:
            return events
        if len(self._events) >= self.max_keys:
            self._prune_locked(now)
        if len(self._events) >= self.max_keys:
            return self._overflow
        events = deque()
        self._events[key] = events
        return events

    def consume(self, key: Hashable, *, now: float | None = None) -> RateLimitDecision:
        """Consume one attempt and return a deterministic allow/deny decision."""

        current = self._clock() if now is None else float(now)
        if not math.isfinite(current):
            raise ValueError("clock must return a finite timestamp")
        with self._lock:
            events = self._queue_for_key(key, current)
            self._prune_queue(events, current - self.window_seconds)
            if len(events) >= self.limit:
                retry_after = max(
                    1,
                    math.ceil(events[0] + self.window_seconds - current),
                )
                return RateLimitDecision(False, self.limit, 0, retry_after)
            events.append(current)
            return RateLimitDecision(
                True,
                self.limit,
                self.limit - len(events),
                0,
            )

    def reset(self, key: Hashable) -> bool:
        """Forget a known key, for example after a successful authentication."""

        with self._lock:
            return self._events.pop(key, None) is not None

    @property
    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._events)

    @property
    def stored_events(self) -> int:
        with self._lock:
            return sum(len(events) for events in self._events.values()) + len(self._overflow)


# ---------------------------------------------------------------------------
# Response headers
# ---------------------------------------------------------------------------


BASELINE_CSP_DIRECTIVES: Mapping[str, tuple[str, ...]] = {
    # These directives are enforceable without breaking the app's current
    # inline script/style inventory.  A nonce-based script/style policy should
    # be introduced separately through Report-Only first.
    "base-uri": ("'self'",),
    "object-src": ("'none'",),
    "frame-ancestors": ("'self'",),
    "form-action": ("'self'",),
}

DEFAULT_PERMISSIONS_POLICY = (
    "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
)


def _safe_header_value(value: str) -> str:
    rendered = str(value)
    if not rendered or any(ord(char) < 32 or ord(char) == 127 for char in rendered):
        raise ValueError("header values must be non-empty and contain no controls")
    return rendered


def build_csp(directives: Mapping[str, Iterable[str] | str]) -> str:
    """Serialize validated CSP directives without header-injection ambiguity."""

    rendered: list[str] = []
    for raw_name, raw_sources in directives.items():
        name = str(raw_name).strip().lower()
        if not _CSP_DIRECTIVE_RE.fullmatch(name):
            raise ValueError(f"invalid CSP directive: {raw_name!r}")
        sources = (raw_sources,) if isinstance(raw_sources, str) else raw_sources
        clean: list[str] = []
        seen: set[str] = set()
        for source in sources:
            token = str(source).strip()
            if not token or ";" in token or "," in token or "\r" in token or "\n" in token:
                raise ValueError(f"invalid CSP source for {name!r}")
            if token not in seen:
                seen.add(token)
                clean.append(token)
        rendered.append(" ".join((name, *clean)) if clean else name)
    return _safe_header_value("; ".join(rendered))


def build_security_headers(
    *,
    production: bool,
    request_is_secure: bool,
    sensitive: bool = False,
    enforced_csp: Mapping[str, Iterable[str] | str] | str | None = None,
    report_only_csp: Mapping[str, Iterable[str] | str] | str | None = None,
    hsts_seconds: int = 31_536_000,
) -> dict[str, str]:
    """Return conservative headers suitable for a Flask ``after_request`` hook."""

    if isinstance(hsts_seconds, bool) or not isinstance(hsts_seconds, int) or hsts_seconds < 0:
        raise ValueError("hsts_seconds must be a non-negative integer")

    csp_value = (
        build_csp(BASELINE_CSP_DIRECTIVES)
        if enforced_csp is None
        else (
            _safe_header_value(enforced_csp)
            if isinstance(enforced_csp, str)
            else build_csp(enforced_csp)
        )
    )
    headers = {
        "Content-Security-Policy": csp_value,
        "Permissions-Policy": DEFAULT_PERMISSIONS_POLICY,
        "Referrer-Policy": "same-origin",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "X-Permitted-Cross-Domain-Policies": "none",
    }
    if production and request_is_secure:
        headers["Strict-Transport-Security"] = f"max-age={hsts_seconds}"
    if report_only_csp is not None:
        headers["Content-Security-Policy-Report-Only"] = (
            _safe_header_value(report_only_csp)
            if isinstance(report_only_csp, str)
            else build_csp(report_only_csp)
        )
    if sensitive:
        headers["Cache-Control"] = "no-store, max-age=0"
        headers["Pragma"] = "no-cache"
    return {name: _safe_header_value(value) for name, value in headers.items()}


def apply_security_headers(
    target: MutableMapping[str, str],
    *,
    overwrite: bool = False,
    **kwargs: object,
) -> MutableMapping[str, str]:
    """Apply :func:`build_security_headers` to a response-like header mapping."""

    existing_names = {str(name).lower(): name for name in target}
    for name, value in build_security_headers(**kwargs).items():
        existing_name = existing_names.get(name.lower())
        if existing_name is None:
            target[name] = value
            existing_names[name.lower()] = name
        elif overwrite:
            target[existing_name] = value
    return target
