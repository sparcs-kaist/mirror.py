"""Preflight test: validate the ftpsync minimal archive layout is accepted by archvsync.

Run this before other ftpsync tests. If this fails, the fixture layout needs augmentation.

This test invokes archvsync's ftpsync script directly inside the mirror container —
bypassing mirror.py's master/worker orchestration — to isolate fixture-layout validation
from scheduler logic.
"""

import pytest


@pytest.mark.integration
@pytest.mark.dependency(name="ftpsync_preflight")
def test_ftpsync_preflight_archvsync_runs(mirror_stack):
    """Sanity-check: archvsync's ftpsync exits 0 against our minimal Debian archive fixture.

    Sets up an archvsync working directory inside the mirror container using
    mirror.sync.ftpsync.setup_ftpsync (which performs the same git-clone-then-fallback
    logic the real daemon uses), then invokes bin/ftpsync directly. Exit code 0
    confirms the fixture layout is accepted; non-zero pinpoints the fixture gap.
    """
    # Use Python inside the container to invoke setup_ftpsync and run bin/ftpsync,
    # capturing the exit code. This replicates exactly what the worker would do,
    # but without going through the socket IPC layer.
    script = (
        "import subprocess, tempfile, sys\n"
        "from pathlib import Path\n"
        "import mirror, mirror.config, mirror.sync.ftpsync as ft\n"
        "\n"
        # Load the production config so mirror.conf and mirror.packages are populated.
        # setup_ftpsync() and _config() rely on mirror.conf.name and mirror.conf.logfolder.
        "mirror.config.load(Path('/etc/mirror/config.json'))\n"
        "pkg = mirror.packages['ftpsync-test']\n"
        "\n"
        "with tempfile.TemporaryDirectory(prefix='ftpsync_preflight_', dir='/var/lib/mirror') as tmp:\n"
        "    tmp = Path(tmp)\n"
        "    ft.setup_ftpsync(tmp, pkg)\n"
        "    result = subprocess.run(\n"
        "        [str(tmp / 'bin' / 'ftpsync'), 'sync:all'],\n"
        "        cwd=str(tmp),\n"
        "        capture_output=True,\n"
        "        text=True,\n"
        "    )\n"
        "    print('stdout:', result.stdout[-2000:] if result.stdout else '')\n"
        "    print('stderr:', result.stderr[-2000:] if result.stderr else '')\n"
        "    print('returncode:', result.returncode)\n"
        "    sys.exit(result.returncode)\n"
    )

    result = mirror_stack.docker_exec("python3", "-c", script, check=False)
    assert result.returncode == 0, (
        f"archvsync ftpsync did not accept the minimal fixture layout (exit {result.returncode}).\n"
        f"stdout: {result.stdout[-3000:]}\n"
        f"stderr: {result.stderr[-3000:]}\n"
        f"Inspect the fixture at tests/integration/docker/ftpsync-fixture/data/."
    )
