"""Tests for mirror.worker.process.run_foreground."""

import os

import pytest

from mirror.worker.process import run_foreground


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


def test_log_helper_started_and_reaped():
    """A log_helper_command is started and reaped without hanging."""
    rc = run_foreground(
        "test-helper",
        ["true"],
        {},
        None,
        None,
        0,
        log_helper_command=["sleep", "0.1"],
    )
    assert rc == 0


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
