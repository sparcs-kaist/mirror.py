import mirror
import mirror.structure

import time
import logging

from typing import Callable
import importlib.util
from threading import Thread
from pathlib import Path

BasicMethodPath = Path(__file__).parent
methods = []

def setup():
    load_default()

def loader(methodPath: Path) -> None:
    """Load the sync moodules"""
    import mirror.sync
    global methods
    methodsFullPath = [method for method in methodPath.glob("*.py") if method.stem != "__init__"]
    for method in methodsFullPath:
        if method.stem.startswith("_"):
            continue

        module_name = f"mirror.sync.{method.stem}"
        spec = importlib.util.spec_from_file_location(module_name, str(method))
        if spec and spec.loader:
            this = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(this)

            if getattr(this, "_LOAD", True) is False:
                continue

            setattr(mirror.sync, method.stem, this)
            if method.stem not in methods:
                methods.append(method.stem)

def get_module(method: str) -> Callable:
    """Get the sync moodule"""
    import mirror.sync
    return getattr(mirror.sync, method)

def start(package: "mirror.structure.Package") -> None:
    """
    Start sync for a package.

    Args:
        package: Package object to sync
    """
    import mirror.sync
    import mirror.logger

    method = package.synctype
    if method not in methods:
        raise ValueError(f"Unknown sync method: {method}")

    start_time = time.time()
    pkg_logger = mirror.logger.create_logger(package.pkgid, start_time)

    package.set_status("SYNC")
    sync_module = getattr(mirror.sync, method)
    thread = Thread(target=sync_module.execute, args=(package, pkg_logger), daemon=True)
    thread.start()

def load_default():
    """Load the default sync moodules"""
    loader(BasicMethodPath)

def execute(package: "mirror.structure.Package", logger: logging.Logger): ...
