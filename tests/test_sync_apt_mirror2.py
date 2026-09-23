import json
import logging
import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import mirror
import mirror.socket.worker
import mirror.structure
import mirror.sync
import mirror.sync.apt_mirror2


def make_package(options: dict, dst: str = "/srv/mirror/cuda") -> MagicMock:
    package = MagicMock(spec=mirror.structure.Package)
    package.pkgid = "cuda"
    package.name = "CUDA"
    package.settings = MagicMock()
    package.settings.src = "https://developer.download.nvidia.com/compute/cuda/repos/"
    package.settings.dst = dst
    package.settings.options = options
    return package


def repository(**updates: object) -> dict:
    value = {
        "src": "https://repo.example/ubuntu2404/x86_64/",
        "dst": "ubuntu2404/x86_64",
        "dist": ["./"],
        "check_gpg": False,
    }
    value.update(updates)
    return value


def test_build_payload_supports_multiple_repositories_and_defaults() -> None:
    package = make_package(
        {
            "config": [
                repository(),
                repository(
                    src="https://repo.example/ubuntu2604/x86_64/",
                    dst="ubuntu2604/x86_64/",
                    keyring=["/keys/one.gpg", "/keys/two.gpg"],
                    check_gpg=True,
                    source=True,
                ),
            ]
        }
    )

    payload = mirror.sync.apt_mirror2.build_payload(package)

    assert payload["nthreads"] == 8
    assert payload["limit_rate"] is None
    configs = payload["repositories"]
    assert configs[0]["source"] is False
    assert configs[1]["source"] is True
    assert configs[1]["keyring"] == ["/keys/one.gpg", "/keys/two.gpg"]


@pytest.mark.parametrize(
    "options, message",
    [
        ({}, "non-empty list"),
        ({"config": []}, "non-empty list"),
        ({"config": [repository()], "nthreads": 0}, "positive integer"),
        ({"config": [repository()], "limit_rate": "1g"}, "optional k or m"),
        ({"config": [repository(check_gpg=True)]}, "keyring"),
        ({"config": [repository(dst="../escape")]}, "safe non-empty"),
        ({"config": [repository(src="file:///repo")]}, "scheme"),
        ({"config": [repository(src="https://user:secret@repo.example/")]}, "credentials"),
        ({"config": [repository(section=[])]}, "must not be empty"),
        ({"config": [repository(section=["main/../../outside"])]}, "traversal"),
        ({"config": [repository(arch=["amd64\n"])]}, "no controls"),
        ({"config": [repository(arch=["source"])]}, "source=true"),
        ({"config": [repository(keyring="/keys/one.gpg,/keys/two.gpg")]}, "delimiters"),
        ({"config": [repository(raw="value")]}, "unknown config"),
    ],
)
def test_build_payload_rejects_invalid_input(options: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        mirror.sync.apt_mirror2.build_payload(make_package(options))


def test_build_payload_rejects_duplicate_sources_and_overlapping_destinations() -> None:
    first = repository()
    duplicate = repository(dst="other")
    with pytest.raises(ValueError, match="src values must be unique"):
        mirror.sync.apt_mirror2.build_payload(make_package({"config": [first, duplicate]}))

    second = repository(
        src="https://repo2.example/",
        dst="ubuntu2404/x86_64/subdirectory",
    )
    with pytest.raises(ValueError, match="must not overlap"):
        mirror.sync.apt_mirror2.build_payload(make_package({"config": [first, second]}))


def test_execute_delegates_static_worker_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    package = make_package({"config": [repository()]})
    logger = MagicMock(spec=logging.Logger)
    logger.handlers = []
    execute_command = MagicMock()
    monkeypatch.setattr(mirror.sync.apt_mirror2.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(mirror.socket.worker, "execute_command", execute_command)
    monkeypatch.setattr(mirror.sync, "get_extra_args", lambda pkgid: {"TOKEN": "value"})
    monkeypatch.setattr(mirror.sync, "on_sync_done", MagicMock())
    monkeypatch.setattr(mirror, "conf", MagicMock(uid=123, gid=456), raising=False)

    mirror.sync.apt_mirror2.execute(package, logger)

    call = execute_command.call_args.kwargs
    assert call["commandline"][:4] == [
        sys.executable,
        "-c",
        "from mirror.sync.apt_mirror2 import main; main()",
        "--",
    ]
    assert json.loads(call["commandline"][4])["dst"] == "/srv/mirror/cuda"
    assert call["sync_method"] == "apt-mirror2"
    assert call["uid"] == 123
    assert call["gid"] == 456
    assert call["env"] == {"TOKEN": "value"}


def test_execute_reports_preflight_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    package = make_package({"config": [repository()]})
    logger = MagicMock(spec=logging.Logger)
    logger.handlers = []
    execute_command = MagicMock()
    on_done = MagicMock()
    monkeypatch.setattr(mirror.sync.apt_mirror2.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(mirror.socket.worker, "execute_command", execute_command)
    monkeypatch.setattr(mirror.sync, "on_sync_done", on_done)

    mirror.sync.apt_mirror2.execute(package, logger)

    execute_command.assert_not_called()
    on_done.assert_called_once_with("cuda", success=False, returncode=None)


def test_render_config_maps_destinations_gpg_cleanup_and_rate(tmp_path: Path) -> None:
    payload = {
        "dst": "/srv/mirror/cuda",
        "nthreads": 12,
        "limit_rate": "20m",
    }
    resolved = [
        mirror.sync.apt_mirror2.ResolvedRepository(
            src="https://repo.example/cuda/",
            dst="ubuntu2404/x86_64",
            dist=("./",),
            section=(),
            arch=("all",),
            source=False,
            binaries=True,
            flat=True,
            check_gpg=True,
            keyrings=("/keys/cuda.gpg",),
        )
    ]

    config = mirror.sync.apt_mirror2.render_config(payload, resolved, tmp_path)

    assert "set mirror_path /srv/mirror/cuda" in config
    assert "set nthreads 12" in config
    assert "set limit_rate 20m" in config
    assert "set slow_rate_protection off" in config
    assert "set _autoclean 1" in config
    assert "set wipe_size_ratio 0.4" in config
    assert "deb [arch=all signed-by=/keys/cuda.gpg] https://repo.example/cuda/ ./" in config
    assert "mirror_path https://repo.example/cuda/ ubuntu2404/x86_64" in config
    assert "gpg_verify https://repo.example/cuda/ force" in config
    assert "clean https://repo.example/cuda/" in config


def test_render_config_uses_unlimited_rate_and_source_only(tmp_path: Path) -> None:
    payload = {"dst": "/srv/mirror/source", "nthreads": 8, "limit_rate": None}
    repository = mirror.sync.apt_mirror2.ResolvedRepository(
        "https://repo.example/source/",
        "source",
        ("bookworm",),
        ("main",),
        (),
        True,
        False,
        False,
        False,
        (),
    )

    config = mirror.sync.apt_mirror2.render_config(payload, [repository], tmp_path)

    assert "set limit_rate 0" in config
    assert "slow_rate_protection" not in config
    assert "deb-src https://repo.example/source/ bookworm main" in config
    assert "\ndeb " not in config


def test_run_payload_cleans_private_config_on_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "dst": str(tmp_path),
        "nthreads": 8,
        "limit_rate": None,
        "repositories": [repository()],
    }
    resolved = mirror.sync.apt_mirror2.ResolvedRepository(
        "https://repo.example/", "repo", ("./",), (), ("all",), False, True, True, False, ()
    )
    monkeypatch.setattr(mirror.sync.apt_mirror2, "resolve_repository", lambda raw, dst: resolved)
    processes: list[MagicMock] = []

    def create_process(command: list[str]) -> MagicMock:
        process = MagicMock()
        process.wait.return_value = 7
        processes.append(process)
        config_path = Path(command[-1])
        assert config_path.is_file()
        assert config_path.stat().st_mode & 0o777 == 0o600
        assert config_path.parent.stat().st_mode & 0o777 == 0o700
        return process

    monkeypatch.setattr(mirror.sync.apt_mirror2.subprocess, "Popen", create_process)

    assert mirror.sync.apt_mirror2.run_payload(payload) == 7
    assert not list(tmp_path.glob(".mirror-apt-mirror2-*"))
    assert processes


def test_run_payload_rejects_existing_destination_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = tmp_path / "mirror"
    destination.mkdir()
    (destination / "linked").symlink_to(outside, target_is_directory=True)
    payload = {
        "dst": str(destination),
        "nthreads": 8,
        "limit_rate": None,
        "repositories": [repository(dst="linked/repo")],
    }
    native = MagicMock()
    monkeypatch.setattr(mirror.sync.apt_mirror2.subprocess, "Popen", native)

    with pytest.raises(mirror.sync.apt_mirror2.AptMirrorError, match="contains a symlink"):
        mirror.sync.apt_mirror2.run_payload(payload)

    native.assert_not_called()


def test_terminate_child_escalates_after_two_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    process = MagicMock()
    process.wait.side_effect = [mirror.sync.apt_mirror2.subprocess.TimeoutExpired("apt", 2), 0]
    monkeypatch.setattr(mirror.sync.apt_mirror2, "_ACTIVE_PROCESS", process)

    with pytest.raises(SystemExit, match="143"):
        mirror.sync.apt_mirror2._terminate_child(signal.SIGTERM, None)

    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert process.wait.call_args_list[0].kwargs == {"timeout": 2}


def test_plugin_exposes_apt_mirror2() -> None:
    record = mirror.sync.apt_mirror2.plugin()
    assert record.name == "apt-mirror2"
    assert record.execute is mirror.sync.apt_mirror2.execute
