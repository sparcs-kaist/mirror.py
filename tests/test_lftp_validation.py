from unittest.mock import MagicMock

import pytest

import mirror
import mirror.sync.lftp as lftp


def _package(src: str, dst: str = "/tmp/mirror", options: dict | None = None):
    package = MagicMock()
    package.pkgid = "pkg"
    package.name = "Pkg"
    package.settings.src = src
    package.settings.dst = dst
    package.settings.options = options if options is not None else {}
    return package


def _logger(tmp_path):
    logger = MagicMock()
    handler = MagicMock()
    handler.baseFilename = str(tmp_path / "pkg.log")
    logger.handlers = [handler]
    return logger


def test_lftp_valid_source_delegates_to_worker(tmp_path, monkeypatch):
    calls = []
    conf = MagicMock()
    conf.uid = 1234
    conf.gid = 5678
    monkeypatch.setattr(mirror, "conf", conf, raising=False)
    monkeypatch.setattr("mirror.socket.worker.execute_command", lambda **kwargs: calls.append(kwargs))

    lftp.execute(_package("ftp://example.org/debian"), _logger(tmp_path))

    assert len(calls) == 1
    assert calls[0]["commandline"][0] == "lftp"
    assert calls[0]["uid"] == 1234
    assert calls[0]["gid"] == 5678
    script = calls[0]["commandline"][2]
    assert "set ftp:anon-pass mirror@example.org;" in script
    assert "set net:max-retries 3;" in script
    assert "set net:timeout 60;" in script
    assert "-X '\\.(mirror|notar)' -x '\\.in\\..*\\.' -X 'lost+found'" in script
    assert "ftp://example.org/debian /tmp/mirror" in script
    assert "--scan-all-first" not in script
    assert "set list-options" not in script


@pytest.mark.parametrize(
    "src",
    [
        "ftp://example.org",
        "ftp://example.org/",
        "ftp://example.org/debian/",
        "ftp://example.org:21/debian",
        "ftp://ftp.isc.org/isc/.",
        "ftp://lftp-fixture/data",
    ],
)
def test_lftp_accepts_valid_ftp_sources(src, tmp_path, monkeypatch):
    calls = []
    conf = MagicMock()
    conf.uid = 1234
    conf.gid = 5678
    monkeypatch.setattr(mirror, "conf", conf, raising=False)
    monkeypatch.setattr("mirror.socket.worker.execute_command", lambda **kwargs: calls.append(kwargs))

    lftp.execute(_package(src), _logger(tmp_path))

    assert len(calls) == 1
    assert src in calls[0]["commandline"][2]


@pytest.mark.parametrize(
    "src",
    [
        "example.org/debian",
        "/srv/source/debian",
        "srv/source/debian",
        "rsync://example.org/debian",
        "http://example.org/debian",
        "sftp://example.org/debian",
        "ftps://example.org/debian",
        "ftp://user@example.org/debian",
        "ftp:///debian",
        "ftp://example.org/debian?bad=1",
        "ftp://example.org/debian#bad",
        "ftp://example.org:bad/debian",
        "ftp://example.org:70000/debian",
        "ftp://example.org/debian%20bad",
        "ftp://exa_mple.org/debian",
        "ftp://exämple.org/debian",
        "ftp://example..org/debian",
        "ftp://-example.org/debian",
        "ftp://example-.org/debian",
        f"ftp://{'a' * 64}.org/debian",
        "ftp://example.org/deb ian",
        "ftp://example.org/debian;bad",
        "ftp://example.org/`touch`",
        "ftp://example.org/de$bian",
        "ftp://example.org/de|bian",
        "ftp://example.org/de&bian",
        "ftp://example.org/de\\bian",
        "ftp://example.org/de*bian",
        "ftp://example.org/debian#comment",
        "ftp://example.org/../debian",
        "ftp://example.org/debian//pool",
        "ftp://example.org/debian/./pool",
    ],
)
def test_lftp_rejects_unsafe_source_before_worker_rpc(src, tmp_path, monkeypatch):
    calls = []
    done = []
    monkeypatch.setattr("mirror.socket.worker.execute_command", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr("mirror.sync.on_sync_done", lambda pkgid, success, returncode: done.append(success))

    lftp.execute(_package(src), _logger(tmp_path))

    assert calls == []
    assert done == [False]


@pytest.mark.parametrize(
    "dst",
    [
        "/tmp/mirror\x00bad",
        "/tmp/mirror bad",
        "-bad",
        "/tmp/mirror;bad",
        "/tmp/mirror`bad`",
        "/tmp/mirror$bad",
        "/tmp/mirror|bad",
        "/tmp/mirror>bad",
    ],
)
def test_lftp_rejects_unsafe_destination_before_worker_rpc(dst, tmp_path, monkeypatch):
    calls = []
    done = []
    monkeypatch.setattr("mirror.socket.worker.execute_command", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr("mirror.sync.on_sync_done", lambda pkgid, success, returncode: done.append(success))

    lftp.execute(_package("ftp://example.org/debian", dst=dst), _logger(tmp_path))

    assert calls == []
    assert done == [False]


def test_lftp_isc_like_options_are_rendered(tmp_path, monkeypatch):
    calls = []
    conf = MagicMock()
    conf.uid = 1234
    conf.gid = 5678
    monkeypatch.setattr(mirror, "conf", conf, raising=False)
    monkeypatch.setattr("mirror.socket.worker.execute_command", lambda **kwargs: calls.append(kwargs))

    options = {
        "list_options": "-a",
        "scan_all_first": True,
        "exclude_X": ["custom-X"],
        "exclude_x": ["custom-x"],
        "exclude": [
            "bind4/src/DEPRECATED/4.9.5/contrib/old",
            "RCS",
            r"MIRRORS\,v",
            r".*\.sigs\.shar",
        ],
        "max_retries": 4,
        "net_timeout": 120,
    }

    lftp.execute(_package("ftp://ftp.isc.org/isc/.", options=options), _logger(tmp_path))

    assert len(calls) == 1
    script = calls[0]["commandline"][2]
    assert "set ftp:anon-pass mirror@ftp.isc.org;" in script
    assert "set list-options -a;" in script
    assert "set net:max-retries 4;" in script
    assert "set net:timeout 120;" in script
    assert "--scan-all-first" in script
    assert "-X 'custom-X' -x 'custom-x'" in script
    assert "-X '\\.(mirror|notar)'" not in script
    assert "-x '\\.in\\..*\\.'" not in script
    assert "-X 'lost+found'" not in script
    assert "--exclude='bind4/src/DEPRECATED/4.9.5/contrib/old'" in script
    assert "--exclude='RCS'" in script
    assert r"--exclude='MIRRORS\,v'" in script
    assert r"--exclude='.*\.sigs\.shar'" in script
    assert "ftp://ftp.isc.org/isc/. /tmp/mirror" in script


def test_lftp_long_exclude_preserves_default_excludes(tmp_path, monkeypatch):
    calls = []
    conf = MagicMock()
    conf.uid = 1234
    conf.gid = 5678
    monkeypatch.setattr(mirror, "conf", conf, raising=False)
    monkeypatch.setattr("mirror.socket.worker.execute_command", lambda **kwargs: calls.append(kwargs))

    lftp.execute(_package("ftp://example.org/debian", options={"exclude": ["RCS"]}), _logger(tmp_path))

    script = calls[0]["commandline"][2]
    assert "-X '\\.(mirror|notar)' -x '\\.in\\..*\\.' -X 'lost+found'" in script
    assert "--exclude='RCS'" in script


def test_lftp_partial_exclude_override_keeps_other_default_group(tmp_path, monkeypatch):
    calls = []
    conf = MagicMock()
    conf.uid = 1234
    conf.gid = 5678
    monkeypatch.setattr(mirror, "conf", conf, raising=False)
    monkeypatch.setattr("mirror.socket.worker.execute_command", lambda **kwargs: calls.append(kwargs))

    lftp.execute(_package("ftp://example.org/debian", options={"exclude_X": ["custom-X"]}), _logger(tmp_path))

    script = calls[0]["commandline"][2]
    assert "-X 'custom-X'" in script
    assert "-x '\\.in\\..*\\.'" in script
    assert "-X '\\.(mirror|notar)'" not in script
    assert "-X 'lost+found'" not in script


@pytest.mark.parametrize(
    "options",
    [
        {"list_options": "-l"},
        {"list_options": ""},
        {"scan_all_first": "true"},
        {"exclude_x": "RCS"},
        {"exclude_x": None},
        {"exclude_X": "RCS"},
        {"exclude_X": [1]},
        {"exclude": [""]},
        {"exclude": ["bad;value"]},
        {"exclude": ["bad'value"]},
        {"exclude": ["bad\x00value"]},
        {"exclude": ["bad\nvalue"]},
        {"max_retries": True},
        {"max_retries": -1},
        {"max_retries": 0},
        {"max_retries": 101},
        {"net_timeout": False},
        {"net_timeout": -1},
        {"net_timeout": 0},
        {"net_timeout": 3601},
        [],
        "not-a-dict",
        0,
    ],
)
def test_lftp_rejects_invalid_options_before_worker_rpc(options, tmp_path, monkeypatch):
    calls = []
    done = []
    monkeypatch.setattr("mirror.socket.worker.execute_command", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr("mirror.sync.on_sync_done", lambda pkgid, success, returncode: done.append(success))

    lftp.execute(_package("ftp://example.org/debian", options=options), _logger(tmp_path))

    assert calls == []
    assert done == [False]


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"max_retries": 1}, "set net:max-retries 1;"),
        ({"max_retries": 100}, "set net:max-retries 100;"),
        ({"net_timeout": 1}, "set net:timeout 1;"),
        ({"net_timeout": 3600}, "set net:timeout 3600;"),
    ],
)
def test_lftp_accepts_option_boundaries(options, expected, tmp_path, monkeypatch):
    calls = []
    conf = MagicMock()
    conf.uid = 1234
    conf.gid = 5678
    monkeypatch.setattr(mirror, "conf", conf, raising=False)
    monkeypatch.setattr("mirror.socket.worker.execute_command", lambda **kwargs: calls.append(kwargs))

    lftp.execute(_package("ftp://example.org/debian", options=options), _logger(tmp_path))

    assert len(calls) == 1
    assert expected in calls[0]["commandline"][2]
