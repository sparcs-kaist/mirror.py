"""Error-retry test: error-test package fails, retries after errorcontinuetime elapses."""

import time

import pytest


@pytest.mark.integration
def test_error_package_retries_after_errorcontinuetime(mirror_stack):
    """error-test reaches ERROR status and retries after errorcontinuetime (10s).

    error-test points at a nonexistent rsync module. The daemon schedules retries
    once errorcontinuetime has elapsed. Verify errorcount increments.
    """
    # Wait for error-test to fail at least once.
    mirror_stack.wait_for_status("error-test", "ERROR", timeout=30)

    assert mirror_stack.package_status("error-test") == "ERROR", (
        "error-test did not reach ERROR status within 30s"
    )

    error_count_before = mirror_stack.package_errorcount("error-test")
    assert error_count_before >= 1, (
        f"Expected errorcount >= 1 after first failure, got {error_count_before}"
    )

    # errorcontinuetime is 10s; wait long enough for at least one retry cycle.
    time.sleep(15)

    error_count_after = mirror_stack.package_errorcount("error-test")
    assert error_count_after > error_count_before, (
        f"errorcount did not increase after errorcontinuetime elapsed; "
        f"before={error_count_before}, after={error_count_after}. "
        "Retry did not fire."
    )
