"""Integration test helpers for mirror.py docker-based test suite."""

import json
import subprocess
import time
from pathlib import Path


MIRROR_CONTAINER = "mirror"


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

        Args:
            pkgid(str): Package identifier to sync.
        """
        script = (
            "from mirror.socket.master import start_sync; "
            f"start_sync({pkgid!r})"
        )
        self.docker_exec("python", "-c", script)

    def restart_process(self, name: str) -> None:
        """Restart a supervised process by name.

        Args:
            name(str): Process name (e.g. "master" or "worker").
        """
        self.docker_exec("supervisorctl", "restart", name)

    def stop_process(self, name: str) -> None:
        """Stop a supervised process by name.

        Args:
            name(str): Process name (e.g. "master" or "worker").
        """
        self.docker_exec("supervisorctl", "stop", name)

    def start_process(self, name: str) -> None:
        """Start a supervised process by name.

        Args:
            name(str): Process name (e.g. "master" or "worker").
        """
        self.docker_exec("supervisorctl", "start", name)

    def process_pid(self, name: str) -> int:
        """Return the PID of a supervised process.

        Args:
            name(str): Process name.

        Return:
            pid(int): Process PID. Returns 0 if not running.
        """
        result = self.docker_exec("supervisorctl", "pid", name)
        try:
            return int(result.stdout.strip())
        except (ValueError, AttributeError):
            return 0

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
    """Poll supervisorctl status for a process until it shows RUNNING.

    Args:
        name(str): Supervisord process name.
        timeout(float): Maximum seconds to wait.

    Raises:
        TimeoutError: If process does not become RUNNING within timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", MIRROR_CONTAINER, "supervisorctl", "status", name],
            capture_output=True,
            text=True,
        )
        if "RUNNING" in result.stdout:
            return
        time.sleep(1)
    raise TimeoutError(
        f"Process '{name}' did not reach RUNNING state within {timeout}s. "
        f"Last supervisorctl output: {result.stdout!r}"
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
