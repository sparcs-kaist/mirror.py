# mirror.py

mirror.py is a master-worker daemon that maintains local mirrors of remote package
repositories via rsync, ftpsync, lftp, bandersnatch, and local sync methods.
It runs on Linux with scheduled syncs, per-package logging, and web-accessible status
reporting.

:::{toctree}
:maxdepth: 2
:caption: Getting started
getting-started/installation
getting-started/quickstart
:::

:::{toctree}
:maxdepth: 2
:caption: User guide
guide/configuration
guide/cli
guide/state-files
guide/troubleshooting
:::

:::{toctree}
:maxdepth: 2
:caption: Sync methods
sync-methods/index
sync-methods/rsync
sync-methods/ftpsync
sync-methods/lftp
sync-methods/ubuntu
sync-methods/jigdo
sync-methods/bandersnatch
sync-methods/local
:::

:::{toctree}
:maxdepth: 2
:caption: Architecture and plugins
architecture/overview
plugins/index
plugins/example-echo
:::

:::{toctree}
:maxdepth: 2
:caption: Reference
api/index
contributing/index
:::
