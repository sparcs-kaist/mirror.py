"""Integration test helpers for mirror.py docker-based test suite."""

import gzip
import json
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


MIRROR_CONTAINER = "mirror"

_UNIT_BY_NAME = {
    "master": "mirror.service",
    "worker": "mirror-worker.service",
}


class MirrorStack:
    """Wrapper for the running compose stack with convenience methods.

    Args:
        integration_tmp(Path): Host-side temp directory bind-mounted into the mirror container.
    """

    def __init__(self, integration_tmp: Path) -> None:
        self._integration_tmp = integration_tmp

    @property
    def publish_dir(self) -> Path:
        """Host path for /srv/publish bind-mount."""
        return self._integration_tmp / "publish"

    @property
    def state_dir(self) -> Path:
        """Host path for /var/lib/mirror bind-mount."""
        return self._integration_tmp / "state"

    @property
    def log_dir(self) -> Path:
        """Host path for /var/log/mirror bind-mount."""
        return self._integration_tmp / "log"

    def stat_json(self) -> dict:
        """Parse and return the current stat.json contents.

        Return:
            data(dict): Parsed stat.json dictionary.
        """
        stat_path = self.state_dir / "stat.json"
        if not stat_path.exists():
            return {}
        return json.loads(stat_path.read_text())

    def package_status(self, pkgid: str) -> str:
        """Return the current status string for a package.

        Args:
            pkgid(str): Package identifier.

        Return:
            status(str): Status string (e.g. "ACTIVE", "ERROR", "SYNC", "UNKNOWN").
        """
        data = self.stat_json()
        packages = data.get("packages", {})
        pkg = packages.get(pkgid, {})
        status_obj = pkg.get("status", {})
        if isinstance(status_obj, dict):
            return status_obj.get("status", "UNKNOWN")
        return str(status_obj)

    def package_lastsync(self, pkgid: str) -> float:
        """Return the lastsync timestamp for a package.

        Args:
            pkgid(str): Package identifier.

        Return:
            lastsync(float): Unix timestamp of last sync, or 0.0 if not set.
        """
        data = self.stat_json()
        packages = data.get("packages", {})
        pkg = packages.get(pkgid, {})
        return pkg.get("lastsync", 0.0)

    def package_errorcount(self, pkgid: str) -> int:
        """Return the error count for a package.

        Args:
            pkgid(str): Package identifier.

        Return:
            errorcount(int): Number of errors recorded.
        """
        data = self.stat_json()
        packages = data.get("packages", {})
        pkg = packages.get(pkgid, {})
        status_obj = pkg.get("status", {})
        if isinstance(status_obj, dict):
            return status_obj.get("statusinfo", {}).get("errorcount", 0)
        return 0

    def runtime_package(self, pkgid: str) -> dict:
        """Return package state directly from the running master daemon.

        Args:
            pkgid(str): Package identifier.

        Return:
            package(dict): Package data returned by the master socket RPC.
        """
        script = (
            "import json; "
            "from mirror.socket.master import get_package; "
            f"print(json.dumps(get_package({pkgid!r})))"
        )
        result = self.docker_exec("python3", "-c", script, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"get_package({pkgid!r}) failed (rc={result.returncode}): "
                f"stderr={result.stderr!r}"
            )
        return json.loads(result.stdout.strip().splitlines()[-1])

    def worker_progress(self, pkgid: str) -> dict:
        """Return one job's live state directly from the worker daemon.

        Args:
            pkgid(str): Worker job identifier.

        Return:
            progress(dict): Worker ``get_progress`` RPC response.
        """
        script = (
            "import json; "
            "from mirror.socket.worker import get_progress; "
            f"print(json.dumps(get_progress({pkgid!r})))"
        )
        result = self.docker_exec("python3", "-c", script, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"get_progress({pkgid!r}) failed (rc={result.returncode}): "
                f"stderr={result.stderr!r}"
            )
        return json.loads(result.stdout.strip().splitlines()[-1])

    def docker_exec(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """Run a command inside the mirror container via docker exec.

        Args:
            *args(str): Command and arguments to execute.
            check(bool): Raise CalledProcessError if return code is non-zero.

        Return:
            result(subprocess.CompletedProcess): Completed process result.
        """
        cmd = ["docker", "exec", MIRROR_CONTAINER, *args]
        return subprocess.run(cmd, capture_output=True, text=True, check=check)

    def trigger_sync(self, pkgid: str) -> None:
        """Trigger an immediate sync for a package via the master socket RPC.

        If a sync is already in progress for the package, the call is treated
        as a no-op since the post-condition (sync running for pkgid) is already
        satisfied.

        Args:
            pkgid(str): Package identifier to sync.
        """
        script = (
            "from mirror.socket.master import start_sync; "
            f"start_sync({pkgid!r})"
        )
        result = self.docker_exec("python", "-c", script, check=False)
        if result.returncode == 0:
            return
        if "already syncing" in (result.stderr or ""):
            return
        raise RuntimeError(
            f"trigger_sync({pkgid!r}) failed (rc={result.returncode}): "
            f"stderr={result.stderr!r}"
        )

    def _systemctl_robust(self, action: str, unit: str) -> None:
        """Run `systemctl <action> <unit>`, retrying once after `reset-failed`.

        systemd applies a start-rate limit (StartLimitBurst within
        StartLimitIntervalSec) that fires when integration tests restart
        services rapidly. The retry transparently clears the failed counter
        without altering the production unit definition.

        Args:
            action(str): systemctl verb ("start", "stop", "restart").
            unit(str): Unit name.
        """
        result = self.docker_exec("systemctl", action, unit, check=False)
        if result.returncode == 0:
            return
        if "start of the service was attempted too often" in (result.stderr or ""):
            self.docker_exec("systemctl", "reset-failed", unit, check=False)
            self.docker_exec("systemctl", action, unit)
            return
        raise RuntimeError(
            f"systemctl {action} {unit} failed (rc={result.returncode}): "
            f"stderr={result.stderr!r}"
        )

    def restart_process(self, name: str) -> None:
        """Restart a managed daemon process by symbolic name.

        Args:
            name(str): Process name (e.g. "master" or "worker").
        """
        self._systemctl_robust("restart", _UNIT_BY_NAME[name])

    def stop_process(self, name: str) -> None:
        """Stop a managed daemon process by symbolic name.

        Args:
            name(str): Process name (e.g. "master" or "worker").
        """
        self._systemctl_robust("stop", _UNIT_BY_NAME[name])

    def start_process(self, name: str) -> None:
        """Start a managed daemon process by symbolic name.

        Args:
            name(str): Process name (e.g. "master" or "worker").
        """
        self._systemctl_robust("start", _UNIT_BY_NAME[name])

    def process_pid(self, name: str) -> int:
        """Return the MainPID of a managed daemon process.

        Args:
            name(str): Process name.

        Return:
            pid(int): Process PID. Returns 0 if not running.
        """
        result = self.docker_exec(
            "systemctl", "show", "--property=MainPID", "--value",
            _UNIT_BY_NAME[name],
        )
        try:
            pid = int(result.stdout.strip())
        except (ValueError, AttributeError):
            return 0
        return pid if pid > 0 else 0

    def swap_rsync_fixture_tree(self, src_dir: Path) -> None:
        """Replace the rsync-fixture data directory with new content via docker cp.

        Args:
            src_dir(Path): Host directory whose contents replace /srv/data/ in rsync-fixture.
        """
        subprocess.run(
            ["docker", "cp", f"{src_dir}/.", "rsync-fixture:/srv/data/"],
            check=True,
        )

    def write_file_in_fixture(self, service: str, path: str, content: bytes) -> None:
        """Write a file into a fixture container via docker exec.

        Args:
            service(str): Container service name (e.g. "rsync-fixture").
            path(str): Absolute path inside the container.
            content(bytes): File content to write.
        """
        container = service
        subprocess.run(
            ["docker", "exec", "-i", container, "tee", path],
            input=content,
            check=True,
            stdout=subprocess.DEVNULL,
        )

    def read_package_log_dir(self, pkgid: str) -> list[Path]:
        """Return all log files matching pkgid under the packages log directory.

        Args:
            pkgid(str): Package identifier to search for.

        Return:
            files(list[Path]): Sorted list of matching log file paths.
        """
        packages_dir = self.log_dir / "packages"
        if not packages_dir.exists():
            return []
        pattern = f"**/*{pkgid}*.log"
        gz_pattern = f"**/*{pkgid}*.log.gz"
        results = list(packages_dir.glob(pattern)) + list(packages_dir.glob(gz_pattern))
        return sorted(results)

    def wait_for_status(self, pkgid: str, expected: str, timeout: float = 60) -> float:
        """Poll stat.json until the package reaches the expected status.

        Args:
            pkgid(str): Package identifier.
            expected(str): Expected status string.
            timeout(float): Maximum seconds to wait before raising TimeoutError.

        Return:
            elapsed(float): Seconds elapsed until status matched.

        Raises:
            TimeoutError: If status does not match within timeout.
        """
        start = time.monotonic()
        deadline = start + timeout
        while time.monotonic() < deadline:
            if self.package_status(pkgid) == expected:
                return time.monotonic() - start
            time.sleep(0.5)
        elapsed = time.monotonic() - start
        actual = self.package_status(pkgid)
        raise TimeoutError(
            f"Package '{pkgid}' did not reach status '{expected}' within {timeout}s "
            f"(last status: '{actual}', elapsed: {elapsed:.1f}s)"
        )

    def wait_for_new_active_sync(
        self,
        pkgid: str,
        previous_lastsync: float,
        timeout: float = 60,
    ) -> float:
        """Wait for a newly completed successful sync generation.

        Args:
            pkgid(str): Package identifier.
            previous_lastsync(float): Timestamp before the requested sync.
            timeout(float): Maximum seconds to wait.

        Return:
            lastsync(float): Timestamp written by the new completion.

        Raises:
            TimeoutError: If no newer ACTIVE generation completes in time.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            package = self.stat_json().get("packages", {}).get(pkgid, {})
            status = package.get("status", {})
            status_name = (
                status.get("status", "UNKNOWN")
                if isinstance(status, dict)
                else str(status)
            )
            lastsync = package.get("lastsync", 0.0)
            if status_name == "ACTIVE" and lastsync > previous_lastsync:
                return lastsync
            time.sleep(0.2)
        raise TimeoutError(
            f"Package '{pkgid}' did not complete a new ACTIVE sync within {timeout}s "
            f"(status={self.package_status(pkgid)!r}, "
            f"before={previous_lastsync}, after={self.package_lastsync(pkgid)})"
        )

    def wait_for_master_ready(self, timeout: float = 30) -> None:
        """Poll supervisorctl until master process is RUNNING.

        Args:
            timeout(float): Maximum seconds to wait.

        Raises:
            TimeoutError: If master does not become RUNNING within timeout.
        """
        _wait_for_process_running("master", timeout=timeout)

    def wait_for_worker_ready(self, timeout: float = 30) -> None:
        """Poll supervisorctl until worker process is RUNNING.

        Args:
            timeout(float): Maximum seconds to wait.

        Raises:
            TimeoutError: If worker does not become RUNNING within timeout.
        """
        _wait_for_process_running("worker", timeout=timeout)


def _wait_for_process_running(name: str, timeout: float = 30) -> None:
    """Poll systemctl is-active for a managed unit until it reports active.

    Args:
        name(str): Symbolic process name (e.g. "master" or "worker").
        timeout(float): Maximum seconds to wait.

    Raises:
        TimeoutError: If the unit does not become active within timeout.
    """
    unit = _UNIT_BY_NAME[name]
    deadline = time.monotonic() + timeout
    last_state = ""
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", MIRROR_CONTAINER,
             "systemctl", "is-active", unit],
            capture_output=True,
            text=True,
        )
        last_state = result.stdout.strip()
        if last_state == "active":
            return
        time.sleep(1)
    raise TimeoutError(
        f"Unit '{unit}' did not reach active state within {timeout}s. "
        f"Last systemctl is-active output: {last_state!r}"
    )


def make_minimal_config(packages: dict) -> dict:
    """Build a minimal mirror config dict with given packages.

    Useful for config-reload tests that need to write a new config.json.

    Args:
        packages(dict): Package definitions in config.json package schema format.

    Return:
        config(dict): Full config dict suitable for JSON serialization.
    """
    return {
        "mirrorname": "test-mirror",
        "hostname": "mirror.test",
        "settings": {
            "logfolder": "/var/log/mirror",
            "webroot": "/var/www/mirror",
            "statusfile": "/var/www/mirror/status.json",
            "statfile": "/var/lib/mirror/stat.json",
            "localtimezone": "UTC",
            "errorcontinuetime": 10,
            "maintainer": {"name": "test", "email": "test@example.com"},
            "logger": {
                "level": "INFO",
                "packagelevel": "INFO",
                "format": "[%(asctime)s] %(levelname)s # %(message)s",
                "packageformat": "[%(asctime)s][{package}] %(levelname)s # %(message)s",
                "fileformat": {
                    "base": "/var/log/mirror",
                    "folder": "{year}/{month}",
                    "filename": "{year}-{month}-{day}.log",
                    "gzip": True,
                },
                "packagefileformat": {
                    "base": "/var/log/mirror/packages",
                    "folder": "{year}/{month}/{day}",
                    "filename": "{hour}:{minute}:{second}.{microsecond}.{packageid}.log",
                    "gzip": True,
                },
            },
            "ftpsync": {
                "maintainer": "test <test@example.com>",
                "sponsor": "test",
                "country": "ZZ",
                "location": "test",
                "throughput": "1Gb",
            },
            "plugins": [],
        },
        "packages": packages,
    }


def run_fixture_python(
    container: str,
    script: str,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    """Run a bounded Python mutation inside a fixture container.

    Args:
        container(str): Fixture container name.
        script(str): Python source passed to ``python -c``.
        *args(str): Positional arguments exposed to the script through ``sys.argv``.

    Return:
        result(subprocess.CompletedProcess): Completed Docker command.
    """
    return subprocess.run(
        ["docker", "exec", container, "python", "-c", script, *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def latest_package_log_text(mirror_stack: MirrorStack, package_id: str) -> str:
    """Read the newest completed log for a package.

    Args:
        mirror_stack(MirrorStack): Running integration stack.
        package_id(str): Package identifier used to select logs.

    Return:
        text(str): Uncompressed package log contents.
    """
    logs = mirror_stack.read_package_log_dir(package_id)
    assert logs, f"No package logs found for {package_id}"
    latest = logs[-1]
    if latest.suffix == ".gz":
        with gzip.open(latest, "rt", errors="replace") as stream:
            return stream.read()
    return latest.read_text(errors="replace")


def _rsync_fixture_exec(*args: str) -> subprocess.CompletedProcess:
    """Run a bounded command in the rsync fixture container."""
    return subprocess.run(
        ["docker", "exec", "rsync-fixture", *args],
        capture_output=True,
        text=True,
        timeout=10,
    )


def clear_slow_rsync_gate() -> None:
    """Remove stale slow-rsync gate files before starting a gated job."""
    _rsync_fixture_exec(
        "rm",
        "-f",
        "/tmp/mirror-slow-rsync.ready",
        "/tmp/mirror-slow-rsync.release",
    )


def wait_for_slow_rsync_gate(timeout: float = 10) -> None:
    """Wait until the slow rsync module reaches its pre-transfer gate."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _rsync_fixture_exec(
            "test", "-e", "/tmp/mirror-slow-rsync.ready"
        ).returncode == 0:
            return
        time.sleep(0.1)
    raise TimeoutError("slow rsync did not reach the pre-transfer gate")


def release_slow_rsync_gate() -> None:
    """Release the slow rsync gate and remove its coordination files."""
    ready_path = "/tmp/mirror-slow-rsync.ready"
    release_path = "/tmp/mirror-slow-rsync.release"
    _rsync_fixture_exec("touch", release_path)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if _rsync_fixture_exec("test", "-e", ready_path).returncode != 0:
            break
        time.sleep(0.1)
    else:
        result = _rsync_fixture_exec("cat", ready_path)
        pid = result.stdout.strip()
        if pid.isdigit():
            _rsync_fixture_exec("kill", "-TERM", pid)
    _rsync_fixture_exec("rm", "-f", ready_path, release_path)


@contextmanager
def temporary_rsync_package(
    mirror_stack: MirrorStack,
    pkgid: str,
    *,
    slow: bool = False,
) -> Iterator[str]:
    """Install a manual-only rsync package for one integration test.

    The package is added only after ``mirror_stack`` has settled its baseline
    packages. ``PT0S`` prevents background syncs from satisfying assertions
    intended to observe an explicitly triggered generation.

    Args:
        mirror_stack(MirrorStack): Running integration stack.
        pkgid(str): Unique package and worker job identifier.
        slow(bool): Use the fixture's bounded pre-transfer gate.

    Yields:
        pkgid(str): The installed package identifier.
    """
    config_result = mirror_stack.docker_exec("cat", "/etc/mirror/config.json")
    original_config = config_result.stdout
    config = json.loads(original_config)
    assert pkgid not in config["packages"], f"temporary package already exists: {pkgid}"
    config["packages"][pkgid] = {
        "name": pkgid,
        "id": pkgid,
        "href": f"/{pkgid}",
        "synctype": "rsync",
        "syncrate": "PT0S",
        "link": [],
        "settings": {
            "hidden": False,
            "src": f"rsync://rsync-fixture/{'slow' if slow else 'data'}",
            "dst": f"/srv/publish/{pkgid}",
            "options": {"username": "", "password": ""},
        },
    }

    if slow:
        clear_slow_rsync_gate()

    try:
        write_container_config("mirror", json.dumps(config, indent=2))
        reload_result = mirror_stack.docker_exec(
            "mirror", "config", "reload", check=False
        )
        assert reload_result.returncode == 0, (
            f"failed to install temporary package {pkgid}: "
            f"stdout={reload_result.stdout!r} stderr={reload_result.stderr!r}"
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if pkgid in mirror_stack.stat_json().get("packages", {}):
                break
            time.sleep(0.1)
        else:
            raise TimeoutError(f"temporary package {pkgid} did not appear in stat.json")
        yield pkgid
    finally:
        mirror_stack.docker_exec(
            "python3",
            "-c",
            "from mirror.socket.worker import stop_command; "
            f"stop_command(job_id={pkgid!r})",
            check=False,
        )
        if slow:
            release_slow_rsync_gate()
        write_container_config("mirror", original_config)
        restore_result = mirror_stack.docker_exec(
            "mirror", "config", "reload", check=False
        )
        assert restore_result.returncode == 0, (
            f"failed to restore config after temporary package {pkgid}: "
            f"stdout={restore_result.stdout!r} stderr={restore_result.stderr!r}"
        )


def write_container_config(container: str, config_text: str) -> None:
    """Replace the integration config inside a container.

    Args:
        container(str): Container whose config should be replaced.
        config_text(str): Complete JSON config contents.
    """
    result = subprocess.run(
        ["docker", "exec", "-i", container, "tee", "/etc/mirror/config.json"],
        input=config_text,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Failed to write config.json: {result.stderr!r}"
