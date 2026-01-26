import time
import logging
import mirror
from .process import Job, create, get, get_all, prune_finished

logger = logging.getLogger(__name__)

def manage(interval: int = 1):
    """
    Background manager that monitors and prunes finished workers.
    """
    logger.info("Worker manager started.")
    while True:
        try:
            # Check for finished processes and cleanup their log threads
            prune_finished()
        except Exception as e:
            logger.error(f"Error in worker manager: {e}")
        
        if mirror.exit:
            break

        time.sleep(interval)


__all__ = ["Job", "create", "get", "get_all", "prune_finished", "manage"]