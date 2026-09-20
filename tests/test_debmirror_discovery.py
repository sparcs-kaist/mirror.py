"""Tests for automatic debmirror option discovery."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mirror.sync import debmirror as discovery


def _release(
    codename: str = "bookworm",
    components: str = "main contrib",
    architectures: str = "amd64 all",
) -> bytes:
    return (
        f"Suite: stable\nCodename: {codename}\n"
        f"Components: {components}\nArchitectures: {architectures}\n"
    ).encode()


def _argv(root: Path, *options: str) -> list[str]:
    return [
        "debmirror", "--method", "file", "--root", str(root),
        "--no-check-gpg", "--nosource", *options, str(root / "mirror"),
    ]


def _write_release(root: Path, dist: str, data: bytes) -> None:
    path = root / "dists" / dist
    path.mkdir(parents=True)
    (path / "Release").write_bytes(data)


def _flag(argv: list[str], name: str) -> str:
    parsed = discovery._scan_options(argv)
    value = discovery._read_flag(parsed, name)
    assert value is not None
    return value


def test_resolve_command_discovers_unions_and_deduplicates_aliases(tmp_path: Path) -> None:
    bookworm = _release()
    _write_release(tmp_path, "bookworm", bookworm)
    _write_release(tmp_path, "stable", bookworm)
    _write_release(tmp_path, "trixie", _release("trixie", "main non-free", "arm64 all"))
    (tmp_path / "dists" / "README").write_text("ignored", encoding="utf-8")

    result = discovery.resolve_command(_argv(tmp_path))

    assert _flag(result, "--dist") == "bookworm,trixie"
    assert _flag(result, "--section") == "contrib,main,non-free"
    assert _flag(result, "--arch") == "amd64,arm64"
    assert result[-1] == str(tmp_path / "mirror")


def test_explicit_dimensions_are_preserved_independently(tmp_path: Path) -> None:
    _write_release(tmp_path, "bookworm", _release(components="main extras", architectures="amd64 arm64"))

    result = discovery.resolve_command(
        _argv(tmp_path, "--dist", "bookworm", "--section", "main")
    )

    assert _flag(result, "--dist") == "bookworm"
    assert _flag(result, "--section") == "main"
    assert _flag(result, "--arch") == "amd64,arm64"


def test_complete_native_command_does_no_io_and_parser_respects_filter_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(discovery, "_fetch", MagicMock(side_effect=AssertionError("unexpected I/O")))
    argv = _argv(
        tmp_path,
        "--exclude", "--arch",
        "--dist", "bookworm",
        "--section", "main",
        "--arch", "amd64",
    )

    assert discovery.resolve_command(argv) == argv


@pytest.mark.parametrize(
    ("architectures", "source", "expected"),
    [("all", False, "none"), ("source", True, "none")],
)
def test_non_binary_archives_select_none(
    tmp_path: Path, architectures: str, source: bool, expected: str
) -> None:
    _write_release(tmp_path, "bookworm", _release(architectures=architectures))
    argv = _argv(tmp_path, "--dist", "bookworm", "--section", "main")
    if source:
        argv[argv.index("--nosource")] = "--source"

    assert _flag(discovery.resolve_command(argv), "--arch") == expected


def test_source_only_archive_requires_source_option(tmp_path: Path) -> None:
    _write_release(tmp_path, "bookworm", _release(architectures="source"))

    with pytest.raises(discovery.DiscoveryError, match="options.source"):
        discovery.resolve_command(_argv(tmp_path, "--dist", "bookworm", "--section", "main"))


def test_automatic_dist_skips_only_missing_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(discovery, "_list_distributions", lambda options: ("missing", "bookworm"))
    monkeypatch.setattr(
        discovery,
        "_read_release",
        lambda options, dist: (
            _release() if dist == "bookworm" else (_ for _ in ()).throw(discovery.ResourceMissing("missing"))
        ),
    )

    result = discovery.resolve_command(_argv(tmp_path))

    assert _flag(result, "--dist") == "bookworm"


def test_signature_failure_is_not_treated_as_missing_distribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(discovery, "_list_distributions", lambda options: ("bookworm",))
    monkeypatch.setattr(
        discovery, "_read_release", MagicMock(side_effect=discovery.DiscoveryError("signature failed"))
    )

    with pytest.raises(discovery.DiscoveryError, match="signature failed"):
        discovery.resolve_command(_argv(tmp_path))


def test_inrelease_falls_back_only_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    options = discovery._parse_native_options(
        ["debmirror", "--method", "https", "--host", "repo.example", "--root", "debian",
         "--no-check-gpg", "--nosource", "/mirror"]
    )
    fetch = MagicMock(side_effect=[discovery.ResourceMissing("missing"), b"release"])
    monkeypatch.setattr(discovery, "_fetch", fetch)

    assert discovery._read_release(options, "bookworm") == b"release"
    assert [call.args[1] for call in fetch.call_args_list] == [
        "dists/bookworm/InRelease", "dists/bookworm/Release"
    ]


def test_malformed_inrelease_does_not_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    options = discovery._parse_native_options(
        ["debmirror", "--method", "https", "--host", "repo.example", "--root", "debian",
         "--no-check-gpg", "--nosource", "/mirror"]
    )
    fetch = MagicMock(return_value=b"not signed")
    monkeypatch.setattr(discovery, "_fetch", fetch)

    with pytest.raises(discovery.DiscoveryError, match="clearsigned"):
        discovery._read_release(options, "bookworm")
    fetch.assert_called_once()


def test_missing_release_signature_fails_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    options = discovery._parse_native_options(
        ["debmirror", "--method", "https", "--host", "repo.example", "--root", "debian",
         "--check-gpg", "--nosource", "/mirror"]
    )
    fetch = MagicMock(
        side_effect=[discovery.ResourceMissing("no InRelease"), b"release", discovery.ResourceMissing("no sig")]
    )
    monkeypatch.setattr(discovery, "_fetch", fetch)

    with pytest.raises(discovery.DiscoveryError, match="Release.gpg"):
        discovery._read_release(options, "bookworm")


def test_ignore_release_gpg_uses_metadata_after_failed_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = discovery._parse_native_options(
        ["debmirror", "--method", "https", "--host", "repo.example", "--root", "debian",
         "--check-gpg", "--ignore-release-gpg", "--nosource", "/mirror"]
    )
    signed = (
        b"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA256\n\n"
        + _release()
        + b"-----BEGIN PGP SIGNATURE-----\ninvalid\n"
    )
    monkeypatch.setattr(discovery, "_fetch", lambda options, relative: signed)
    monkeypatch.setattr(
        discovery, "_verify_gpg", MagicMock(side_effect=discovery.DiscoveryError("bad signature"))
    )

    assert discovery._read_release(options, "bookworm") == _release()


def test_alias_codename_collision_fails(tmp_path: Path) -> None:
    _write_release(tmp_path, "stable", _release(components="main"))
    _write_release(tmp_path, "bookworm", _release(components="main contrib"))

    with pytest.raises(discovery.DiscoveryError, match="conflicting Codename bookworm"):
        discovery.resolve_command(_argv(tmp_path))


def test_partial_listing_cannot_drop_existing_distribution(tmp_path: Path) -> None:
    _write_release(tmp_path, "bookworm", _release())
    (tmp_path / "mirror" / "dists" / "bookworm").mkdir(parents=True)
    (tmp_path / "mirror" / "dists" / "trixie").mkdir()

    with pytest.raises(discovery.DiscoveryError, match="options.dist explicitly"):
        discovery.resolve_command(_argv(tmp_path))


def test_shrink_guard_ignores_existing_suite_symlinks(tmp_path: Path) -> None:
    _write_release(tmp_path, "bookworm", _release())
    dists = tmp_path / "mirror" / "dists"
    (dists / "bookworm").mkdir(parents=True)
    (dists / "stable").symlink_to("bookworm", target_is_directory=True)

    assert _flag(discovery.resolve_command(_argv(tmp_path)), "--dist") == "bookworm"


def test_ubuntu_shrink_guard_uses_suite_as_native_directory(tmp_path: Path) -> None:
    ubuntu = (
        b"Origin: Ubuntu\nSuite: noble-updates\nCodename: noble\n"
        b"Components: main\nArchitectures: amd64 all\n"
    )
    _write_release(tmp_path, "noble-updates", ubuntu)
    (tmp_path / "mirror" / "dists" / "noble-updates").mkdir(parents=True)

    assert _flag(discovery.resolve_command(_argv(tmp_path)), "--dist") == "noble-updates"


@pytest.mark.parametrize("components", ["../main", "main/../contrib", "main//debug"])
def test_release_rejects_unsafe_component_paths(tmp_path: Path, components: str) -> None:
    _write_release(tmp_path, "bookworm", _release(components=components))

    with pytest.raises(discovery.DiscoveryError, match="invalid component"):
        discovery.resolve_command(_argv(tmp_path))


def test_release_rejects_reserved_none_architecture(tmp_path: Path) -> None:
    _write_release(tmp_path, "bookworm", _release(architectures="none"))

    with pytest.raises(discovery.DiscoveryError, match="reserved architecture none"):
        discovery.resolve_command(_argv(tmp_path))


def test_http_listing_accepts_only_same_origin_direct_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = discovery._parse_native_options(
        ["debmirror", "--method", "https", "--host", "repo.example", "--root", "debian",
         "--no-check-gpg", "--nosource", "/mirror"]
    )
    listing = b"""
        <a href="bookworm/">bookworm</a>
        <a href="bookworm/main/">nested</a>
        <a href="https://evil.example/debian/dists/trixie/">external</a>
        <a href="bullseye/?token=secret">query</a>
        <a href="../escape/">escape</a>
    """
    monkeypatch.setattr(discovery, "_http_open", lambda options, relative: listing)

    assert discovery._http_list(options) == ("bookworm",)


def test_https_redirect_handler_rejects_downgrade() -> None:
    handler = discovery._SafeRedirectHandler()
    request = discovery.urllib.request.Request("https://repo.example/debian/dists/")

    with pytest.raises(discovery.DiscoveryError, match="downgrade"):
        handler.redirect_request(
            request, MagicMock(), 302, "Found", MagicMock(),
            "http://repo.example/debian/dists/",
        )


def test_ftp_proxy_routes_listing_and_fetch_through_url_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = [
        "debmirror", "--method", "ftp", "--host", "repo.example", "--root", "debian",
        "--proxy", "http://proxy.example", "--no-check-gpg", "--nosource", str(tmp_path),
    ]
    options = discovery._parse_native_options(argv)
    url_open = MagicMock(return_value=b"payload")
    monkeypatch.setattr(discovery, "_http_open", url_open)

    assert discovery._fetch(options, "dists/bookworm/Release") == b"payload"
    url_open.assert_called_once_with(options, "dists/bookworm/Release")


@pytest.mark.parametrize(
    ("message", "error_type"),
    [("550 No such file", discovery.ResourceMissing), ("550 Permission denied", discovery.DiscoveryError)],
)
def test_ftp_fetch_distinguishes_missing_from_permission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    error_type: type[Exception],
) -> None:
    options = discovery._parse_native_options(
        ["debmirror", "--method", "ftp", "--host", "repo.example", "--root", "debian",
         "--no-check-gpg", "--nosource", str(tmp_path)]
    )
    ftp = MagicMock()
    ftp.retrbinary.side_effect = discovery.ftplib.error_perm(message)
    monkeypatch.setattr(discovery, "_ftp_connect", lambda options: ftp)

    with pytest.raises(error_type):
        discovery._ftp_fetch(options, "dists/bookworm/InRelease")


def test_http_fetch_distinguishes_missing_from_forbidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = discovery._parse_native_options(
        ["debmirror", "--method", "https", "--host", "repo.example", "--root", "debian",
         "--no-check-gpg", "--nosource", str(tmp_path)]
    )
    opener = MagicMock()
    monkeypatch.setattr(discovery.urllib.request, "build_opener", lambda *handlers: opener)
    opener.open.side_effect = urllib.error.HTTPError("redacted", 404, "missing", {}, None)
    with pytest.raises(discovery.ResourceMissing):
        discovery._http_open(options, "dists/bookworm/InRelease")
    opener.open.side_effect = urllib.error.HTTPError("redacted", 403, "forbidden", {}, None)
    with pytest.raises(discovery.DiscoveryError, match="status 403"):
        discovery._http_open(options, "dists/bookworm/InRelease")


def test_rsync_fetch_requires_successful_parent_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = discovery._parse_native_options(
        ["debmirror", "--method", "rsync", "--host", "repo.example", "--root", "debian",
         "--no-check-gpg", "--nosource", str(tmp_path)]
    )
    monkeypatch.setattr(discovery, "_rsync_listing", lambda options, relative: b"")
    with pytest.raises(discovery.ResourceMissing):
        discovery._rsync_fetch(options, "dists/bookworm/InRelease")
    monkeypatch.setattr(
        discovery, "_rsync_listing", MagicMock(side_effect=discovery.DiscoveryError("permission"))
    )
    with pytest.raises(discovery.DiscoveryError, match="permission"):
        discovery._rsync_fetch(options, "dists/bookworm/InRelease")


def test_subprocess_file_output_is_limited() -> None:
    command = [
        sys.executable,
        "-c",
        f"import sys; sys.stdout.buffer.write(b'x' * ({discovery._MAX_METADATA_SIZE} + 1))",
    ]
    with tempfile.TemporaryFile() as output:
        result = discovery._run_process(command, 10, output)
        assert result.returncode != 0
        assert output.tell() <= discovery._MAX_METADATA_SIZE


def test_distribution_count_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    options = discovery._parse_native_options(_argv(tmp_path))
    monkeypatch.setattr(
        discovery,
        "_file_list",
        lambda options: tuple(f"dist{index}" for index in range(discovery._MAX_DISTRIBUTIONS + 1)),
    )

    with pytest.raises(discovery.DiscoveryError, match="too many distributions"):
        discovery._list_distributions(options)


def test_local_listing_does_not_follow_symlink_outside_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    dists = tmp_path / "repo" / "dists"
    dists.mkdir(parents=True)
    (dists / "escape").symlink_to(outside, target_is_directory=True)
    options = discovery._parse_native_options(_argv(tmp_path / "repo"))

    assert discovery._file_list(options) == ()


def test_gpgv_uses_explicit_keyrings_and_inherited_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = discovery._parse_native_options(
        ["debmirror", "--method", "file", "--root", str(tmp_path), "--check-gpg",
         "--keyring", "/keys/one.gpg", "--keyring=/keys/two.gpg", "--nosource", "/mirror"]
    )
    captured = {}

    def fake_run(command: list[str], timeout: int, stdout: int | None = subprocess.PIPE):
        captured["command"] = command
        Path(command[command.index("--output") + 1]).write_bytes(b"verified")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(discovery, "_run_process", fake_run)
    monkeypatch.setenv("GNUPGHOME", str(tmp_path / "gnupg"))

    assert discovery._verify_gpg(options, b"signed") == b"verified"
    assert captured["command"][:5] == [
        "gpgv", "--keyring", "/keys/one.gpg", "--keyring", "/keys/two.gpg"
    ]
    assert os.environ["GNUPGHOME"] == str(tmp_path / "gnupg")


def test_main_runs_resolved_native_command(monkeypatch: pytest.MonkeyPatch) -> None:
    native = ["debmirror", "--method", "file", "--root", "/repo", "--dist", "bookworm",
              "--section", "main", "--arch", "amd64", "/mirror"]
    resolved = [*native[:-1], "--nosource", native[-1]]
    monkeypatch.setattr(discovery.sys, "argv", ["debmirror", "--", *native])
    monkeypatch.setattr(discovery.signal, "signal", MagicMock())
    monkeypatch.setattr(discovery, "resolve_command", MagicMock(return_value=resolved))
    run_native = MagicMock(return_value=7)
    monkeypatch.setattr(discovery, "_run_native", run_native)

    with pytest.raises(SystemExit) as stopped:
        discovery.main()

    assert stopped.value.code == 7
    run_native.assert_called_once_with(resolved)


@pytest.mark.parametrize("returncode", [0, 7])
def test_native_run_isolates_config_and_removes_it_after_exit(
    monkeypatch: pytest.MonkeyPatch, returncode: int,
) -> None:
    paths = []

    def start(command: list[str]) -> MagicMock:
        config = Path(_flag(command, "--config-file"))
        assert config.read_text() == "1;\n"
        assert config.parent.stat().st_mode & 0o777 == 0o700
        assert command[-1] == "/mirror"
        paths.append(config.parent)
        return MagicMock(wait=MagicMock(return_value=returncode))

    monkeypatch.setattr(discovery.subprocess, "Popen", start)
    assert discovery._run_native(["debmirror", "--nosource", "/mirror"]) == returncode
    assert paths and not paths[0].exists()
    assert discovery._ACTIVE_PROCESS is None


def test_native_start_failure_removes_temporary_config(monkeypatch: pytest.MonkeyPatch) -> None:
    paths = []

    def fail(command: list[str]) -> None:
        paths.append(Path(_flag(command, "--config-file")).parent)
        raise OSError("cannot execute")

    monkeypatch.setattr(discovery.subprocess, "Popen", fail)
    with pytest.raises(discovery.DiscoveryError, match="could not be started"):
        discovery._run_native(["debmirror", "/mirror"])
    assert paths and not paths[0].exists()


def test_native_interrupt_reaps_child_and_removes_config(monkeypatch: pytest.MonkeyPatch) -> None:
    paths = []
    child = MagicMock()

    def interrupt() -> None:
        discovery._terminate_child(15, None)

    def start(command: list[str]) -> MagicMock:
        paths.append(Path(_flag(command, "--config-file")).parent)
        child.wait.side_effect = lambda **kwargs: 0 if kwargs else interrupt()
        return child

    monkeypatch.setattr(discovery.subprocess, "Popen", start)
    with pytest.raises(SystemExit) as stopped:
        discovery._run_native(["debmirror", "/mirror"])
    assert stopped.value.code == 143
    child.terminate.assert_called_once_with()
    assert paths and not paths[0].exists()
    assert discovery._ACTIVE_PROCESS is None


def test_sigterm_handler_reaps_active_metadata_child(monkeypatch: pytest.MonkeyPatch) -> None:
    child = MagicMock()
    child.wait.return_value = 0
    monkeypatch.setattr(discovery, "_ACTIVE_PROCESS", child)

    with pytest.raises(SystemExit) as stopped:
        discovery._terminate_child(15, None)

    assert stopped.value.code == 143
    child.terminate.assert_called_once_with()
    child.wait.assert_called_once_with(timeout=5)


def test_listing_error_requests_explicit_dist(tmp_path: Path) -> None:
    with pytest.raises(discovery.DiscoveryError, match="options.dist explicitly"):
        discovery.resolve_command(_argv(tmp_path))


def test_release_accepts_many_folded_values_and_rejects_duplicate_headers() -> None:
    metadata = (
        b"Codename: bookworm\nComponents: main\n"
        + b" contrib\n" * 50_000
        + b"Architectures: amd64\n arm64\nSHA256:\n ignored checksum entry\n"
    )
    fields = discovery._parse_fields(metadata)
    assert fields["Components"].split() == ["main", *(["contrib"] * 50_000)]
    assert fields["Architectures"] == "amd64 arm64"
    assert "SHA256" not in fields
    with pytest.raises(discovery.DiscoveryError, match="duplicate Components"):
        discovery._parse_fields(metadata + b"Components: extras\n")
