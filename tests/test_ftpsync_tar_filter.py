"""Tar extraction must use the data filter to block path traversal."""
import io
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

import mirror.sync.ftpsync as ftpsync_mod


def _make_evil_tarball() -> tuple[bytes, str]:
    """Create a minimal tar.gz with a malicious traversal entry."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # First, a benign entry so the archive isn't suspicious by emptiness.
        info = tarfile.TarInfo(name="archvsync/README")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"ok\n\n"))

        # Then a traversal entry that data filter must block.
        evil = tarfile.TarInfo(name="../../../../tmp/PWNED_TARFILTER")
        evil.size = 5
        tar.addfile(evil, io.BytesIO(b"pwned"))
    data = buf.getvalue()
    import hashlib
    h = hashlib.sha256(data).hexdigest()
    return data, h


def test_extract_blocked_by_data_filter(tmp_path, monkeypatch):
    data, sha = _make_evil_tarball()
    import base64
    encoded = base64.b64encode(data)

    # Patch the bundled archvsync constants to point at our tarball.
    monkeypatch.setattr(
        "mirror.sync._ftpsync_script.ARCHVSYNC_HASH", sha, raising=False
    )
    monkeypatch.setattr(
        "mirror.sync._ftpsync_script.ARCHVSYNC_SCRIPT", encoded, raising=False
    )

    out = ftpsync_mod._extract_archvsync(tmp_path)
    # Result should still be True (extraction "succeeded" — benign entry extracted).
    # But the malicious entry must NOT escape `tmp_path`.
    pwned = Path("/tmp/PWNED_TARFILTER")
    assert not pwned.exists(), "data filter should have blocked the traversal"
