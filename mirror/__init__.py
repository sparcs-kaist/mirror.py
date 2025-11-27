import mirror.structure

from pathlib import Path
import logging

conf: mirror.structure.Config
packages: mirror.structure.Packages
confPath: Path
publishPath: Path
logger: logging.Logger
debug: bool
worker: dict[str, mirror.structure.Worker]
status: dict
__version__: str

import mirror.toolbox
import mirror.event
import mirror.sync
import mirror.logger
import mirror.command
#import mirror.socket
import mirror.config
import mirror.plugin


mirror.sync.load_default()
#mirror.plugin.plugin_loader()
