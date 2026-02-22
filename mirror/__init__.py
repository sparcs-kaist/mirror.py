import mirror.structure

from pathlib import Path
import logging

conf: mirror.structure.Config
packages: mirror.structure.Packages
confPath: Path
publishPath: Path
log: logging.Logger
worker: dict[str, mirror.structure.Worker]
status: dict
debug: bool = False
exit: bool = False
__version__ = "1.0.0-pre3"

STATE_PATH = Path("/var/lib/mirror/")
RUN_PATH = Path("/var/run/mirror/")

import mirror.sync
mirror.sync.load_default()

# import mirror.plugin
#mirror.plugin.plugin_loader()
