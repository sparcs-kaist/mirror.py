from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import types
from unittest.mock import MagicMock, patch

import pytest

import mirror

@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """
    This autouse fixture runs once per session to set up the test environment.
    It modifies the default RUN_PATH and STATE_PATH to use a temporary
    test-specific directory, preventing tests from interfering with the
    actual production environment or each other.
    """
    # Define temporary paths within the project's test environment directory
    test_run_path = Path("test_env/run")
    test_state_path = Path("test_env/state")

    # Clean up old test directories if they exist
    if test_run_path.exists():
        shutil.rmtree(test_run_path)
    if test_state_path.exists():
        shutil.rmtree(test_state_path)

    # Create the temporary directories
    test_run_path.mkdir(parents=True, exist_ok=True)
    test_state_path.mkdir(parents=True, exist_ok=True)

    # Monkeypatch the paths in the mirror module
    mirror.RUN_PATH = test_run_path
    mirror.STATE_PATH = test_state_path

    print(f"Redirected mirror.RUN_PATH to {test_run_path}")
    print(f"Redirected mirror.STATE_PATH to {test_state_path}")

    # Yield control to the test session
    yield

    # Teardown: Clean up the temporary directories after the test session
    print("Cleaning up test environment...")
    shutil.rmtree(test_run_path)
    shutil.rmtree(test_state_path)


@pytest.fixture
def clean_plugin_registry():
    """Provide a clean built-in plug-in registry for one test."""
    import mirror.plugin
    import mirror.sync
    from mirror.plugin import load_builtin_plugins

    mirror.plugin._registry.clear()
    mirror.plugin._BUILTIN_NAMES.clear()
    mirror.plugin._status_stat_hooks.clear()
    mirror.plugin._status_web_hooks.clear()
    mirror.sync.methods.clear()
    load_builtin_plugins()

    clean_registry = dict(mirror.plugin._registry)
    clean_methods = list(mirror.sync.methods)
    clean_builtins = set(mirror.plugin._BUILTIN_NAMES)

    yield

    mirror.plugin._registry.clear()
    mirror.plugin._registry.update(clean_registry)
    mirror.sync.methods[:] = clean_methods
    mirror.plugin._BUILTIN_NAMES.clear()
    mirror.plugin._BUILTIN_NAMES.update(clean_builtins)
    mirror.plugin._status_stat_hooks.clear()
    mirror.plugin._status_web_hooks.clear()


@pytest.fixture
def mock_mirror_log(monkeypatch):
    """Replace the process-wide mirror logger for one test."""
    monkeypatch.setattr(mirror, "log", MagicMock(), raising=False)


@pytest.fixture
def clean_sync_state():
    """Clear sync in-flight state before and after one test."""
    import mirror.sync

    with mirror.sync._start_lock:
        mirror.sync._extra_args.clear()
        mirror.sync._watchdog_fired.clear()
    yield
    with mirror.sync._start_lock:
        mirror.sync._extra_args.clear()
        mirror.sync._watchdog_fired.clear()


@pytest.fixture
def stub_mirror_event(monkeypatch):
    """Disable event dispatch for tests focused on status mutation."""
    monkeypatch.setattr("mirror.event.post_event", lambda *args, **kwargs: None)


@pytest.fixture
def reload_settings_factory():
    """Build settings dictionaries used by reload tests."""
    def make_settings(tmp_path: Path, **overrides) -> dict:
        settings = {
            "logfolder": str(tmp_path / "logs"),
            "webroot": str(tmp_path / "web"),
            "statusfile": str(tmp_path / "status.json"),
            "statfile": str(tmp_path / "stat.json"),
            "socket_path": str(tmp_path / "mirror.sock"),
            "uid": 1000,
            "gid": 1000,
            "localtimezone": "UTC",
            "errorcontinuetime": 60,
            "maintainer": {"name": "Test", "email": "t@t.com"},
            "logger": {
                "level": "INFO",
                "packagelevel": "ERROR",
                "format": "[%(asctime)s] %(levelname)s # %(message)s",
                "packageformat": "[%(asctime)s][{package}] %(levelname)s # %(message)s",
                "fileformat": {
                    "base": str(tmp_path / "logs"),
                    "folder": "{year}/{month}",
                    "filename": "{year}-{month}-{day}.log",
                    "gzip": False,
                },
                "packagefileformat": {
                    "base": str(tmp_path / "logs" / "packages"),
                    "folder": "{year}/{month}/{day}",
                    "filename": "{packageid}.{hour}.log",
                    "gzip": False,
                },
            },
            "ftpsync": {
                "maintainer": "M",
                "sponsor": "S",
                "country": "KR",
                "location": "Seoul",
                "throughput": "1G",
            },
            "plugins": [],
        }
        settings.update(overrides)
        return settings

    return make_settings


@pytest.fixture
def reload_package_factory():
    """Build package dictionaries used by reload tests."""
    def make_package(
        pkgid: str,
        src: str = "rsync://src/a",
        syncrate: str = "PT1H",
    ) -> dict:
        return {
            "id": pkgid,
            "name": pkgid,
            "href": f"/{pkgid}",
            "synctype": "rsync",
            "syncrate": syncrate,
            "link": [],
            "settings": {
                "hidden": False,
                "src": src,
                "dst": "/tmp/" + pkgid,
                "options": {},
            },
        }

    return make_package


@pytest.fixture
def reload_config_factory(reload_settings_factory, reload_package_factory):
    """Build complete configuration dictionaries for reload tests."""
    def make_config(
        tmp_path: Path,
        packages: dict | None = None,
        default_pkgid: str = "pkg-alpha",
        **settings_overrides,
    ) -> dict:
        if packages is None:
            packages = {
                default_pkgid: reload_package_factory(default_pkgid),
            }
        return {
            "mirrorname": "TestMirror",
            "hostname": "test.local",
            "settings": reload_settings_factory(tmp_path, **settings_overrides),
            "packages": packages,
        }

    return make_config


@pytest.fixture
def daemon_master_server():
    """Patch the master server constructor for daemon unit tests."""
    with patch("mirror.socket.master.MasterServer") as mock:
        yield mock


@pytest.fixture
def daemon_dependencies():
    """Isolate common daemon dependencies and mutable module state."""
    with (
        patch("mirror.config.load"),
        patch("mirror.logger.setup_logger"),
        patch("mirror.sync.start"),
        patch.object(mirror, "packages", {}, create=True),
        patch.object(mirror, "log", MagicMock(), create=True),
    ):
        yield


@pytest.fixture
def mock_signal():
    """Patch signal handler registration for command tests."""
    with patch("signal.signal") as mock:
        yield mock


@pytest.fixture
def mock_sys_exit():
    """Patch process exit for command tests."""
    with patch("sys.exit") as mock:
        yield mock


@pytest.fixture
def ftpsync_package_factory():
    """Build a minimal package object for ftpsync config tests."""
    def make_package(
        options: dict,
        src: str = "rsync.example.org",
        dst: str = "/tmp/dst",
    ) -> MagicMock:
        package = MagicMock()
        package.settings.src = src
        package.settings.dst = dst
        package.settings.options = options
        return package

    return make_package


@pytest.fixture
def stub_ftpsync_conf(monkeypatch):
    """Install global ftpsync defaults used by config generation tests."""
    fake_conf = MagicMock()
    fake_conf.name = "TestMirror"
    fake_conf.hostname = "ftp.example.org"
    fake_conf.logfolder = Path("/var/log/mirror")
    fake_conf.ftpsync = types.SimpleNamespace(
        maintainer="Admins <admins@example.com>",
        sponsor="Example <https://example.com>",
        country="KR",
        location="Seoul",
        throughput="1G",
        include="",
        exclude="",
    )
    monkeypatch.setattr(mirror, "conf", fake_conf, raising=False)


class FinishedFakePopen:
    """Minimal completed process used by worker pruning tests."""

    def __init__(self, *args, **kwargs):
        self.pid = 1
        self.returncode = 0
        self.stdin = None
        self.stdout = None
        self.stderr = None

    def poll(self):
        return 0

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def finished_job_factory():
    """Create completed worker jobs with clean registry entries."""
    from mirror.worker import process

    def make_job(job_id: str) -> process.Job:
        with process._jobs_lock:
            process._jobs.pop(job_id, None)

        with patch("mirror.worker.process.subprocess.Popen", FinishedFakePopen):
            return process.create(
                job_id,
                ["true"],
                {},
                os.getuid(),
                os.getgid(),
                0,
            )

    return make_job


@pytest.fixture
def patch_worker_notification():
    """Patch worker completion notification regardless of module aliasing."""
    import mirror.socket
    import mirror.socket.worker as original_worker_module

    @contextmanager
    def patch_notification(callback):
        current_worker = getattr(mirror.socket, "worker", original_worker_module)
        if not hasattr(current_worker, "send_finished_notification"):
            current_worker = original_worker_module

        if original_worker_module is current_worker:
            with patch.object(
                original_worker_module,
                "send_finished_notification",
                callback,
            ):
                yield
        else:
            with (
                patch.object(
                    original_worker_module,
                    "send_finished_notification",
                    callback,
                ),
                patch.object(
                    current_worker,
                    "send_finished_notification",
                    callback,
                ),
            ):
                yield

    return patch_notification
