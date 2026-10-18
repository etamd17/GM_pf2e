"""Round-trip check of the configured VTT asset store.

Uses the same environment variables as the app (VTT_ASSETS_BACKEND and the
VTT_ASSETS_S3_* settings, or DATA_DIR for the local store). It writes one small
probe object under the reserved campaign id ``vtt-probe``, reads it back, and
checks that a key never written reads as missing. It exits non-zero on any
failure, so it can run as a deploy's pre-deploy command. Nothing secret is
printed.

    python tools/vtt/check_asset_store.py
"""

from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from vtt.assets import backends  # noqa: E402

PROBE = b"vtt asset store probe\n"


def main() -> int:
    store = backends.from_env()
    kind = type(store).__name__
    where = getattr(store, "host", None) or getattr(store, "root", "")
    present = backends.key_for("vtt-probe", hashlib.sha256(PROBE).hexdigest())
    absent = backends.key_for("vtt-probe", hashlib.sha256(b"never written").hexdigest())
    try:
        store.put(present, PROBE, "text/plain")
        read = store.get(present)
        missing = store.get(absent)
    except backends.AssetBackendError as exc:
        print(f"asset store check FAILED ({kind} {where}): {exc}")
        return 1
    if read != PROBE:
        print(f"asset store check FAILED ({kind} {where}): read back {len(read or b'')} bytes, not the probe")
        return 1
    if missing is not None:
        print(f"asset store check FAILED ({kind} {where}): a key never written was found")
        return 1
    print(f"asset store check passed ({kind} {where})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
