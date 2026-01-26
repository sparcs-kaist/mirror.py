# Project Documentation: mirror.py

`mirror.py` is a Python-based tool designed for mirroring files and directories to a remote server. It supports various synchronization protocols (like rsync, ftpsync) and includes features for configuration management, logging, and plugin support.

## Directory Structure

```
mirror/
├── command/     # CLI command implementations (daemon, setup, crontab, worker)
├── config/      # Configuration loading, defaults, and status management
├── event/       # Event handling system with pre/post listeners
├── handler/     # (Empty) Reserved for future handler implementations
├── logger/      # Logging configuration with prompt_toolkit integration
├── plugin/      # Plugin loading mechanism
├── socket/      # Socket handling (Unix domain sockets) with master/worker/client
├── structure/   # Core data structures and type definitions
├── sync/        # Synchronization protocol implementations (rsync, ftpsync, lftp)
└── toolbox/     # Utility functions (ISO duration parsing, permissions, etc.)
```

---

## Module Breakdown

### 1. Root (`mirror/`)

-   **`__init__.py`**
    -   Initializes global variables:
        -   `conf` (Config): Global configuration object
        -   `packages` (Packages): Package container object
        -   `confPath` (Path): Configuration file path
        -   `publishPath` (Path): Publish path
        -   `logger` (logging.Logger): Main logger
        -   `debug` (bool): Debug mode flag
        -   `worker` (dict[str, Worker]): Worker dictionary
        -   `status` (dict): Status dictionary
        -   `__version__` (str): Version string
    -   Imports sub-modules (`toolbox`, `event`, `sync`, `logger`, `command`, `config`, `plugin`).
    -   Calls `mirror.sync.load_default()` to load default sync methods.

-   **`__main__.py`**
    -   Entry point for the CLI application using `click`.
    -   Version: `1.0.0-pre3`
    -   **Commands:**
        -   `setup`: Sets up the mirror environment (config, directories, systemd services).
        -   `crontab`: Generates a crontab file from the config.
            -   Options: `-u/--user` (default: root), `-c/--config` (default: /etc/mirror/config.json)
        -   `daemon`: Runs the mirror daemon.
            -   Options: `--config` (default: /etc/mirror/config.json)
        -   `worker`: Runs the worker server.
            -   Options: `--config` (default: /etc/mirror/config.json)

### 2. Command (`mirror/command/`)

-   **`__init__.py`**
    -   Exports `setup`, `daemon`, `crontab`, and `worker`.

-   **`setup.py`**
    -   **`setup()`**:
        -   Validates OS (Linux only) and user (root only).
        -   Creates directory structure (`/etc/mirror`, `/var/run/mirror`, `/var/lib/mirror`).
        -   Writes default configuration to `/etc/mirror/config.json`.
        -   Creates systemd service files:
            -   `mirror.service`: Runs `mirror daemon`
            -   `mirror-worker.service`: Runs `mirror worker`

-   **`daemon.py`**
    -   **`daemon(config)`**: Loads configuration and enters a loop. Checks if packages need syncing based on `lastsync` and `syncrate`. Calls `mirror.sync.start(package)` to initiate sync.
    -   **`check_daemon()`**: *Placeholder.*

-   **`crontab.py`**
    -   **`crontab(user, config)`**: *Placeholder* for generating crontab entries.

-   **`worker.py`**
    -   **`worker()`**: *Stub/Placeholder* for the worker process logic.
    -   *Note: The CLI expects this function to accept a `config` argument, but the current implementation takes none.*

### 3. Config (`mirror/config/`)

-   **`__init__.py`**
    -   Global path variables:
        -   `CONFIG_PATH`: Main configuration file path
        -   `STAT_DATA_PATH`: Stat data file path
        -   `STATUS_PATH`: Web status file path
        -   `SOCKET_PATH`: Unix socket path
    -   **`load(conf_path: Path)`**: Loads the main JSON configuration.
        -   Derives `STAT_DATA_PATH`, `STATUS_PATH`, and `SOCKET_PATH` from settings.
        -   Syncs current packages with the persistent stat file.
        -   Loads configuration into `mirror.conf` (Config object) and `mirror.packages` (Packages object).
        -   Calls `_load_web_status_data()` to load web status.
    -   **`_load_web_status_data()`**: Loads data for the web status page from `STATUS_PATH`.
    -   **`reload()`**: Reloads the configuration.
    -   **`generate_and_save_web_status()`**: Generates `status.json` for web display, including sync status, rates, and links.

-   **`config.py`**
    -   Defines `DEFAULT_CONFIG`: A dictionary template containing:
        -   `mirrorname`: Mirror name
        -   `settings`: Logger config, ftpsync config, webroot, logfolder, uid/gid, timezone, maintainer, plugins
        -   `packages`: Example package configuration

-   **`stat.py`**
    -   Defines `DEFAULT_STAT_DATA`: Example status data for packages with status info (errorcount, logs).

-   **`status.py`**
    -   Defines `DEFAULT_STATUS`: A dictionary template for the web status file, including example mirrors (Linux Kernel, AlmaLinux, OpenWRT, RPM Fusion, TinyCore Linux).

### 4. Sync (`mirror/sync/`)

-   **`__init__.py`**
    -   `BasicMethodPath`: Path to sync modules directory
    -   `methods`: List of available sync method names (auto-detected from .py files)
    -   `now`: List for tracking current sync operations
    -   **`Options` (class)**: Dynamic options container for sync methods.
    -   **`loader(methodPath: Path)`**: Dynamically loads python modules from a directory into `mirror.sync`.
    -   **`load_default()`**: Loads sync modules from the current directory.
    -   **`start(package)`**: Initiates a sync for a package. *Currently a pass/placeholder.*
    -   **`execute(package, logger, method)`**: Executes the `execute` function of a specified sync method.
    -   **`_execute(package, logger, method)`**: Execute with threading. *Returns False (placeholder).*
    -   **`setexecuser(uid, gid)`**: Helper to set process UID/GID (used in `preexec_fn`).

-   **`rsync.py`**
    -   Module metadata:
        -   `module`: "sync"
        -   `name`: "rsync"
        -   `required`: ["rsync", "ssh"]
        -   `options`: ffts (bool), fftsfile (str), auth (bool), userid (str), passwd (str)
    -   **`setup()`**: Validates that required commands exist.
    -   **`execute(package)`**: Main entry point for rsync. Checks status, creates a logger, and prepares for sync.
    -   **`rsync(logger, pkgid, src, dst, auth, userid, passwd)`**: Constructs and runs the `rsync` command with options for logging, exclusion, and authentication.
    -   **`ffts(package, logger)`**: Runs a "File Transfer Timestamp" check using rsync dry-run to see if updates are needed.

-   **`ftpsync.py`**
    -   **`ftpsync(package)`**: Sets up a temporary directory, writes the `ftpsync` script and config, and executes it.
    -   **`_setup(path, package)`**: Prepares the `bin` and `etc` directories for the script.
    -   **`_config(package)`**: Generates the configuration content for the `ftpsync` script.
    -   **`_test()`**: Test function for ftpsync.

-   **`ftpsync_script.py`**
    -   Contains the raw bash script source code (`ARCHVSYNC_SCRIPT`) for the `ftpsync` tool.

-   **`lftp.py`**
    -   **`ftp(package)`**: *Partial implementation* of syncing using `lftp` command.

-   **`bandersnatch.py`**
    -   Contains imports but no implementation logic yet.

-   **`local.py`**
    -   *Empty.*

### 5. Structure (`mirror/structure/`)

Defines the core data models and types used throughout the application.

-   **`Options` (dataclass)**
    -   Base class with `to_dict()` and `to_json()` methods.

-   **`SyncExecuter` (class)**
    -   Handles the execution of synchronization tasks.
    -   **`sync()`**: *Placeholder* for the sync logic.

-   **`Worker` (class)**
    -   Represents a worker process responsible for a specific package.
    -   Fields: `package`, `logger`, `sync` (SyncExecuter)

-   **`PackageSettings` (dataclass)**
    -   Stores settings specific to a package.
    -   Fields: `hidden` (bool), `src` (str), `dst` (str), `options` (dict)

-   **`Package` (dataclass)**
    -   Represents a single mirror package and its state.
    -   **Nested classes**:
        -   `Link`: Represents a link with `rel` and `href`.
        -   `StatusInfo`: Contains `lastsynclog`, `lastsuccesslog`, `errorcount`.
    -   **Fields**: `pkgid`, `name`, `status`, `href`, `synctype`, `syncrate`, `link`, `settings`, `lastsync`, `errorcount`, `disabled`
    -   **Methods**: `from_dict`, `set_status`, `to_dict`, `to_json`, `is_syncing`, `is_disabled`, `_path_check`.

-   **`Sync` (class)**
    -   Structure for sync operations.
    -   Fields: `pkgid`, `synctype`, `logPath`, `options`, `settings`

-   **`Packages` (dataclass)**
    -   Container for multiple `Package` objects.
    -   Supports dict-like access (`__getitem__`, `__iter__`, `__len__`).
    -   **Methods**: `items`, `keys`, `values`, `to_dict`.

-   **`Config` (dataclass)**
    -   Global application configuration.
    -   **Nested class**:
        -   `FTPSync`: ftpsync-specific settings (maintainer, sponsor, country, location, throughput, include, exclude)
    -   **Fields**: `name`, `hostname`, `lastsettingmodified`, `logfolder`, `webroot`, `ftpsync`, `uid`, `gid`, `maintainer`, `localtimezone`, `logger`, `plugins`
    -   **Methods**: `load_from_dict`, `save`, `to_dict`, `to_json`, `_path_check`.

-   **`Packet` (class)**
    -   Structure for IPC data packets.
    -   **Fields**: `mode`, `sender`, `to`, `command`.
    -   **Methods**: `load`, `to_dict`, `to_json`.

### 6. Logger (`mirror/logger/`)

-   **`__init__.py`**
    -   **`PromptHandler` (class)**: Custom logging handler using `prompt_toolkit` for formatted output.
    -   **Constants**:
        -   `DEFAULT_LEVEL`: "INFO"
        -   `DEFAULT_PACKAGE_LEVEL`: "ERROR"
        -   `DEFAULT_FORMAT`: "[%(asctime)s] %(levelname)s # %(message)s"
        -   `DEFAULT_PACKAGE_FORMAT`: "[%(asctime)s][{package}] %(levelname)s # %(message)s"
        -   `DEFAULT_FILE_FORMAT`: File path formatting template
    -   **`_time_formatting(line, usetime, pkgid)`**: Formats time placeholders in log file paths.
    -   **`create_logger(package, start_time)`**: Creates a file-based logger for a specific package sync session.
    -   **`setup_logger()`**: Configures the main application logger with file and console handlers.

### 7. Toolbox (`mirror/toolbox/`)

-   **`__init__.py`**
    -   **`iso_duration_parser(iso8601)`**: Parses ISO 8601 duration strings (e.g., "P1DT1H") into seconds.
        -   Returns -1 for "PUSH" (special case)
        -   Supports days, hours, minutes, seconds
    -   **`iso_duration_maker(duration)`**: Converts seconds back to ISO 8601 duration string.
        -   Returns "PUSH" for -1
        -   Max duration: 31 days
    -   **`set_rsync_user(url, user)`**: Adds user to rsync URL.
    -   **`checkPermission()`**: Checks if the user has root or sudo privileges.
    -   **`is_command_exists(command)`**: Checks if a shell command is available.

### 8. Plugin (`mirror/plugin/`)

-   **`__init__.py`**
    -   `loadable_module`: List of modules that can be extended ("sync", "logger", "plugin")
    -   **`plugin_loader()`**: Loads plugins defined in the configuration.
        -   Validates plugin attributes: `setup`, `module`, `name`, `entry`
        -   Dynamically loads and registers plugins to their target modules

### 9. Event (`mirror/event/`)

-   **`__init__.py`**
    -   `events`: Global event list
    -   **`BasicEvent` (class)**: A simple event class supporting pre/post listeners and threaded execution.
        -   **Fields**: `pre_listeners`, `post_listeners`, `threads`
        -   **Methods**:
            -   `_call(listeners, *args, **kwargs)`: Executes listeners in separate threads
            -   `add_listener(listener, pre)`: Adds a listener (pre or post)
            -   `remove_listener(listener, pre)`: Removes a listener
            -   `wait()`: Waits for all threads to complete

### 10. Socket (`mirror/socket/`)

-   **`__init__.py`**
    -   **`BaseHandler` (class)**:
        -   Base class for socket handlers with length-prefixed JSON protocol.
        -   **`handle(connection)`**: Reads JSON packets, executes commands dynamically, and sends responses.
        -   Response format: `{"status": int, "result": any}` or `{"status": int, "error": str, "traceback": str}`
    -   **`MirrorSocket` (class)**:
        -   Manages a Unix domain socket server for IPC.
        -   **Roles**: 'master' or 'worker'
        -   **Methods**: `start()`, `stop()`, `_accept_loop()`
        -   Supports threading for concurrent connections

-   **`client.py`**
    -   **`MirrorClient` (class)**:
        -   Client for Unix domain socket RPC communication.
        -   **`_send_request(command, kwargs)`**: Sends a command and receives response.
        -   **`__getattr__(name)`**: Dynamic method generation for RPC calls.
            -   Example: `client.start_sync(id=1)` -> `_send_request('start_sync', {'id': 1})`

-   **`master.py`**
    -   **`MasterHandler` (class)**:
        -   Handles requests for the master process.
        -   Commands: `ping` (returns "pong")
    -   **`MasterClient` (class)**:
        -   Client interface for interacting with the Master process.
        -   Used by the Worker process.

-   **`worker.py`**
    -   **`WorkerHandler` (class)**:
        -   Handles requests for the worker process.
        -   Commands: `start_sync(id, command)` - Starts sync for a package
    -   **`WorkerClient` (class)**:
        -   Client interface for interacting with the Worker process.
        -   Used by the Master process.
