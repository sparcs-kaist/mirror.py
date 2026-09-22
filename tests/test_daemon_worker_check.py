import importlib
import signal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import mirror
from mirror.config.reload_controller import reload_controller


daemon_mod = importlib.import_module("mirror.command.daemon")


@pytest.fixture
def daemon_runtime(monkeypatch, tmp_path):
    """Isolate daemon globals and stop its loop through the installed handler."""
    log = MagicMock()
    socket_server = MagicMock(socket_path=tmp_path / "master.sock")
    signal_handlers = {}

    monkeypatch.setattr(mirror, "RUN_PATH", tmp_path)
    monkeypatch.setattr(mirror, "packages", {}, raising=False)
    monkeypatch.setattr(
        mirror, "conf", SimpleNamespace(errorcontinuetime=60), raising=False
    )
    monkeypatch.setattr(mirror, "log", log, raising=False)
    monkeypatch.setattr(mirror.config, "load", MagicMock())
    monkeypatch.setattr(mirror.logger, "setup_logger", MagicMock())
    monkeypatch.setattr(mirror.event, "post_event", MagicMock())
    monkeypatch.setattr(mirror.socket, "init", MagicMock(return_value=socket_server))
    monkeypatch.setattr(mirror.socket, "stop", MagicMock())
    monkeypatch.setattr(
        reload_controller,
        "consume_pending",
        MagicMock(return_value=(False, [])),
    )
    monkeypatch.setattr(
        daemon_mod.signal,
        "signal",
        lambda sig, handler: signal_handlers.__setitem__(sig, handler),
    )

    sleep_calls = 0

    def stop_after_first_iteration(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        signal_handlers[signal.SIGTERM](signal.SIGTERM, None)

    monkeypatch.setattr(daemon_mod.time, "sleep", stop_after_first_iteration)
    daemon_mod._mismatch_first_seen.clear()

    yield SimpleNamespace(
        log=log,
        socket_server=socket_server,
        signal_handlers=signal_handlers,
        sleep_calls=lambda: sleep_calls,
    )

    daemon_mod._mismatch_first_seen.clear()


@pytest.mark.parametrize(
    ("worker_running", "level", "message"),
    [
        (True, "info", "Worker server is running and reachable."),
        (
            False,
            "error",
            "Worker server is NOT running. Sync operations may fail if they rely on it.",
        ),
    ],
)
def test_daemon_reports_worker_state_and_cleans_up(
    daemon_runtime, monkeypatch, worker_running, level, message
):
    monkeypatch.setattr(
        mirror.socket.worker,
        "is_worker_running",
        MagicMock(return_value=worker_running),
    )

    with pytest.raises(SystemExit) as exc_info:
        daemon_mod.daemon("dummy_config.json")

    assert exc_info.value.code == 0
    getattr(daemon_runtime.log, level).assert_any_call(message)
    assert daemon_runtime.sleep_calls() == 1
    daemon_runtime.socket_server.stop.assert_called_once_with()
    mirror.socket.stop.assert_called_once_with()
    assert not (mirror.RUN_PATH / "mirror.pid").exists()
    assert not (mirror.RUN_PATH / "master.sock.path").exists()


def test_daemon_loop_starts_due_package(daemon_runtime, monkeypatch):
    package = MagicMock(
        pkgid="test-pkg",
        lastsync=0,
        syncrate=10,
        status="ACTIVE",
    )
    package.is_disabled.return_value = False
    package.is_syncing.return_value = False
    mirror.packages = {package.pkgid: package}

    worker_running = MagicMock(side_effect=lambda *args: not bool(args))
    sync_start = MagicMock()
    monkeypatch.setattr(mirror.socket.worker, "is_worker_running", worker_running)
    monkeypatch.setattr(mirror.sync, "start", sync_start)

    with pytest.raises(SystemExit) as exc_info:
        daemon_mod.daemon("dummy_config.json")

    assert exc_info.value.code == 0
    sync_start.assert_called_once_with(package)
    daemon_runtime.log.info.assert_any_call(
        "Package test-pkg requires sync (last_sync=0, syncrate=10, status=ACTIVE)"
    )
    daemon_runtime.socket_server.stop.assert_called_once_with()
