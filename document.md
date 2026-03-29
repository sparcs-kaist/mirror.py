# Project Documentation: mirror.py

`mirror.py` is a Python-based tool designed for mirroring files and directories to a remote server. It supports various synchronization protocols (like rsync, ftpsync) and includes features for configuration management, logging, and plugin support.

## Directory Structure

```
mirror/
├── command/     # CLI command implementations (daemon, setup, crontab, worker)
├── config/      # Configuration loading, defaults, and status management
├── event/       # Pub/Sub event system with priority-based listeners
├── handler/     # (Empty) Reserved for future handler implementations
├── logger/      # Logging configuration with prompt_toolkit integration
├── plugin/      # Plugin loading mechanism (currently disabled)
├── socket/      # Unix domain socket IPC with master/worker server/client
├── structure/   # Core data structures and type definitions
├── sync/        # Synchronization protocol implementations (rsync, ftpsync, lftp)
├── toolbox/     # Utility functions (ISO duration parsing, permissions, etc.)
└── worker/      # Worker process management (Job lifecycle, subprocess control)
```

---

## Module Breakdown

### 1. Root (`mirror/`)

-   **`__init__.py`**
    -   **Role**: Initializes global variables and imports sub-modules, setting up the core environment for the `mirror` application.
    -   **Global Variables**:
        -   `conf` (Config): Global configuration object.
        -   `packages` (Packages): Container for all managed mirror packages.
        -   `confPath` (Path): Path to the main configuration file.
        -   `publishPath` (Path): Base path for mirrored content.
        -   `log` (logging.Logger): Main application logger.
        -   `worker` (dict[str, Worker]): Dictionary of active worker instances.
        -   `status` (dict): Web status data.
        -   `debug` (bool): Flag indicating if debug mode is active (default: `False`).
        -   `exit` (bool): Flag to signal graceful shutdown (default: `False`).
        -   `STATE_PATH` (Path): Persistent state data directory (`/var/lib/mirror/`).
        -   `RUN_PATH` (Path): Runtime data directory (`/var/run/mirror/`).
        -   `__version__` (str): Version string (`1.0.0-pre3`).
    -   **Initialization**: Calls `mirror.sync.load_default()` to load default synchronization methods on import.

-   **`__main__.py`**
    -   **Role**: The primary entry point for the `mirror` CLI application, defining command-line interfaces using the `click` library.
    -   **Commands**:
        -   **`setup()`**:
            -   **Description**: Configures the `mirror` environment, including creating necessary directories and systemd service files.
            -   **Dependencies**: Calls `mirror.command.setup()`.
        -   **`crontab(user, config)`**:
            -   **Description**: Generates crontab entries based on the mirror configuration.
            -   **Options**:
                -   `-u/--user`: User for whom the crontab is generated (default: `root`).
                -   `-c/--config`: Path to the configuration file (default: `/etc/mirror/config.json`).
            -   **Dependencies**: Calls `mirror.command.crontab()`.
        -   **`daemon(config)`**:
            -   **Description**: Runs the `mirror` daemon process, responsible for monitoring and initiating sync operations.
            -   **Options**:
                -   `--config`: Path to the configuration file (default: `/etc/mirror/config.json`).
            -   **Dependencies**: Calls `mirror.command.daemon()`.
        -   **`worker(config)`**:
            -   **Description**: Runs the worker server process that executes actual synchronization tasks.
            -   **Options**:
                -   `--config`: Path to the configuration file (default: `/etc/mirror/config.json`).
            -   **Dependencies**: Calls `mirror.command.worker()`.

### 2. Command (`mirror/command/`)

-   **`__init__.py`**
    -   **Role**: Serves as a convenient module to import and expose the main CLI command functions from its sub-modules.
    -   **Exports**: `setup`, `daemon`, `crontab`, and `worker`.

-   **`setup.py`**
    -   **Role**: Handles the initial setup and provisioning of the `mirror` application on a system.
    -   **`setup()`**:
        -   **Description**: Performs environment validation (Linux OS and root user), creates essential directory structures (`/etc/mirror`, `/var/run/mirror`, `/var/lib/mirror`), writes a default configuration from `DEFAULT_CONFIG`, and generates systemd service unit files.
        -   **Dependencies**: `os`, `json`, `platform`, `pathlib.Path`, `mirror.config.config.DEFAULT_CONFIG`.
        -   **Files Generated**:
            -   `/etc/mirror/config.json`: Default configuration.
            -   `/etc/systemd/system/mirror.service`: Systemd unit for the daemon.
            -   `/etc/systemd/system/mirror-worker.service`: Systemd unit for the worker.

-   **`daemon.py`**
    -   **Role**: Implements the main daemon logic for `mirror`, overseeing synchronization tasks and managing worker processes.
    -   **`daemon(config)`**:
        -   **Description**: Loads the specified configuration via `mirror.config.load()`, initializes the logger, writes a PID file to `mirror.RUN_PATH`, starts the master socket server via `mirror.socket.init("master")`, fires `MASTER.INIT.PRE` and `MASTER.INIT.POST` events, and enters a continuous loop. In this loop, it:
            1. Skips disabled packages.
            2. Detects orphaned SYNC states (marked as syncing but no worker found) and sets them to ERROR.
            3. Detects running workers with non-SYNC status and corrects the status.
            4. Triggers sync when `time.time() - lastsync > syncrate`.
            5. Retries ERROR packages after `errorcontinuetime` has elapsed.
        -   **Signal Handling**: Registers SIGINT and SIGTERM handlers for graceful shutdown (stops socket, removes PID file).
        -   **Dependencies**: `mirror.config`, `mirror.logger`, `mirror.socket`, `mirror.sync`, `mirror.event`, `os`, `sys`, `time`, `signal`, `pathlib.Path`.

-   **`crontab.py`**
    -   **Role**: Manages the generation of crontab entries for scheduled mirror operations.
    -   **`crontab(user, config)`**:
        -   **Description**: (Placeholder) Designed to generate and potentially install crontab entries.

-   **`worker.py`**
    -   **Role**: Implements the worker process that listens for and executes synchronization commands from the master daemon.
    -   **`worker(config, socket_path=None)`**:
        -   **Description**: Loads log level from the config file, configures basic logging, starts the worker socket server via `mirror.socket.init("worker")`, registers signal handlers for graceful shutdown, and enters a keep-alive loop.
        -   **Dependencies**: `json`, `logging`, `sys`, `time`, `signal`, `pathlib.Path`, `mirror.socket`.

### 3. Config (`mirror/config/`)

-   **`__init__.py`**
    -   **Role**: Manages the loading, reloading, and storage of application configuration and status data. Automatically saves status on package updates via event listener.
    -   **Global Path Variables**:
        -   `CONFIG_PATH`: Resolved path to the main configuration file.
        -   `STAT_DATA_PATH`: Resolved path to the persistent statistics data file.
        -   `STATUS_PATH`: Resolved path to the web status JSON file.
        -   `SOCKET_PATH`: Resolved path for the Unix domain socket.
    -   **Functions**:
        -   **`load(conf_path: Path)`**: Reads the main JSON config file, synchronizes it with the persistent stat file (removing packages not in config, adding new packages with default status, preserving existing status for known packages), constructs `mirror.conf` and `mirror.packages`, saves merged stat data, and loads web status.
        -   **`_load_web_status_data()`**: Loads the web status JSON from `STATUS_PATH` into `mirror.status`.
        -   **`reload()`**: Re-applies configuration by calling `load()` with the stored `CONFIG_PATH`.
        -   **`generate_and_save_web_status()`**: Compiles current sync status, rates, and package information into a JSON file for web display.
        -   **`save_stat_data()`**: Saves the current package states (including status and statusinfo) to the persistent stat file.
    -   **Event Listener**:
        -   **`_on_package_status_update()`**: Registered on `MASTER.PACKAGE_STATUS_UPDATE.POST`. Automatically calls `generate_and_save_web_status()` and `save_stat_data()` when any package status changes.
    -   **Dependencies**: `json`, `time`, `pathlib.Path`, `mirror.structure`, `mirror.toolbox`, `mirror.event`.

-   **`config.py`**
    -   **Role**: Defines the default structure and values for the main application configuration.
    -   **`DEFAULT_CONFIG`**:
        -   **Description**: A dictionary template outlining all configurable settings, including `mirrorname`, various `settings` (logger, ftpsync, webroot, logfolder, UID/GID, timezone, maintainer, errorcontinuetime, plugins), and a sample `packages` configuration.

-   **`stat.py`**
    -   **Role**: Defines the default structure for package statistics data.
    -   **`DEFAULT_STAT_DATA`**:
        -   **Description**: A dictionary containing template fields for package status information such as `errorcount`, `lasterrorlog`, `lastsuccesslog`, `runninglog`.

-   **`status.py`**
    -   **Role**: Defines the default structure and example content for the web status JSON file.
    -   **`DEFAULT_STATUS`**:
        -   **Description**: A dictionary template providing example mirror configurations (e.g., Linux Kernel, AlmaLinux, OpenWRT, RPM Fusion, TinyCore Linux) to illustrate the expected format for web status data.

### 4. Event (`mirror/event/`)

-   **`__init__.py`**
    -   **Role**: Implements a Pub/Sub event system with priority-based listener ordering and thread-pool execution.
    -   **`EventManager` (class)**:
        -   **Description**: Central event management system. Uses a `ThreadPoolExecutor` (default 20 workers) to execute listeners asynchronously.
        -   **Fields**: `_listeners` (dict mapping event names to sorted lists of `(priority, callable)` tuples), `_lock` (threading.Lock), `_executor` (ThreadPoolExecutor).
        -   **Methods**:
            -   `on(event_name, listener, priority=50)`: Registers a listener with a given priority. Lower number = higher priority (executes earlier). Prevents duplicate registrations. Listeners are kept sorted by priority.
            -   `once(event_name, listener, priority=50)`: Registers a listener that automatically unregisters itself after one execution.
            -   `off(event_name, listener)`: Unregisters a listener.
            -   `post_event(event_name, wait, *args, **kwargs)`: Fires an event, executing all registered listeners via the thread pool. If `wait=True`, blocks until all listeners complete using `concurrent.futures.wait`.
            -   `shutdown(wait=True)`: Shuts down the thread pool.
    -   **Global Singleton**: `_manager = EventManager()`.
    -   **Public API Functions** (delegate to `_manager`):
        -   `on(event_name, listener=None, priority=50)`: Can be used as a decorator when `listener` is None.
        -   `once(event_name, listener, priority=50)`
        -   `off(event_name, listener)`
        -   `post_event(event_name, *args, **kwargs)`: Extracts `wait` from kwargs (default `False`).
    -   **Decorator**:
        -   `@listener(event_name, priority=50)`: Decorator to register a function as an event listener at import time.
    -   **Dependencies**: `threading`, `concurrent.futures`, `logging`.

### 5. Handler (`mirror/handler/`)

-   **`__init__.py`**
    -   **Role**: (Currently empty) Reserved for future handler implementations.

-   **`sigint.py`**
    -   **Role**: (Currently empty) Reserved for SIGINT signal handling.

### 6. Logger (`mirror/logger/`)

-   **`__init__.py`**
    -   **Role**: Re-exports all public symbols from `handler.py` and `core.py`.
    -   **Exports**: `PromptHandler`, `DynamicGzipRotatingFileHandler`, `psession`, `input`, `logger`, `DEFAULT_LEVEL`, `DEFAULT_PACKAGE_LEVEL`, `DEFAULT_FORMAT`, `DEFAULT_PACKAGE_FORMAT`, `DEFAULT_FILE_FORMAT`, `compress_file`, `create_logger`, `close_logger`, `setup_logger`, `get`.

-   **`handler.py`**
    -   **Role**: Implementation of custom logging handlers and utility functions.
    -   **Functions**:
        -   **`_time_formatting(line, usetime, pkgid)`**: Formats dynamic placeholders in log file paths based on time and package ID. Automatically provides zero-padding (e.g., `{month}` becomes `02`) for year, month, day, hour, minute, second, microsecond.
        -   **`compress_file(filepath)`**: Compresses a file with gzip and removes the original. Returns the path to the compressed file or `None` on failure.
    -   **Classes**:
        -   **`PromptHandler`**: A custom `logging.StreamHandler` that outputs log records via `prompt_toolkit.print_formatted_text` with ANSI formatting.
        -   **`DynamicGzipRotatingFileHandler`**: Extends `logging.FileHandler`. On each `emit()`, resolves a new path from time-based templates. If the path differs from the current file, performs rotation: closes the old file, optionally compresses it via `compress_file()`, and opens the new path.
    -   **Dependencies**: `prompt_toolkit`, `logging`, `gzip`, `shutil`, `os`, `time`, `datetime`, `pathlib.Path`.

-   **`core.py`**
    -   **Role**: Core logging setup and package logger lifecycle management.
    -   **Module State**:
        -   `psession`: `PromptSession` instance.
        -   `input`: Bound to `psession.prompt` for interactive input.
        -   `logger`: Root `mirror` logger.
        -   `basePath`: Base path for log files (set during `setup_logger()`).
    -   **Defaults**: `DEFAULT_LEVEL`, `DEFAULT_PACKAGE_LEVEL`, `DEFAULT_FORMAT`, `DEFAULT_PACKAGE_FORMAT`, `DEFAULT_FILE_FORMAT`, `DEFAULT_PACKAGE_FILE_FORMAT`.
    -   **Functions**:
        -   **`create_logger(name, start_time)`**: Creates a dedicated logger for a package sync session. Adds a `PromptHandler` for console output and a `FileHandler` for file logging. Uses the `packagefileformat` config for path resolution.
        -   **`close_logger(pkg_logger, compress=None)`**: Closes all handlers for a package logger. If compression is enabled (default from config), compresses the log file via `compress_file()`. Returns the path to the log file.
        -   **`setup_logger()`**: Configures the main `mirror` logger with console formatting and a `DynamicGzipRotatingFileHandler` for automatic time-based log rotation. Sets `mirror.log` to the configured logger.
        -   **`get(pkgid)`**: Returns the logger for a specific package (`mirror.package.{pkgid}`).
    -   **Dependencies**: `logging`, `datetime`, `pathlib.Path`, `prompt_toolkit`, `mirror.conf`.

### 7. Plugin (`mirror/plugin/`)

-   **`__init__.py`**
    -   **Role**: Manages the dynamic loading and registration of external plugins to extend `mirror`'s functionality. Currently disabled in `mirror/__init__.py`.
    -   **Global Variables**:
        -   `loadable_module`: A list of module types that can be extended by plugins (`"sync"`, `"logger"`, `"plugin"`).
    -   **Functions**:
        -   **`_load_module_from_path(name, path)`**: Loads a Python module from a file path using `importlib.util`.
        -   **`plugin_loader()`**: Iterates through `mirror.conf.plugins`, validates required attributes (`setup`, `module`, `name`, `entry`), validates that `module` is in `loadable_module`, dynamically imports the plugin as `mirror.{module}.{name}`, attaches it to the appropriate sub-module, and calls `setup()`.
    -   **Dependencies**: `importlib.util`, `pathlib.Path`, `mirror`.

### 8. Socket (`mirror/socket/`)

-   **`__init__.py`**
    -   **Role**: Provides the foundational classes for inter-process communication (IPC) using Unix domain sockets. Implements a length-prefixed JSON protocol with handshake, RPC command dispatch, and bi-directional asynchronous notifications.
    -   **Protocol Constants**:
        -   `PROTOCOL_VERSION = 1`
        -   `APP_NAME = "mirror.py"`
        -   `HANDSHAKE_TIMEOUT = 5.0`
    -   **Global Variables**:
        -   `master`: Global instance of `MasterServer` or `None`.
        -   `worker`: Global instance of `WorkerServer`, `WorkerClient`, or `None`.
    -   **`HandshakeInfo` (dataclass)**:
        -   **Fields**: `app_name`, `app_version`, `protocol_version`, `is_server`, `role`.
        -   **Methods**: `to_dict()`, `from_dict()`.
    -   **`@expose(cmd_name=None)` (decorator)**:
        -   **Description**: Marks a method as an RPC handler. Methods decorated with `@expose` are automatically registered by `BaseServer._auto_register_handlers()`. If `cmd_name` is not provided, the method name is used.
    -   **`BaseServer` (class)**:
        -   **Description**: Base class for socket servers. Manages connection acceptance, handshake validation, threaded client handling, and command dispatch via registered handlers.
        -   **Key Methods**:
            -   `_auto_register_handlers()`: Scans instance methods for `@expose` decorator and registers them.
            -   `register_handler(command, handler)`: Manually register a command handler.
            -   `_perform_handshake(conn)`: Validates `app_name` and `protocol_version`.
            -   `_handle_connection(conn, client_info)`: Receives RPC requests, dispatches to handlers, sends responses.
            -   `broadcast(data)`: Sends a non-RPC message to all connected clients.
            -   `client_count` (property): Number of connected clients.
            -   `start()`: Binds socket, sets permissions to `0o600`, starts accept loop thread.
            -   `stop()`: Stops server, closes socket, removes socket file.
        -   **Dependencies**: `socket`, `threading`, `json`, `struct`, `traceback`.
    -   **`BaseClient` (class)**:
        -   **Description**: Base class for socket clients. Features a background listener thread for asynchronous notifications and a response queue for synchronous RPC.
        -   **Key Methods**:
            -   `connect()`: Connects, performs handshake, starts listener thread.
            -   `disconnect()`: Disconnects and cleans up.
            -   `send_command(command, **kwargs)`: Sends an RPC command and waits for response (30s timeout).
            -   `handle_notification(data)`: Hook for subclasses to process server notifications.
            -   `_listen_loop()`: Background thread that dispatches notifications vs RPC responses.
            -   `__getattr__`: Allows calling commands as methods (e.g., `client.ping()`).
        -   **Context Manager**: Supports `with` statement via `__enter__`/`__exit__`.
        -   **Dependencies**: `socket`, `threading`, `json`, `struct`, `queue`.
    -   **Module Functions**:
        -   **`init(role, **kwargs)`**: Factory function. Creates and starts servers or connects clients based on role:
            -   `"master"`: Creates `MasterServer`, starts it, attempts to connect to existing worker.
            -   `"worker"`: Creates `WorkerServer`, starts it.
            -   `"client"` / `"master_client"`: Creates and connects `MasterClient`.
            -   `"worker_client"`: Creates and connects `WorkerClient`.
        -   **`stop()`**: Stops master server and disconnects worker client.

-   **`master.py`**
    -   **Role**: Defines the specialized server and client for master daemon communication.
    -   **Constants**: `MASTER_SOCKET_PATH = mirror.RUN_PATH / "master.sock"`.
    -   **`MasterServer` (class)**:
        -   **Description**: Extends `BaseServer` with master-specific RPC handlers.
        -   **Exposed Commands**:
            -   `ping`: Health check, returns `{"message": "pong"}`.
            -   `status`: Returns server running state, role, version, socket path.
            -   `list_packages`: (TODO) List all packages.
            -   `start_sync`: (TODO) Start sync for a package.
            -   `stop_sync`: (TODO) Stop sync for a package.
            -   `get_package`: (TODO) Get package details.
    -   **`MasterClient` (class)**:
        -   **Description**: Extends `BaseClient` with typed methods for each master command.
        -   **Methods**: `ping()`, `status()`, `list_packages()`, `start_sync(package_id)`, `stop_sync(package_id)`, `get_package(package_id)`.
    -   **Module Convenience Functions**: `ping()`, `status()`, `list_packages()`, `start_sync()`, `stop_sync()`, `get_package()`, `get_master_client()`, `is_master_running()`.

-   **`worker.py`**
    -   **Role**: Defines the specialized server and client for worker process communication.
    -   **Constants**: `WORKER_SOCKET_PATH = mirror.RUN_PATH / "worker.sock"`.
    -   **`WorkerServer` (class)**:
        -   **Description**: Extends `BaseServer` to handle worker-specific tasks and job management.
        -   **Methods**:
            -   `send_finished_notification(job_id, success, returncode)`: Broadcasts a `job_finished` event to all connected clients. Raises `ConnectionError` if no clients are connected.
        -   **Exposed Commands**:
            -   `ping`: Health check.
            -   `status`: Returns server info and list of active job IDs.
            -   `execute_command(job_id, commandline, env, sync_method, uid, gid, nice, log_path)`: Creates and starts a new `Job` via `mirror.worker.process`. Returns job ID, PID, and status.
            -   `stop_command(job_id=None)`: Stops a specific job or all jobs.
            -   `get_progress(job_id=None)`: Returns running state and info for a specific job or all jobs.
    -   **`WorkerClient` (class)**:
        -   **Description**: Extends `BaseClient` to manage worker tasks from the master process.
        -   **Methods**:
            -   `handle_notification(data)`: Overridden to process `job_finished` events, calling `mirror.sync.on_sync_done(job_id, success, returncode)`.
            -   `ping()`, `status()`, `execute_command(...)`, `stop_command(job_id)`, `get_progress(job_id)`.
    -   **Module Convenience Functions**: `ping()`, `status()`, `stop_command()`, `get_progress()`, `execute_command()`, `is_worker_running(job_id=None)`.

### 9. Structure (`mirror/structure/`)

-   **Role**: Defines the core data models and type definitions used throughout the `mirror` application, ensuring data consistency and clear interfaces.

-   **`Options` (dataclass)**
    -   **Description**: A base dataclass that provides common utility methods.
    -   **Methods**: `get(key, default=None)` (via `getattr`), `to_dict()`, `to_json()`.

-   **`SyncExecuter` (class)**
    -   **Description**: (Placeholder) Intended to encapsulate the logic for executing synchronization tasks.
    -   **Methods**: `sync()`: (Placeholder).

-   **`Worker` (class)**
    -   **Description**: Represents a worker instance with its assigned package and associated logging/synchronization objects.
    -   **Fields**: `package`, `logger`, `sync` (SyncExecuter).

-   **`PackageSettings` (dataclass, extends Options)**
    -   **Description**: Stores the specific configuration settings for an individual mirror package.
    -   **Fields**: `hidden` (bool), `src` (str), `dst` (str), `options` (dict, default `{}`).
    -   **Methods**: `from_dict(data)`: Filters input to known fields only.

-   **`Package` (dataclass)**
    -   **Description**: Represents a single mirror package, including its configuration, status, and metadata.
    -   **Nested Classes**:
        -   `Link` (dataclass, extends Options): Defines a hyperlink with `rel` (relation) and `href` (URL).
        -   `StatusInfo` (dataclass, extends Options): Holds status-related metrics.
            -   **Fields**: `lasterrorlog` (Optional[str]), `lastsuccesslog` (Optional[str]), `runninglog` (Optional[str]), `errorcount` (int, default 0).
            -   **Methods**: `from_dict(data)`: Filters input to known fields.
    -   **Fields**: `pkgid` (str), `name` (str), `status` (str), `href` (str), `synctype` (str), `syncrate` (int, in seconds), `link` (list[Link]), `settings` (PackageSettings), `lastsync` (float, default 0.0), `disabled` (bool, default False), `statusinfo` (StatusInfo, default empty).
    -   **Methods**:
        -   `from_dict(config)`: Factory method. Validates `synctype` against `mirror.sync.methods`, parses `syncrate` via `iso_duration_parser`, handles nested `status` object (containing `status` string and `statusinfo` dict), extracts `lastsync` from statusinfo.
        -   `set_status(status, logfile=None)`: Sets package status (one of `ACTIVE`, `SYNC`, `ERROR`, `UNKNOWN`). Fires `MASTER.PACKAGE_STATUS_UPDATE.PRE` and `MASTER.PACKAGE_STATUS_UPDATE.POST` events. Increments `errorcount` on ERROR. Updates `lastsuccesslog` or `lasterrorlog` based on status and logfile parameter.
        -   `to_dict()`: Serializes package. Converts `pkgid` to `id`, wraps `status` and `statusinfo` into a nested object.
        -   `to_json()`, `is_syncing()`, `is_disabled()`, `_path_check(path)`.

-   **`Sync` (class)**
    -   **Description**: Defines the structure for a synchronization operation request.
    -   **Fields**: `pkgid`, `synctype`, `logPath`, `options`, `settings`.

-   **`Packages` (dataclass, extends Options)**
    -   **Description**: A container class that manages a collection of `Package` objects. Stores packages as dynamic attributes, provides dictionary-like access.
    -   **Methods**: `get(key)`, `__getitem__(key)`, `__iter__()`, `__len__()`, `items()`, `keys()`, `values()`, `to_dict()`.

-   **`Config` (dataclass)**
    -   **Description**: The main application-wide configuration object, holding global settings and nested configurations.
    -   **Nested Class**:
        -   `FTPSync` (dataclass, extends Options): Holds FTPSync-specific configuration (`maintainer`, `sponsor`, `country`, `location`, `throughput`, `include`, `exclude`).
    -   **Fields**: `name` (str), `hostname` (str), `lastsettingmodified` (int), `errorcontinuetime` (int), `logfolder` (Path), `webroot` (Path), `ftpsync` (FTPSync), `uid` (int), `gid` (int), `maintainer` (dict), `localtimezone` (str), `logger` (dict), `plugins` (list[str]).
    -   **Methods**: `load_from_dict(config)`, `save()`, `to_dict()`, `to_json()`, `_path_check(path)`.

-   **`Packet` (class)**
    -   **Description**: Defines the structure for data packets used in inter-process communication (IPC) via sockets.
    -   **Fields**: `mode`, `sender`, `to`, `command`.
    -   **Methods**: `load(data)`, `to_dict()`, `to_json()`.

### 10. Sync (`mirror/sync/`)

-   **Role**: Provides the framework and implementations for various file synchronization methods. Uses a dynamic module loading system and delegates actual command execution to the Worker server.

-   **`__init__.py`**
    -   **Global Variables**:
        -   `BasicMethodPath`: Path to the directory containing built-in sync method modules.
        -   `methods`: A list of loaded synchronization method names (populated by `loader()`).
    -   **Functions**:
        -   **`loader(methodPath: Path)`**: Dynamically loads Python modules from a specified directory into the `mirror.sync` namespace. Skips files starting with `_` and modules where `_LOAD` is `False`.
        -   **`load_default()`**: Loads the default sync modules from `BasicMethodPath`.
        -   **`get_module(method)`**: Returns the loaded sync module by name.
        -   **`start(package, trigger="auto")`**: Initiates a synchronization for a package. Creates a package logger via `mirror.logger.create_logger()`, sets status to `SYNC`, and launches the sync module's `execute()` in a daemon Thread.
        -   **`on_sync_done(pkgid, success, returncode)`**: Called when a sync job completes (via WorkerClient notification). Logs the result, closes the package logger, and updates the package status to `ACTIVE` or `ERROR`.
    -   **Protocol**: `execute(package, logger)` - Stub signature that sync modules must implement.
    -   **Dependencies**: `time`, `logging`, `importlib.util`, `threading`, `pathlib.Path`, `mirror.logger`, `mirror.structure`.

-   **`rsync.py`**
    -   **Role**: Implements file synchronization using the `rsync` command-line tool. Delegates command execution to the Worker server.
    -   **Module Metadata**: `module = "sync"`, `name = "rsync"`.
    -   **Functions**:
        -   **`setup(path, package)`**: (Placeholder) For future setup logic.
        -   **`execute(package, pkg_logger)`**: Main entry point. Extracts settings (src, dst, user, password), optionally runs FFTS check, generates rsync command, and delegates execution to `mirror.socket.worker.execute_command()`.
        -   **`rsync(logger, pkgid, src, dst, user, password)`**: Constructs the rsync command list and environment dict. Command flags: `-vrlptDSH --exclude=*.~tmp~ --delete-delay --delay-updates`.
        -   **`ffts(package, pkg_logger)`**: Performs a Full File Time List check using `rsync --dry-run` to determine if any files need updating. Returns `True` if sync is needed, `False` if up to date.
    -   **Dependencies**: `subprocess`, `os`, `time`, `logging`, `pathlib.Path`, `mirror.socket.worker`.

-   **`ftpsync.py`**
    -   **Role**: Implements Debian-style FTP synchronization using the `archvsync` ftpsync scripts. Delegates command execution to the Worker server.
    -   **Functions**:
        -   **`setup(path, package)`**: (Placeholder).
        -   **`setup_ftpsync(path, package)`**: Sets up the ftpsync environment: creates `bin/` and `etc/` directories, fetches archvsync scripts (via git clone or base64 fallback from `_ftpsync_script.py`), copies scripts to `bin/`, writes config to `etc/ftpsync.conf`.
        -   **`execute(package, logger)`**: Creates a temporary directory, calls `setup_ftpsync()`, and delegates the ftpsync command to the Worker via `WorkerClient`.
        -   **`ftpsync(package)`**: (Legacy) Direct execution path without Worker delegation.
        -   **`_config(package)`**: Generates ftpsync configuration file content from package settings.
        -   **`_check_git()`**: Checks if git is available.
        -   **`_clone_archvsync(path)`**: Clones the archvsync repository.
        -   **`_extract_archvsync(path)`**: Extracts archvsync from base64-encoded tar.gz with hash verification.
    -   **Dependencies**: `tempfile`, `subprocess`, `shutil`, `tarfile`, `io`, `base64`, `hashlib`, `pathlib.Path`, `mirror.socket.worker`.

-   **`lftp.py`**
    -   **Role**: Synchronization using the `lftp` command-line tool. Currently disabled (`_LOAD = False`).
    -   **Module Metadata**: `module = "sync"`, `name = "lftp"`, `_LOAD = False`.
    -   **Functions**:
        -   **`execute(package, pkg_logger)`**: Constructs an lftp mirror command and delegates to the Worker via `WorkerClient`.
        -   **`ftp(package)`**: (Legacy placeholder).
    -   **Dependencies**: `mirror.socket.worker`, `os`, `time`, `logging`, `pathlib.Path`.

-   **`bandersnatch.py`**
    -   **Role**: Synchronization using the `bandersnatch` Python package mirroring tool. Currently disabled (`_LOAD = False`).
    -   **Module Metadata**: `module = "sync"`, `name = "bandersnatch"`, `_LOAD = False`.
    -   **Functions**:
        -   **`setup()`**: (Placeholder).
        -   **`execute(package, pkg_logger)`**: Constructs a `bandersnatch mirror` command and delegates to the Worker via `WorkerClient`.
    -   **Dependencies**: `mirror.socket.worker`, `os`, `time`, `logging`, `pathlib.Path`.

-   **`local.py`**
    -   **Role**: (Disabled) Local file system synchronization.
    -   **`_LOAD = False`**.

-   **`_ftpsync_script.py`**
    -   **Role**: Contains `ARCHVSYNC_HASH` and `ARCHVSYNC_SCRIPT` (base64-encoded tar.gz of the archvsync scripts) used as a fallback when git is unavailable.

### 11. Toolbox (`mirror/toolbox/`)

-   **`__init__.py`**
    -   **Role**: Provides a collection of general utility functions used across various modules.
    -   **Functions**:
        -   **`iso_duration_parser(iso8601)`**: Parses an ISO 8601 duration string (e.g., `"P1DT1H"`) and converts it into total seconds. Supports days, hours, minutes, seconds. Returns `-1` for the special `"PUSH"` value, `0` for empty string.
        -   **`iso_duration_maker(duration)`**: Converts a duration in seconds back into an ISO 8601 duration string. Returns `"PUSH"` for `-1`, empty string for `0`. Maximum: 31 days (2,678,399 seconds).
        -   **`set_rsync_user(url, user)`**: Modifies an rsync URL to include a specified user. Handles both `rsync://` and `::` URL formats.
        -   **`checkPermission()`**: Checks if the current user has root privileges (`os.getuid() == 0`) or can execute commands via `sudo -n true`.
        -   **`is_command_exists(command)`**: Verifies if a shell command is available via `command -v`.
    -   **Dependencies**: `re`, `os`.

### 12. Worker (`mirror/worker/`)

-   **`__init__.py`**
    -   **Role**: Re-exports key symbols from the `process` module and provides the `manage()` background loop.
    -   **Exports**: `Job`, `create`, `get`, `get_all`, `prune_finished`, `manage`.
    -   **Functions**:
        -   **`manage(interval=1)`**: Background manager loop that periodically calls `prune_finished()` to clean up completed jobs. Runs until `mirror.exit` is set to `True`.

-   **`process.py`**
    -   **Role**: Manages the lifecycle of worker subprocesses, including starting, stopping, and monitoring them, as well as handling user/group ID switching and logging.
    -   **Global Variables**:
        -   `_jobs`: A dictionary registry of active `Job` objects, keyed by job ID.
    -   **Classes**:
        -   **`Job` (class)**:
            -   **Description**: Represents an individual worker process.
            -   **Fields**: `id` (str), `commandline` (list[str]), `env` (dict), `uid` (int), `gid` (int), `nice` (int), `log_path` (Optional[Path]), `process` (Optional[Popen]), `start_time` (Optional[float]), `end_time` (Optional[float]).
            -   **Methods**:
                -   `start()`: Spawns the subprocess with `preexec_fn` that sets GID, UID, and niceness. If `log_path` is provided, redirects stdout to the log file and merges stderr into stdout. Uses unbuffered I/O (`bufsize=0`).
                -   `get_pipe(stream)`: Returns the file descriptor for stdin, stdout, or stderr.
                -   `pid` (property): Process ID or None.
                -   `is_running` (property): Checks via `process.poll()`.
                -   `returncode` (property): Exit code or None.
                -   `stop(timeout=5)`: Terminates the process, kills if timeout expires.
                -   `info()`: Returns a dictionary with id, pid, commandline, uid, gid, nice, running status, start_time, and uptime.
    -   **Functions**:
        -   **`create(job_id, commandline, env, uid, gid, nice, log_path=None)`**: Creates and starts a new `Job`. Raises `ValueError` if `job_id` already exists in the registry.
        -   **`get(job_id)`**: Retrieves a `Job` by ID.
        -   **`get_all()`**: Returns a list of all registered jobs.
        -   **`prune_finished()`**: Iterates finished jobs, sends `job_finished` notification via `mirror.socket.worker.send_finished_notification()`, and removes them from the registry. If notification fails (e.g., no clients connected), the job is retained for the next attempt.
    -   **Dependencies**: `os`, `subprocess`, `time`, `logging`, `pathlib.Path`.
