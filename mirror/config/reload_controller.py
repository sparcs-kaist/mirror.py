"""Coordination object for config-reload requests.

The master daemon's main loop is the only thread that should mutate
``mirror.packages`` / ``mirror.conf`` / ``stat.json``. SIGHUP handlers and
socket-handler threads enqueue requests through this controller; the main
loop drains them at the start of each iteration.

Two request shapes are supported:
- ``request_signal()``: signal-handler-safe, fire-and-forget. Used by SIGHUP.
- ``request_sync(timeout)``: blocks until the main loop reports the result.
  Used by the ``mirror config reload`` CLI via the master socket RPC.
"""
import threading


class _ReloadResponse:
    """Per-request future used by ``request_sync`` callers."""
    __slots__ = ("event", "result")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: dict | None = None


class ReloadController:
    """Thread-safe queue between request producers and the daemon main loop.

    Properties:
    - Concurrent ``request_sync`` callers each receive their own
      ``_ReloadResponse``; one main-loop reload run satisfies all of them
      with the SAME result dict (no stale-result leakage).
    - SIGHUP merges into a pending CLI reload (one reload pass, one result).
    - The signal-side path (``request_signal``) does ONE plain attribute
      store; it never acquires a lock or calls ``Event.set()``, both of
      which can re-enter unsafely under a CPython signal handler.
    """

    def __init__(self) -> None:
        self._sighup_pending: bool = False
        self._pending_responses: list[_ReloadResponse] = []
        self._lock = threading.Lock()

    def request_signal(self) -> None:
        """Signal-handler-safe entry point for SIGHUP.

        Must NOT take any lock or call methods that internally lock.
        ``self._sighup_pending = True`` is a single atomic store under the
        GIL, which is the strongest guarantee we have available here.
        """
        self._sighup_pending = True

    def request_sync(self, timeout: float) -> dict:
        """Block until the main loop processes a reload, then return the result.

        Args:
            timeout(float): Maximum seconds to wait.

        Return:
            result(dict): The reload result, or
            ``{"status": "error", "error": "reload timeout after Ns"}`` if
            the main loop did not signal completion within ``timeout``.
        """
        response = _ReloadResponse()
        with self._lock:
            self._pending_responses.append(response)
        if not response.event.wait(timeout):
            with self._lock:
                if response in self._pending_responses:
                    self._pending_responses.remove(response)
            return {"status": "error", "error": f"reload timeout after {timeout}s"}
        return response.result if response.result is not None else \
            {"status": "error", "error": "reload completed without a result"}

    def consume_pending(self) -> tuple[bool, list[_ReloadResponse]]:
        """Atomically pop all pending requests. Called from the main loop.

        Return:
            should_reload(bool): True iff at least one request is pending.
            responses(list[_ReloadResponse]): CLI requesters awaiting a result.
                The signal-side request does not produce a response object.
        """
        with self._lock:
            had_signal = self._sighup_pending
            self._sighup_pending = False
            responses = self._pending_responses
            self._pending_responses = []
        return (had_signal or bool(responses)), responses

    def signal_done(self, responses: list[_ReloadResponse], result: dict) -> None:
        """Deliver the reload result to all waiting ``request_sync`` callers."""
        for response in responses:
            response.result = result
            response.event.set()


reload_controller = ReloadController()
