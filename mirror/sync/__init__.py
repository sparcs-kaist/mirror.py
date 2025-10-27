import mirror
import mirror.structure

import types
import logging
import os

from importlib.machinery import SourceFileLoader
from threading import Thread
from pathlib import Path

BasicMethodPath = Path(__file__).parent
methods = [method.stem for method in BasicMethodPath.glob("*.py") if method.stem != "__init__"]
now = []

class Options:
    def __init__(self, method):
        self.options: dict[str, type] = method.options
        for key in self.options.keys():
            setattr(self, key, None)
    
    def copy(self):
        new = Options(self)
        for key in self.options.keys():
            setattr(new, key, getattr(self, key))
        return new

def loader(methodPath: Path) -> None:
    """Load the sync moodules"""
    import mirror.sync
    methodsFullPath = [method for method in methodPath.glob("*.py") if method.stem != "__init__"]
    for method in methodsFullPath:
        this = SourceFileLoader(f"mirror.sync.{method.stem}", str(method)).load_module()
        setattr(mirror.sync, method.stem, this)

def load_default():
    """Load the default sync moodules"""
    loader(BasicMethodPath)

def execute(package: mirror.structure.Package, logger: logging.Logger, method: str):
    """
    Run the Sync method (CORE)
    Args:
        package (mirror.structure.Package): Package object
        logger (logging.Logger): Logger object
        method (types.ModuleType): Sync method
    Returns:
        Null
    """
    import mirror.sync
    getattr(mirror.sync, method).execute(package, logger)

def _execute(package: mirror.structure.Package, logger: logging.Logger, method: types.ModuleType) -> bool:
    """
    execute with threading
    """
    return False

def setexecuser(uid: int, gid: int):
    def setids():
        os.setgid(gid)
        os.setuid(uid)

    return setids
