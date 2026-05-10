"""Tests for mirror.config.reload_controller.ReloadController and _ReloadResponse."""
import threading
import time

import pytest

from mirror.config.reload_controller import ReloadController, _ReloadResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_controller() -> ReloadController:
    return ReloadController()


# ---------------------------------------------------------------------------
# Test 1: request_signal sets flag without lock contention
# ---------------------------------------------------------------------------

def test_request_signal_sets_flag_without_lock():
    """request_signal() from a thread sets the flag; consume_pending() returns (True, [])."""
    ctrl = _make_controller()

    done = threading.Event()

    def _worker():
        ctrl.request_signal()
        done.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    assert done.wait(timeout=2.0), "signal thread did not finish"
    t.join(timeout=2.0)

    should_reload, responses = ctrl.consume_pending()
    assert should_reload is True
    assert responses == []


# ---------------------------------------------------------------------------
# Test 2: request_sync timeout returns error and removes response from pending
# ---------------------------------------------------------------------------

def test_request_sync_timeout_returns_error():
    """request_sync(timeout=0.1) with no main loop returns a timeout error dict."""
    ctrl = _make_controller()

    result = ctrl.request_sync(timeout=0.1)

    assert result["status"] == "error"
    assert "timeout" in result["error"]
    # The timed-out response must have been removed from _pending_responses.
    with ctrl._lock:
        assert len(ctrl._pending_responses) == 0


# ---------------------------------------------------------------------------
# Test 3: request_sync receives result via signal_done
# ---------------------------------------------------------------------------

def test_request_sync_receives_result_from_signal_done():
    """A thread calling request_sync receives the dict delivered by signal_done."""
    ctrl = _make_controller()
    returned: list[dict] = []

    def _caller():
        returned.append(ctrl.request_sync(timeout=2.0))

    t = threading.Thread(target=_caller, daemon=True)
    t.start()

    # Give the caller time to register its response.
    time.sleep(0.05)

    should_reload, responses = ctrl.consume_pending()
    assert should_reload is True
    assert len(responses) == 1

    expected = {"status": "ok", "added": ["x"]}
    ctrl.signal_done(responses, expected)

    t.join(timeout=3.0)
    assert len(returned) == 1
    assert returned[0] == expected


# ---------------------------------------------------------------------------
# Test 4: two concurrent request_sync callers get the same result
# ---------------------------------------------------------------------------

def test_two_concurrent_request_sync_callers_get_same_result():
    """Two concurrent request_sync callers both receive the same result dict."""
    ctrl = _make_controller()
    results: list[dict] = []
    lock = threading.Lock()

    def _caller():
        r = ctrl.request_sync(timeout=2.0)
        with lock:
            results.append(r)

    t1 = threading.Thread(target=_caller, daemon=True)
    t2 = threading.Thread(target=_caller, daemon=True)
    t1.start()
    t2.start()

    # Wait until both responses are registered.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with ctrl._lock:
            if len(ctrl._pending_responses) >= 2:
                break
        time.sleep(0.01)

    should_reload, responses = ctrl.consume_pending()
    assert should_reload is True
    assert len(responses) == 2

    expected = {"status": "ok"}
    ctrl.signal_done(responses, expected)

    t1.join(timeout=3.0)
    t2.join(timeout=3.0)

    assert len(results) == 2
    assert results[0] == expected
    assert results[1] == expected


# ---------------------------------------------------------------------------
# Test 5: consume_pending after only request_signal returns empty response list
# ---------------------------------------------------------------------------

def test_consume_pending_after_only_signal_returns_empty_response_list():
    """request_signal() only (no request_sync) → consume_pending returns (True, [])."""
    ctrl = _make_controller()
    ctrl.request_signal()

    should_reload, responses = ctrl.consume_pending()
    assert should_reload is True
    assert responses == []


# ---------------------------------------------------------------------------
# Test 6: consume_pending clears state; second call returns (False, [])
# ---------------------------------------------------------------------------

def test_consume_pending_clears_state():
    """After consume_pending, the state is cleared; a second call returns (False, [])."""
    ctrl = _make_controller()
    ctrl.request_signal()

    # First consume drains the flag.
    should_reload, _ = ctrl.consume_pending()
    assert should_reload is True

    # Second consume finds nothing.
    should_reload2, responses2 = ctrl.consume_pending()
    assert should_reload2 is False
    assert responses2 == []
