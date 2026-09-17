import ftplib
import io
import time
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mirror.sync import apt_mirror2


def request(**updates: object) -> apt_mirror2.RepositoryRequest:
    values = {
        "src": "https://repo.example/",
        "dst": "repo",
        "dist": None,
        "section": None,
        "arch": None,
        "source": False,
        "check_gpg": False,
        "keyrings": (),
        "deadline": time.monotonic() + 300,
    }
    values.update(updates)
    return apt_mirror2.RepositoryRequest(**values)


def release(
    components: str = "main contrib",
    architectures: str = "amd64 arm64 all",
    indexes: tuple[str, ...] = (),
) -> bytes:
    hashes = "".join(f" deadbeef 1 {path}\n" for path in indexes)
    return (
        f"Components: {components}\n"
        f"Architectures: {architectures}\n"
        f"SHA256:\n{hashes}"
    ).encode()


def test_parse_release_handles_folded_hash_fields() -> None:
    metadata = apt_mirror2._parse_release(
        "bookworm", release(indexes=("main/binary-amd64/Packages.xz",))
    )
    assert metadata.components == ("main", "contrib")
    assert metadata.architectures == ("amd64", "arm64", "all")
    assert metadata.index_paths == ("main/binary-amd64/Packages.xz",)


def test_parse_release_handles_folded_selection_fields() -> None:
    metadata = apt_mirror2._parse_release(
        "bookworm",
        b"Components: main\n contrib non-free\nArchitectures: amd64\n arm64 all\n",
    )

    assert metadata.components == ("main", "contrib", "non-free")
    assert metadata.architectures == ("amd64", "arm64", "all")


def test_resolve_normal_repository_discovers_all_dimensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = {
        "src": "https://repo.example/",
        "dst": "debian",
        "source": False,
        "check_gpg": False,
        "keyring": [],
    }
    monkeypatch.setattr(
        apt_mirror2,
        "_read_release",
        lambda req, prefix: (_ for _ in ()).throw(apt_mirror2.ResourceMissing("missing"))
        if not prefix
        else release(
            components="main" if prefix.endswith("bookworm") else "main partner",
            architectures="amd64" if prefix.endswith("bookworm") else "arm64 all",
        ),
    )
    monkeypatch.setattr(apt_mirror2, "_list_distributions", lambda req: ("bookworm", "trixie"))

    resolved = apt_mirror2.resolve_repository(raw, tmp_path)

    assert resolved.dist == ("bookworm", "trixie")
    assert resolved.section == ("main", "partner")
    assert resolved.arch == ("all", "amd64", "arm64")
    assert resolved.binaries is True
    assert resolved.flat is False


def test_auto_flat_detection_uses_direct_package_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = {
        "src": "https://repo.example/flat/",
        "dst": "flat",
        "source": False,
        "check_gpg": False,
        "keyring": [],
    }
    monkeypatch.setattr(
        apt_mirror2,
        "_read_release",
        lambda req, prefix: release(indexes=("Packages.gz",)),
    )
    listing = MagicMock(side_effect=AssertionError("dists listing must not be used"))
    monkeypatch.setattr(apt_mirror2, "_list_distributions", listing)

    resolved = apt_mirror2.resolve_repository(raw, tmp_path)

    assert resolved.flat is True
    assert resolved.dist == ("./",)
    assert resolved.arch == ("all",)
    listing.assert_not_called()


def test_explicit_flat_requires_release_before_native(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = {
        "src": "https://repo.example/flat/",
        "dst": "flat",
        "dist": ["./"],
        "source": False,
        "check_gpg": False,
        "keyring": [],
    }
    monkeypatch.setattr(
        apt_mirror2,
        "_read_release",
        lambda req, prefix: (_ for _ in ()).throw(apt_mirror2.ResourceMissing("missing")),
    )

    with pytest.raises(apt_mirror2.AptMirrorError, match="v16 requires Release"):
        apt_mirror2.resolve_repository(raw, tmp_path)


def test_auto_flat_source_only_emits_no_binary_arch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = {
        "src": "https://repo.example/flat/",
        "dst": "flat",
        "source": True,
        "check_gpg": False,
        "keyring": [],
    }
    monkeypatch.setattr(
        apt_mirror2,
        "_read_release",
        lambda req, prefix: release(architectures="", indexes=("Sources.xz",)),
    )

    resolved = apt_mirror2.resolve_repository(raw, tmp_path)

    assert resolved.source is True
    assert resolved.binaries is False
    assert resolved.arch == ()


@pytest.mark.parametrize("field", ["section", "arch"])
def test_flat_rejects_section_and_arch(
    field: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = {
        "src": "https://repo.example/flat/",
        "dst": "flat",
        "dist": ["./"],
        field: ["main" if field == "section" else "amd64"],
        "source": False,
        "check_gpg": False,
        "keyring": [],
    }
    monkeypatch.setattr(
        apt_mirror2,
        "_read_release",
        lambda req, prefix: release(indexes=("Packages.gz",)),
    )
    with pytest.raises(apt_mirror2.AptMirrorError, match="do not accept"):
        apt_mirror2.resolve_repository(raw, tmp_path)


def test_auto_discovery_refuses_distribution_shrink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "debian/dists/bookworm").mkdir(parents=True)
    raw = {
        "src": "https://repo.example/",
        "dst": "debian",
        "source": False,
        "check_gpg": False,
        "keyring": [],
    }
    monkeypatch.setattr(
        apt_mirror2,
        "_read_release",
        lambda req, prefix: (_ for _ in ()).throw(apt_mirror2.ResourceMissing("missing"))
        if not prefix
        else release(),
    )
    monkeypatch.setattr(apt_mirror2, "_list_distributions", lambda req: ("trixie",))

    with pytest.raises(apt_mirror2.AptMirrorError, match="bookworm"):
        apt_mirror2.resolve_repository(raw, tmp_path)


def test_auto_discovery_rejects_unsafe_release_selections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = {
        "src": "https://repo.example/",
        "dst": "debian",
        "dist": ["bookworm"],
        "source": False,
        "check_gpg": False,
        "keyring": [],
    }
    monkeypatch.setattr(
        apt_mirror2,
        "_read_release",
        lambda req, prefix: release(components="main/../../outside"),
    )
    with pytest.raises(apt_mirror2.AptMirrorError, match="unsafe Components"):
        apt_mirror2.resolve_repository(raw, tmp_path)


def test_ftp_normal_layout_skips_absent_root_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = {
        "src": "ftp://repo.example/debian/",
        "dst": "debian",
        "source": False,
        "check_gpg": False,
        "keyring": [],
    }
    monkeypatch.setattr(apt_mirror2, "_ftp_root_has_release", lambda req: False)
    monkeypatch.setattr(apt_mirror2, "_list_distributions", lambda req: ("bookworm",))
    read = MagicMock(return_value=release(components="main", architectures="amd64"))
    monkeypatch.setattr(apt_mirror2, "_read_release", read)

    resolved = apt_mirror2.resolve_repository(raw, tmp_path)

    assert resolved.dist == ("bookworm",)
    read.assert_called_once()
    assert read.call_args.args[1] == "dists/bookworm"


def test_read_release_falls_back_only_when_inrelease_is_missing(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    req = request()
    calls: list[str] = []

    def fetch(_request: object, relative: str) -> bytes:
        calls.append(relative)
        if relative.endswith("InRelease"):
            raise apt_mirror2.ResourceMissing("missing")
        return b"release"

    monkeypatch.setattr(apt_mirror2, "_fetch", fetch)

    assert apt_mirror2._read_release(req, "dists/bookworm") == b"release"
    assert calls == ["dists/bookworm/InRelease", "dists/bookworm/Release"]


def test_bad_inrelease_does_not_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    req = request()
    fetch = MagicMock(return_value=b"invalid")
    monkeypatch.setattr(apt_mirror2, "_fetch", fetch)

    with pytest.raises(apt_mirror2.AptMirrorError, match="clearsigned"):
        apt_mirror2._read_release(req, "dists/bookworm")

    fetch.assert_called_once()


def test_missing_release_signature_is_not_treated_as_missing_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = request(check_gpg=True, keyrings=("/keys/repository.gpg",))

    def fetch(_request: object, relative: str) -> bytes:
        if relative.endswith("InRelease") or relative.endswith("Release.gpg"):
            raise apt_mirror2.ResourceMissing("missing")
        return release()

    monkeypatch.setattr(apt_mirror2, "_fetch", fetch)

    with pytest.raises(apt_mirror2.AptMirrorError, match="missing Release.gpg"):
        apt_mirror2._read_release(req, "dists/bookworm")


def test_http_missing_status_and_https_downgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    req = request()

    class MissingOpener:
        def open(self, url: str, timeout: float) -> object:
            raise urllib.error.HTTPError(url, 404, "missing", {}, None)

    monkeypatch.setattr(apt_mirror2.urllib.request, "build_opener", lambda handler: MissingOpener())
    with pytest.raises(apt_mirror2.ResourceMissing):
        apt_mirror2._http_fetch(req, "InRelease")

    handler = apt_mirror2._SafeRedirectHandler()
    original = urllib.request.Request("https://repo.example/InRelease")
    with pytest.raises(apt_mirror2.AptMirrorError, match="downgrade"):
        handler.redirect_request(original, None, 302, "", {}, "http://repo.example/InRelease")


def test_bounded_read_checks_size_and_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    req = request()
    monkeypatch.setattr(apt_mirror2, "_MAX_METADATA_SIZE", 4)
    with pytest.raises(apt_mirror2.AptMirrorError, match="16 MiB"):
        apt_mirror2._bounded_read(io.BytesIO(b"12345"), req)

    expired = request(deadline=time.monotonic() - 1)
    with pytest.raises(apt_mirror2.AptMirrorError, match="300 seconds"):
        apt_mirror2._bounded_read(io.BytesIO(b"1"), expired)


def test_bounded_read_uses_available_chunks() -> None:
    class StreamingResponse:
        chunks = iter((b"one", b"two", b""))

        def read(self, size: int) -> bytes:
            raise AssertionError("blocking read must not be used")

        def read1(self, size: int) -> bytes:
            return next(self.chunks)

    assert apt_mirror2._bounded_read(StreamingResponse(), request()) == b"onetwo"


def test_ftp_550_is_missing_only_for_inrelease(monkeypatch: pytest.MonkeyPatch) -> None:
    ftp = MagicMock()
    ftp.retrbinary.side_effect = ftplib.error_perm("550 not found")
    monkeypatch.setattr(apt_mirror2, "_ftp_connection", lambda req: ftp)
    req = request(src="ftp://repo.example/")

    with pytest.raises(apt_mirror2.ResourceMissing):
        apt_mirror2._ftp_fetch(req, "dists/bookworm/InRelease")
    with pytest.raises(apt_mirror2.AptMirrorError, match="FTP request failed"):
        apt_mirror2._ftp_fetch(req, "dists/bookworm/Release")


def test_ftp_listing_streams_with_total_entry_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ftp = MagicMock()
    ftp.encoding = "utf-8"
    connection = MagicMock()
    connection.recv.side_effect = [
        b"type=file; item-0\ntype=file; item-1\n",
        b"type=file; item-2\ntype=file; item-3\n",
    ]
    ftp.transfercmd.return_value = connection
    monkeypatch.setattr(apt_mirror2, "_MAX_LISTING_LINKS", 3)

    with pytest.raises(apt_mirror2.AptMirrorError, match="too many entries"):
        apt_mirror2._stream_ftp_listing(
            ftp,
            "/repository",
            request(src="ftp://repo.example/"),
            lambda name, facts: None,
        )

    ftp.transfercmd.assert_called_once_with("MLSD /repository")
    assert connection.recv.call_count == 2


def test_ftp_listing_checks_deadline_between_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ftp = MagicMock()
    ftp.encoding = "utf-8"
    connection = MagicMock()
    connection.recv.return_value = b"bytes-without-a-newline"
    ftp.transfercmd.return_value = connection
    req = request(src="ftp://repo.example/", deadline=10)
    times = iter((0, 0, 11))
    monkeypatch.setattr(apt_mirror2.time, "monotonic", lambda: next(times))

    with pytest.raises(apt_mirror2.AptMirrorError, match="300 seconds"):
        apt_mirror2._receive_ftp_listing(
            ftp,
            "MLSD /repository",
            req,
            lambda line: None,
        )

    connection.close.assert_called_once()


def test_gpg_uses_only_item_keyrings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "first.gpg"
    second = tmp_path / "second.gpg"
    first.write_bytes(b"key")
    second.write_bytes(b"key")
    req = request(check_gpg=True, keyrings=(str(first), str(second)))
    commands: list[list[str]] = []
    monkeypatch.setattr(apt_mirror2, "_run_gpg", lambda command, request: commands.append(command))

    result = apt_mirror2._verify_release(req, b"release", b"signature")

    assert result == b"release"
    command = commands[0]
    assert command[:5] == ["gpgv", "--keyring", str(first), "--keyring", str(second)]
