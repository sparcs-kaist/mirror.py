import mirror.structure

from pathlib import Path
import logging

conf: mirror.structure.Config
packages: mirror.structure.Packages
confPath: Path
publishPath: Path
log: logging.Logger = logging.getLogger("mirror")
worker: dict
status: dict
debug: bool = False
exit: bool = False
__version__ = "1.0.0rc11"

STATE_PATH = Path("/var/lib/mirror/")
RUN_PATH = Path("/var/run/mirror/")

import mirror.sync
import mirror.plugin
mirror.plugin.load_builtin_plugins()
