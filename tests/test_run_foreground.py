"""Tests for mirror.worker.process.run_foreground."""

import os
import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

from mirror.worker.process import FOREGROUND_HELPER_KILL_TIMEOUT, run_foreground


def test_exit_code_nonzero():
    """run_foreground returns the child's exit code when non-zero."""
    rc = run_foreground(
        "test-exit7",
        ["sh", "-c", "exit 7"],
        {},
        None,
        None,
        0,
    )
    assert rc == 7


def test_exit_code_zero():
    """run_foreground returns 0 for a successful command."""
    rc = run_foreground(
        "test-true",
        ["true"],
        {},
        None,
        None,
        0,
    )
    assert rc == 0


def test_env_var_reaches_child():
    """Extra env vars are visible inside the child process."""
    rc = run_foreground(
        "test-env",
        ["sh", "-c", "exit $MYVAR"],
        {"MYVAR": "5"},
        None,
        None,
        0,
    )
    assert rc == 5


def test_log_helper_started_before_main_and_reaped():
    """A real log helper starts before the main command and is reaped."""
    real_popen = subprocess.Popen
    processes = []
    helper_command = ["sleep", "60"]

    def popen_spy(command, *args, **kwargs):
        process = real_popen(command, *args, **kwargs)
        if command == helper_command:
            process.wait = MagicMock(wraps=process.wait)
        processes.append((command, process))
        return process

    main_command = ["sh", "-c", "sleep 0.05"]
    try:
        with patch("mirror.worker.process.subprocess.Popen", side_effect=popen_spy):
            rc = run_foreground(
                "test-helper",
                main_command,
                {},
                None,
                None,
                0,
                log_helper_command=helper_command,
            )

        assert rc == 0
        assert [command for command, _ in processes] == [helper_command, main_command]
        helper_process = processes[0][1]
        helper_process.wait.assert_called_once_with(
            timeout=FOREGROUND_HELPER_KILL_TIMEOUT
        )
        assert helper_process.returncode is not None
    finally:
        for _, process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def test_log_helper_timeout_is_killed_and_reaped():
    """A helper that ignores terminate is killed and waited for."""
    helper_process = MagicMock()
    helper_process.poll.return_value = None
    helper_process.wait.side_effect = [subprocess.TimeoutExpired("helper", 5), 0]
    main_result = MagicMock(returncode=0)

    with (
        patch("mirror.worker.process.subprocess.Popen", return_value=helper_process),
        patch("mirror.worker.process.subprocess.run", return_value=main_result),
    ):
        rc = run_foreground(
            "test-helper-timeout",
            ["true"],
            {},
            None,
            None,
            0,
            log_helper_command=["helper"],
        )

    assert rc == 0
    helper_process.terminate.assert_called_once_with()
    helper_process.kill.assert_called_once_with()
    assert helper_process.wait.call_args_list == [
        call(timeout=FOREGROUND_HELPER_KILL_TIMEOUT),
        call(),
    ]


def test_none_uid_gid_runs_as_current_user():
    """uid=None, gid=None, nice=0 runs as the current user without error."""
    rc = run_foreground(
        "test-current-user",
        ["sh", "-c", "exit 0"],
        {},
        None,
        None,
        0,
    )
    assert rc == 0


def test_does_not_register_in_jobs_registry():
    """run_foreground must not add anything to the _jobs registry."""
    from mirror.worker import process

    before = set(process._jobs.keys())
    run_foreground(
        "test-no-registry",
        ["true"],
        {},
        None,
        None,
        0,
    )
    after = set(process._jobs.keys())
    assert after == before
