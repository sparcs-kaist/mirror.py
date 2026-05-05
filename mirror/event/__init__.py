import threading
import logging
from concurrent.futures import ThreadPoolExecutor, wait as wait_futures
from typing import Callable, Any, Optional, Tuple

logger = logging.getLogger(__name__)

class EventManager:
    """
    Central event management system using Pub/Sub pattern.
    Supports synchronous and asynchronous (threaded) listeners.
    """
    def __init__(self, max_workers: int = 20):
        # Dictionary mapping event names to lists of tuples (priority, listener)
        self._listeners: dict[str, list[Tuple[int, Callable]]] = {}
        self._lock = threading.Lock()
        # Thread pool for asynchronous execution
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="EventWorker")

    def on(self, event_name: str, listener: Callable, priority: int = 50) -> None:
        """Register a listener for a specific event.

        Lower priority number means higher precedence (executes earlier).

        Args:
            event_name(str): Name of the event to listen for.
            listener(Callable): Callback to invoke when the event fires.
            priority(int, optional): Execution order (lower = earlier). Defaults to 50.
        """
        with self._lock:
            if event_name not in self._listeners:
                self._listeners[event_name] = []
            
            # Check if listener is already registered to avoid duplicates
            if not any(cb == listener for _, cb in self._listeners[event_name]):
                self._listeners[event_name].append((priority, listener))
                # Sort listeners by priority (ascending)
                self._listeners[event_name].sort(key=lambda x: x[0])
                logger.debug(f"Registered listener {listener.__name__} for event '{event_name}' with priority {priority}")

    def once(self, event_name: str, listener: Callable, priority: int = 50) -> None:
        """Register a one-shot listener that auto-removes itself after first invocation.

        Args:
            event_name(str): Name of the event to listen for.
            listener(Callable): Callback to invoke once.
            priority(int, optional): Execution order. Defaults to 50.
        """
        def wrapper(*args, **kwargs):
            try:
                listener(*args, **kwargs)
            except Exception as e:
                logger.exception(f"Error in 'once' listener '{listener.__name__}' for '{event_name}': {e}")
            finally:
                self.off(event_name, wrapper)
        
        # Preserve original name for debugging purposes
        wrapper.__name__ = getattr(listener, "__name__", "unknown_listener")
        self.on(event_name, wrapper, priority)

    def off(self, event_name: str, listener: Callable) -> None:
        """Unregister a previously registered listener.

        Args:
            event_name(str): Event name the listener is registered under.
            listener(Callable): Listener to remove.
        """
        with self._lock:
            if event_name in self._listeners:
                # Rebuild list without the listener
                self._listeners[event_name] = [
                    (p, cb) for p, cb in self._listeners[event_name] if cb != listener
                ]

    def post_event(self, event_name: str, *args, wait: bool = False, **kwargs) -> None:
        """Fire an event, executing all registered listeners.

        Args:
            event_name(str): Name of the event to fire.
            *args: Positional payload forwarded to listeners.
            wait(bool, keyword-only): If True, block until all listeners complete.
            **kwargs: Keyword payload forwarded to listeners.
        """

        with self._lock:
            # Copy list to allow modification during iteration
            listeners = self._listeners.get(event_name, [])[:]

        if not listeners:
            return

        logger.debug(f"Event '{event_name}' fired. Triggering {len(listeners)} listeners.")

        futures = []
        # listeners list is already sorted by priority during 'on' registration
        for _, listener in listeners:
            future = self._execute_listener(listener, event_name, *args, **kwargs)
            if wait:
                futures.append(future)
        
        if wait and futures:
            wait_futures(futures)

    def _execute_listener(self, listener: Callable, event_name: str, *args, **kwargs) -> "Future":
        """Submit a single listener to the thread pool for safe async execution."""
        def wrapper():
            try:
                listener(*args, **kwargs)
            except Exception as e:
                logger.exception(f"Error in event listener '{listener.__name__}' for '{event_name}': {e}")

        return self._executor.submit(wrapper)

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the event manager and its thread pool.

        Args:
            wait(bool, optional): If True, block until all running listeners complete. Defaults to True.
        """
        self._executor.shutdown(wait=wait)

# Global singleton instance
_manager = EventManager()

# Public API wrappers
def on(event_name: str, listener: Optional[Callable] = None, priority: int = 50):
    """Register a listener for an event, or return a decorator if listener is omitted.

    Args:
        event_name(str): Event name to listen for.
        listener(Callable, optional): Callback to register. If None, returns a decorator.
        priority(int, optional): Execution order. Defaults to 50.
    """
    if listener is None:
        def decorator(func):
            _manager.on(event_name, func, priority)
            return func
        return decorator
    _manager.on(event_name, listener, priority)

def once(event_name: str, listener: Callable, priority: int = 50) -> None:
    """Register a one-shot listener via the global manager.

    Args:
        event_name(str): Event name to listen for.
        listener(Callable): Callback to invoke once.
        priority(int, optional): Execution order. Defaults to 50.
    """
    _manager.once(event_name, listener, priority)

def off(event_name: str, listener: Callable) -> None:
    """Unregister a listener via the global manager.

    Args:
        event_name(str): Event name the listener is registered under.
        listener(Callable): Listener to remove.
    """
    _manager.off(event_name, listener)

def post_event(event_name: str, *args, wait: bool = False, **kwargs) -> None:
    """Fire an event via the global manager.

    Args:
        event_name(str): Name of the event to fire.
        *args: Positional payload forwarded to listeners.
        wait(bool, keyword-only): If True, block until all listeners complete.
        **kwargs: Keyword payload forwarded to listeners.
    """
    _manager.post_event(event_name, *args, wait=wait, **kwargs)

# Decorator for easy registration
def listener(event_name: str, priority: int = 50):
    """Decorator to register a function as an event listener.

    Args:
        event_name(str): Event name to listen for.
        priority(int, optional): Execution order. Defaults to 50.
    """
    def decorator(func):
        on(event_name, func, priority)
        return func
    return decorator

__all__ = ["on", "once", "off", "post_event", "listener", "EventManager"]