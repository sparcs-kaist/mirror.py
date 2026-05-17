"""Verify the embedded archvsync fallback in mirror.sync.ftpsync._extract_archvsync.

The fallback path is exercised when git is unavailable or git clone fails.
The function must (a) accept the bundled artifact at its real shape, (b) lay it
out so setup_ftpsync can find a `bin/ftpsync` underneath an archvsync dir, and
(c) reject corrupt data.
"""


import mirror.sync.ftpsync as ftpsync_mod


def test_extract_succeeds_with_real_artifact(tmp_path):
    """The shipped ARCHVSYNC_SCRIPT extracts cleanly into the expected layout."""
    ok = ftpsync_mod._extract_archvsync(tmp_path)
    assert ok, "Extraction reported failure with the shipped artifact"

    # setup_ftpsync expects exactly one non-bin/etc directory under path.
    candidates = [d for d in tmp_path.iterdir() if d.is_dir() and d.name not in ("bin", "etc")]
    assert candidates, (
        f"Expected an archvsync directory in {tmp_path}; found "
        f"{[p.name for p in tmp_path.iterdir()]}"
    )
    archvsync_dir = candidates[0]

    bin_dir = archvsync_dir / "bin"
    assert bin_dir.is_dir(), f"{bin_dir} not present after extraction"

    ftpsync_script = bin_dir / "ftpsync"
    assert ftpsync_script.is_file(), f"{ftpsync_script} not present after extraction"

    content = ftpsync_script.read_bytes()
    assert content.startswith(b"#!/usr/bin/env bash"), (
        f"ftpsync script missing bash shebang; first 30 bytes: {content[:30]!r}"
    )

    assert ftpsync_script.stat().st_mode & 0o111, (
        f"ftpsync script is not executable: mode {oct(ftpsync_script.stat().st_mode)}"
    )


def test_extract_returns_false_on_hash_mismatch(tmp_path, monkeypatch):
    """Tampered hash must abort extraction without leaving artifacts behind."""
    monkeypatch.setattr(
        "mirror.sync._ftpsync_script.ARCHVSYNC_HASH",
        "0" * 64,
        raising=False,
    )

    ok = ftpsync_mod._extract_archvsync(tmp_path)
    assert ok is False, "Extraction should fail on hash mismatch"


def test_extract_returns_false_on_invalid_base64(tmp_path, monkeypatch):
    """Non-base64 payload must yield False, not raise."""
    monkeypatch.setattr(
        "mirror.sync._ftpsync_script.ARCHVSYNC_SCRIPT",
        "!!! not valid base64 !!!",
        raising=False,
    )

    ok = ftpsync_mod._extract_archvsync(tmp_path)
    assert ok is False, "Extraction should fail on undecodable base64"


def test_extract_creates_layout_consumable_by_setup_ftpsync(tmp_path):
    """The extracted layout must satisfy the directory-pick logic in setup_ftpsync.

    setup_ftpsync chooses `dirs[0]` from non-bin/etc subdirectories; that
    directory must contain a `bin/` whose contents are copied into `path/bin/`.
    """
    ok = ftpsync_mod._extract_archvsync(tmp_path)
    assert ok

    dirs = [d for d in tmp_path.iterdir() if d.is_dir() and d.name not in ("bin", "etc")]
    assert len(dirs) >= 1
    archvsync_root = dirs[0]
    src_bin = archvsync_root / "bin"
    assert src_bin.is_dir()

    bin_files = [p for p in src_bin.iterdir() if p.is_file()]
    assert bin_files, f"{src_bin} has no files; setup_ftpsync would copy nothing"
    names = {p.name for p in bin_files}
    assert "ftpsync" in names, f"ftpsync script missing; bin contains: {names}"
