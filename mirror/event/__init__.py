import threading
import logging
from concurrent.futures import ThreadPoolExecutor, wait as wait_futures
from typing import Callable, Any, Optional

logger = logging.getLogger(__name__)

class EventManager:
    """
    Central event management system using Pub/Sub pattern.
    Supports synchronous and asynchronous (threaded) listeners.
    """
    def __init__(self, max_workers: int = 20):
        # Dictionary mapping event names to lists of listeners
        self._listeners: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()
        # Thread pool for asynchronous execution
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="EventWorker")

    def on(self, event_name: str, listener: Callable):
        """
        Register a listener for a specific event.
        """
        with self._lock:
            if event_name not in self._listeners:
                self._listeners[event_name] = []
            if listener not in self._listeners[event_name]:
                self._listeners[event_name].append(listener)
                logger.debug(f"Registered listener {listener.__name__} for event '{event_name}'")

    def once(self, event_name: str, listener: Callable):
        """
        Register a listener that runs only once.
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
        self.on(event_name, wrapper)

    def off(self, event_name: str, listener: Callable):
        """
        Unregister a listener.
        """
        with self._lock:
            if event_name in self._listeners:
                if listener in self._listeners[event_name]:
                    self._listeners[event_name].remove(listener)

    def post_event(self, event_name: str, *args, **kwargs):
        """
        Fire an event.
        
        Optional Keyword Args:
            wait (bool): If True, blocks until all listeners have completed. 
                         Defaults to False. Consumed by this method.
        """
        should_wait = kwargs.pop('wait', False)

        with self._lock:
            # Copy list to allow modification during iteration
            listeners = self._listeners.get(event_name, [])[:]

        if not listeners:
            return

        logger.debug(f"Event '{event_name}' fired. Triggering {len(listeners)} listeners.")

        futures = []
        for listener in listeners:
            future = self._execute_listener(listener, event_name, *args, **kwargs)
            if should_wait:
                futures.append(future)
        
        if should_wait and futures:
            wait_futures(futures)

    def _execute_listener(self, listener: Callable, event_name: str, *args, **kwargs):
        """Execute a single listener safely using the thread pool."""
        def wrapper():
            try:
                listener(*args, **kwargs)
            except Exception as e:
                logger.exception(f"Error in event listener '{listener.__name__}' for '{event_name}': {e}")

        return self._executor.submit(wrapper)

    def shutdown(self, wait: bool = True):
        """Shutdown the event manager and its thread pool."""
        self._executor.shutdown(wait=wait)

# Global singleton instance
_manager = EventManager()

# Public API wrappers
def on(event_name: str, listener: Callable):
    """Register a listener for an event."""
    _manager.on(event_name, listener)

def once(event_name: str, listener: Callable):
    """Register a listener that runs only once."""
    _manager.once(event_name, listener)

def off(event_name: str, listener: Callable):
    """Unregister a listener."""
    _manager.off(event_name, listener)

def post_event(event_name: str, *args, **kwargs):
    """
    Fire an event.
    Use 'wait=True' to block until completion.
    """
    _manager.post_event(event_name, *args, **kwargs)

# Decorator for easy registration
def listener(event_name: str):
    """Decorator to register a function as an event listener."""
    def decorator(func):
        on(event_name, func)
        return func
    return decorator

__all__ = ["on", "once", "off", "post_event", "listener", "EventManager"]