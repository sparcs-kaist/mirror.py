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
    -   **Role**: Initializes global variables and imports sub-modules, setting up the core environment for the `mirror` application.
    -   **Global Variables**:
        -   `conf` (Config): Global configuration object.
        -   `packages` (Packages): Container for all managed mirror packages.
        -   `confPath` (Path): Path to the main configuration file.
        -   `publishPath` (Path): Base path for mirrored content.
        -   `log` (logging.Logger): Main application logger.
        -   `debug` (bool): Flag indicating if debug mode is active.
        -   `RUN_PATH` (Path): Runtime data directory (e.g., for PIDs, sockets).
        -   `STATE_PATH` (Path): Persistent state data directory.
        -   `__version__` (str): Version string of the application.
    -   **Dependencies**: Imports sub-modules `toolbox`, `event`, `sync`, `logger`, `command`, `config`, `plugin`, `socket`.
    -   **Functions**: Calls `mirror.sync.load_default()` to load default synchronization methods.

-   **`__main__.py`**
    -   **Role**: The primary entry point for the `mirror` CLI application, defining command-line interfaces using the `click` library.
    -   **Version**: `1.0.0-pre3` (as of current development).
    -   **Commands**:
        -   **`setup()`**:
            -   **Description**: Configures the `mirror` environment, including creating necessary directories and systemd service files.
            -   **Dependencies**: Calls functions from `mirror.command.setup`.
        -   **`crontab(user, config)`**:
            -   **Description**: Generates crontab entries based on the mirror configuration.
            -   **Options**:
                -   `-u/--user`: User for whom the crontab is generated (default: `root`).
                -   `-c/--config`: Path to the configuration file (default: `/etc/mirror/config.json`).
            -   **Dependencies**: Calls functions from `mirror.command.crontab`.
        -   **`daemon(config)`**:
            -   **Description**: Runs the `mirror` daemon process, responsible for monitoring and initiating sync operations.
            -   **Options**:
                -   `--config`: Path to the configuration file (default: `/etc/mirror/config.json`).
            -   **Dependencies**: Calls functions from `mirror.command.daemon`.
        -   **`worker(config)`**:
            -   **Description**: Runs a worker server process that executes actual synchronization tasks.
            -   **Options**:
                -   `--config`: Path to the configuration file (default: `/etc/mirror/config.json`).
            -   **Dependencies**: Calls functions from `mirror.command.worker`.

### 2. Command (`mirror/command/`)

-   **`__init__.py`**
    -   **Role**: Serves as a convenient module to import and expose the main CLI command functions from its sub-modules.
    -   **Exports**: `setup`, `daemon`, `crontab`, and `worker`.

-   **`setup.py`**
    -   **Role**: Handles the initial setup and provisioning of the `mirror` application on a system.
    -   **`setup()`**:
        -   **Description**: Performs environment validation (OS and user), creates essential directory structures (`/etc/mirror`, `/var/run/mirror`, `/var/lib/mirror`), writes a default configuration, and generates systemd service unit files.
        -   **Dependencies**: `os`, `pathlib.Path`, `mirror.command.crontab`, `mirror.config`, `mirror.logger`.
        -   **Files Generated**:
            -   `/etc/mirror/config.json`: Default configuration.
            -   `/etc/systemd/system/mirror.service`: Systemd unit for the daemon.
            -   `/etc/systemd/system/mirror-worker.service`: Systemd unit for the worker.

-   **`daemon.py`**
    -   **Role**: Implements the main daemon logic for `mirror`, overseeing synchronization tasks and managing worker processes.
    -   **`daemon(config)`**:
        -   **Description**: Loads the specified configuration, initializes the logger, writes a PID file, starts the master socket server, and enters a continuous loop. In this loop, it checks the status of the worker server and evaluates packages to determine if a sync is required based on their `lastsync` time and `syncrate`.
        -   **Dependencies**: `mirror.config`, `mirror.logger`, `mirror.socket`, `mirror.sync`, `os`, `sys`, `time`, `pathlib.Path`.
    -   **`check_daemon()`**:
        -   **Description**: (Placeholder) Intended to check if the daemon is currently running.
        -   **Dependencies**: (Not yet implemented)

-   **`crontab.py`**
    -   **Role**: Manages the generation of crontab entries for scheduled mirror operations.
    -   **`crontab(user, config)`**:
        -   **Description**: (Placeholder) Designed to generate and potentially install crontab entries.
        -   **Dependencies**: (Not yet implemented, will likely involve `mirror.config`).

-   **`worker.py`**
    -   **Role**: Implements the worker process that listens for and executes synchronization commands from the master daemon.
    -   **`worker()`**:
        -   **Description**: This function starts the worker socket server, which then waits for commands (e.g., `start_sync`) from the master daemon to perform actual synchronization tasks.
        -   **Dependencies**: `mirror.logger`, `mirror.socket`, `mirror.sync`, `sys`.

### 3. Config (`mirror/config/`)

-   **`__init__.py`**
    -   **Role**: Manages the loading, reloading, and storage of application configuration and status data.
    -   **Global Path Variables**:
        -   `CONFIG_PATH`: Resolved path to the main configuration file.
        -   `STAT_DATA_PATH`: Resolved path to the persistent statistics data file.
        -   `STATUS_PATH`: Resolved path to the web status JSON file.
        -   `SOCKET_PATH`: Resolved path for the Unix domain socket.
    -   **`load(conf_path: Path)`**:
        -   **Description**: Reads the main JSON configuration file, populates `mirror.conf` (a `Config` object) and `mirror.packages` (a `Packages` object), and handles initial setup for status data and socket paths.
        -   **Dependencies**: `json`, `pathlib.Path`, `mirror.structure`, `mirror.toolbox`.
    -   **`_load_web_status_data()`**:
        -   **Description**: Loads historical web status data from `STATUS_PATH`.
        -   **Dependencies**: `json`, `pathlib.Path`.
    -   **`reload()`**:
        -   **Description**: Re-applies the current configuration from `mirror.conf` to `mirror.packages`.
        -   **Dependencies**: `mirror.packages`.
    -   **`generate_and_save_web_status()`**:
        -   **Description**: Compiles current sync status, rates, and package information into a JSON file (`status.json`) for web display.
        -   **Dependencies**: `json`, `datetime`, `pathlib.Path`, `mirror.packages`, `mirror.conf`.

-   **`config.py`**
    -   **Role**: Defines the default structure and values for the main application configuration.
    -   **`DEFAULT_CONFIG`**:
        -   **Description**: A dictionary template outlining all configurable settings, including `mirrorname`, various `settings` (logger, ftpsync, webroot, logfolder, UID/GID, timezone, maintainer, plugins), and a sample `packages` configuration.
        -   **Dependencies**: None.

-   **`stat.py`**
    -   **Role**: Defines the default structure for package statistics data.
    -   **`DEFAULT_STAT_DATA`**:
        -   **Description**: A dictionary containing template fields for package status information such as `errorcount` and `logs`.
        -   **Dependencies**: None.

-   **`status.py`**
    -   **Role**: Defines the default structure and example content for the web status JSON file.
    -   **`DEFAULT_STATUS`**:
        -   **Description**: A dictionary template providing example mirror configurations (e.g., Linux Kernel, AlmaLinux, OpenWRT, RPM Fusion, TinyCore Linux) to illustrate the expected format for web status data.
        -   **Dependencies**: None.

### 4. Event (`mirror/event/`)

-   **`__init__.py`**
    -   **Role**: Implements a generic event handling system that allows for registering and triggering listeners before and after an event.
    -   **Global Variables**:
        -   `events`: A dictionary to store `BasicEvent` instances, keyed by event names.
    -   **`BasicEvent` (class)**:
        -   **Description**: Represents a single event that can have `pre_listeners` and `post_listeners`. It executes listeners in separate threads.
        -   **Fields**: `pre_listeners` (list), `post_listeners` (list), `threads` (list of `threading.Thread`).
        -   **Methods**:
            -   `_call(listeners, *args, **kwargs)`: Executes a list of listener functions, each in its own thread.
            -   `add_listener(listener, pre)`: Registers a function as either a pre-event or post-event listener.
            -   `remove_listener(listener, pre)`: Unregisters a listener.
            -   `wait()`: Blocks until all currently running listener threads for this event complete.
        -   **Dependencies**: `threading`, `mirror.logger`.
    -   **`post_event(event_name, *args, wait=False, **kwargs)`**:
        -   **Description**: Triggers an event by name, executing its pre-listeners (if any), then optionally the main task, and finally its post-listeners. Can wait for listeners to complete.
        -   **Dependencies**: `mirror.logger`.
    -   **`register_event(event_name, listener, pre=True)`**:
        -   **Description**: A utility function to register a listener for a specific event.

### 5. Handler (`mirror/handler/`)

-   **`__init__.py`**
    -   **Role**: (Currently empty) This module is reserved for future implementations of various handlers (e.g., specific error handlers, custom input handlers).
    -   **Dependencies**: None.

### 6. Logger (`mirror/logger/`)

-   **`__init__.py`**
    -   **Role**: Initializes the logging subsystem, providing custom handlers and default formatting options.
    -   **Classes**:
        -   **`PromptHandler`**: A custom logging handler that formats log records for console output.
        -   **`GzipTimedRotatingFileHandler`**: A custom rotating file handler that compresses old log files using gzip.
    -   **Constants**: Defines default log levels, formats for console and package-specific logging, and file naming conventions.
    -   **Functions**:
        -   **`_time_formatting(line, usetime, pkgid)`**: Formats dynamic placeholders in log file paths based on time and package ID.
        -   **`compress_file(filepath)`**: Compresses a given file using gzip.
        -   **`create_logger(name, start_time)`**: Creates and configures a dedicated logger for a specific package synchronization, including file and console handlers.
        -   **`close_logger(pkg_logger, compress)`**: Closes all handlers for a package logger and optionally compresses its log file.
        -   **`setup_logger()`**: Configures the main application logger (`mirror.log`) with console and rotating file handlers, setting up the `basePath` for log files.
    -   **Dependencies**: `logging`, `datetime`, `gzip`, `shutil`, `pathlib.Path`, `prompt_toolkit`.

### 7. Plugin (`mirror/plugin/`)

-   **`__init__.py`**
    -   **Role**: Manages the dynamic loading and registration of external plugins to extend `mirror`'s functionality.
    -   **Global Variables**:
        -   `loadable_module`: A list of module types that can be extended by plugins (e.g., "sync", "logger").
    -   **`plugin_loader()`**:
        -   **Description**: Iterates through configured plugins, validates their attributes (`setup`, `module`, `name`, `entry`), dynamically imports them, and registers their entry points with the appropriate `mirror` sub-modules.
        -   **Dependencies**: `importlib`, `mirror.logger`, `mirror.event`, `mirror.config`.

### 8. Socket (`mirror/socket/`)

-   **`__init__.py`**
    -   **Role**: Provides the foundational classes for inter-process communication (IPC) using Unix domain sockets, defining a length-prefixed JSON protocol.
    -   **`BaseHandler` (class)**:
        -   **Description**: Abstract base class for handling socket connections. It reads length-prefixed JSON packets, dynamically executes commands received, and sends back structured responses (either success or error).
        -   **Methods**: `handle(connection)`: Processes incoming requests.
        -   **Response Format**: `{"status": int, "result": any}` on success, or `{"status": int, "error": str, "traceback": str}` on failure.
        -   **Dependencies**: `json`, `socket`, `traceback`, `mirror.logger`.
    -   **`MirrorSocket` (class)**:
        -   **Description**: Manages a Unix domain socket server (`socket_path`). It can operate in 'master' or 'worker' roles, accepting concurrent connections and dispatching them to a handler.
        -   **Methods**:
            -   `__init__(socket_path, handler_class)`: Initializes the socket server.
            -   `start()`: Begins listening for incoming connections in a separate thread.
            -   `stop()`: Shuts down the socket server cleanly.
            -   `_accept_loop()`: (Internal) Continuously accepts new connections and dispatches handlers.
        -   **Dependencies**: `socket`, `threading`, `pathlib.Path`, `mirror.logger`.
    -   **`MASTER_SOCKET_PATH`**:
        -   **Description**: Returns the default Unix domain socket path for the master process.
        -   **Dependencies**: `mirror.RUN_PATH`.
    -   **`WORKER_SOCKET_PATH`**:
        -   **Description**: Returns the default Unix domain socket path for worker processes.
        -   **Dependencies**: `mirror.RUN_PATH`.
    -   **`init(role, socket_path)`**:
        -   **Description**: Factory function to initialize and return a `MirrorSocket` instance based on the specified `role` ('master' or 'worker') and an optional `socket_path`.
        -   **Dependencies**: `mirror.socket.master`, `mirror.socket.worker`.
    -   **`stop()`**:
        -   **Description**: Global function to stop any active `MirrorSocket` instance.

-   **`client.py`**
    -   **Role**: Provides a client interface for communicating with `MirrorSocket` servers via Unix domain sockets.
    -   **`MirrorClient` (class)**:
        -   **Description**: Establishes a connection to a `MirrorSocket`, sends commands, and receives responses. It supports dynamic method calls that are translated into JSON RPC requests.
        -   **Methods**:
            -   `__init__(socket_path)`: Connects to the specified socket.
            -   `_send_request(command, kwargs)`: Sends a JSON RPC request and waits for a response.
            -   `__getattr__(name)`: Enables dynamic method calls (e.g., `client.start_sync(id=1)`).
        -   **Dependencies**: `json`, `socket`, `mirror.logger`.

-   **`master.py`**
    -   **Role**: Defines the handler and client for the master process's socket interface.
    -   **`MasterHandler` (class)**:
        -   **Description**: Implements the `BaseHandler` for the master process. It processes incoming commands, such as `ping`.
        -   **Commands**: `ping` (returns a simple "pong" response).
        -   **Dependencies**: `mirror.socket`, `mirror.logger`.
    -   **`MasterClient` (class)**:
        -   **Description**: Provides a client-side interface for other processes (e.g., worker) to send requests to the master process.
        -   **Dependencies**: `mirror.socket.client`.

-   **`worker.py`**
    -   **Role**: Defines the handler and client for the worker process's socket interface, enabling it to receive and execute tasks from the master.
    -   **`WorkerHandler` (class)**:
        -   **Description**: Implements the `BaseHandler` for the worker process. It processes commands like `start_sync`.
        -   **Commands**: `start_sync(id, command)`: Initiates a synchronization task for a given package.
        -   **Dependencies**: `mirror.socket`, `mirror.logger`, `mirror.sync`.
    -   **`WorkerClient` (class)**:
        -   **Description**: Provides a client-side interface for the master process to send requests to a worker process.
        -   **Dependencies**: `mirror.socket.client`.
    -   **`is_worker_running(job_id)`**:
        -   **Description**: Checks if a worker process is reachable and responsive via its socket.

### 9. Structure (`mirror/structure/`)

-   **Role**: Defines the core data models and type definitions used throughout the `mirror` application, ensuring data consistency and clear interfaces.

-   **`Options` (dataclass)**
    -   **Description**: A base dataclass that provides common utility methods for converting data structures to dictionaries and JSON.
    -   **Methods**: `to_dict()`, `to_json()`.
    -   **Dependencies**: `dataclasses`, `json`.

-   **`SyncExecuter` (class)**
    -   **Description**: (Placeholder) Intended to encapsulate the logic for executing synchronization tasks.
    -   **Methods**: `sync()`: (Placeholder).
    -   **Dependencies**: (Future implementations will likely depend on `mirror.sync`).

-   **`Worker` (class)**
    -   **Description**: Represents a worker instance with its assigned package and associated logging/synchronization objects.
    -   **Fields**: `package`, `logger`, `sync` (SyncExecuter).
    -   **Dependencies**: `mirror.structure.Package`, `logging`.

-   **`PackageSettings` (dataclass)**
    -   **Description**: Stores the specific configuration settings for an individual mirror package.
    -   **Fields**: `hidden` (bool), `src` (str), `dst` (str), `options` (dict).
    -   **Dependencies**: `dataclasses`.

-   **`Package` (dataclass)**
    -   **Description**: Represents a single mirror package, including its configuration, status, and metadata.
    -   **Nested Classes**:
        -   `Link`: Defines a hyperlink with `rel` (relation) and `href` (URL).
        -   `StatusInfo`: Holds status-related metrics like `lastsynclog`, `lastsuccesslog`, `errorcount`.
    -   **Fields**: `pkgid`, `name`, `status`, `href`, `synctype`, `syncrate`, `link`, `settings`, `lastsync`, `errorcount`, `disabled`, `uuid`.
    -   **Methods**: `from_dict()`, `set_status()`, `to_dict()`, `to_json()`, `is_syncing()`, `is_disabled()`, `_path_check()`.
    -   **Dependencies**: `dataclasses`, `json`, `datetime`, `uuid`.

-   **`Sync` (dataclass)**
    -   **Description**: Defines the structure for a synchronization operation request.
    -   **Fields**: `pkgid`, `synctype`, `logPath`, `options`, `settings`.
    -   **Dependencies**: `dataclasses`, `pathlib.Path`, `mirror.structure.PackageSettings`.

-   **`Packages` (dataclass)**
    -   **Description**: A container class that manages a collection of `Package` objects, providing dictionary-like access.
    -   **Methods**: `items()`, `keys()`, `values()`, `to_dict()`.
    -   **Dependencies**: `dataclasses`, `collections.UserDict`, `mirror.structure.Package`.

-   **`Config` (dataclass)**
    -   **Description**: The main application-wide configuration object, holding global settings and nested configurations.
    -   **Nested Class**:
        -   `FTPSync`: Holds specific configuration for FTPSync operations (maintainer, sponsor, country, location, throughput, include, exclude).
    -   **Fields**: `name`, `hostname`, `lastsettingmodified`, `logfolder`, `webroot`, `ftpsync`, `uid`, `gid`, `maintainer`, `localtimezone`, `logger`, `plugins`.
    -   **Methods**: `load_from_dict()`, `save()`, `to_dict()`, `to_json()`, `_path_check()`.
    -   **Dependencies**: `dataclasses`, `json`, `pathlib.Path`, `datetime`.

-   **`Packet` (class)**
    -   **Description**: Defines the structure for data packets used in inter-process communication (IPC) via sockets.
    -   **Fields**: `mode`, `sender`, `to`, `command`, `args`, `kwargs`.
    -   **Methods**: `load()`, `to_dict()`, `to_json()`.
    -   **Dependencies**: `json`.

### 10. Sync (`mirror/sync/`)

-   **Role**: Provides the framework and implementations for various file synchronization methods.

-   **`__init__.py`**
    -   **Global Variables**:
        -   `BasicMethodPath`: Path to the directory containing basic sync method modules.
        -   `methods`: A list of available synchronization method names (e.g., "rsync", "ftpsync").
        -   `now`: (Not fully clear, likely intended for tracking active syncs).
    -   **Classes**:
        -   **`Options`**: A class (likely a placeholder or base) for handling dynamic sync options.
    -   **Functions**:
        -   **`loader(methodPath: Path)`**: Dynamically loads Python modules from a specified directory into the `mirror.sync` namespace, enabling extensibility for sync methods.
        -   **`load_default()`**: Loads the default sync modules from the current directory.
        -   **`start(package)`**: (Placeholder) Initiates a synchronization process for a given package.
        -   **`execute(package, logger, method)`**: Executes the `execute` function of a specified sync method module.
        -   **`_execute(package, logger, method)`**: (Placeholder) Intended for threaded or background execution of sync.
        -   **`setexecuser(uid, gid)`**: A helper function to set the effective UID and GID for the current process, typically used in `preexec_fn` for subprocesses.
    -   **Dependencies**: `importlib`, `pathlib.Path`, `mirror.logger`, `mirror.event`, `os`.

-   **`_ftpsync_script.py`**
    -   **Role**: Contains the raw Bash script source code used by the `ftpsync` module.
    -   **Constants**: `ARCHVSYNC_SCRIPT`: A multi-line string holding the Bash script.
    -   **Dependencies**: None.

-   **`bandersnatch.py`**
    -   **Role**: (Currently mostly a placeholder) Intended to implement synchronization using the `bandersnatch` tool.
    -   **Dependencies**: `mirror.logger`, `mirror.structure`.

-   **`ftpsync.py`**
    -   **Role**: Implements file synchronization using FTP via a custom Bash script.
    -   **`ftpsync(package)`**:
        -   **Description**: Prepares a temporary environment, writes the `ftpsync` Bash script and its configuration to a temporary location, and then executes the script to perform the FTP sync.
        -   **Dependencies**: `tempfile`, `subprocess`, `os`, `mirror.logger`, `mirror.structure`, `mirror.sync._ftpsync_script`.
    -   **`_setup(path, package)`**: (Internal) Creates necessary `bin` and `etc` directories for the `ftpsync` script.
    -   **`_config(package)`**: (Internal) Generates the configuration file content required by the `ftpsync` Bash script.
    -   **`_test()`**: (Internal) A test function for ftpsync functionality.
    -   **Metadata**: `module`, `name`, `required`, `options`.

-   **`lftp.py`**
    -   **Role**: (Partial implementation) Intended to provide synchronization capabilities using the `lftp` command-line tool.
    -   **`ftp(package)`**: (Partial implementation) Contains logic for preparing and executing `lftp` commands.
    -   **Dependencies**: `mirror.logger`, `mirror.structure`, `subprocess`.
    -   **Metadata**: `module`, `name`, `required`, `options`.

-   **`local.py`**
    -   **Role**: (Currently empty) Intended to implement local file system synchronization (e.g., copying files within the same machine).
    -   **Dependencies**: None.
    -   **Metadata**: `module`, `name`, `required`, `options`.

-   **`rsync.py`**
    -   **Role**: Implements file synchronization using the `rsync` command-line tool.
    -   **Functions**:
        -   **`setup()`**: Validates the presence of required `rsync` and `ssh` commands.
        -   **`execute(package)`**: The main entry point for an `rsync` synchronization task. It checks the package status, sets up a logger, and then performs the sync using the `rsync` function.
        -   **`rsync(logger, pkgid, src, dst, auth, userid, passwd)`**: Constructs and executes the `rsync` command, including options for logging, exclusion, and various authentication methods.
        -   **`ffts(package, logger)`**: Performs a "File Transfer Timestamp" check using an `rsync` dry-run to determine if any files need to be updated.
    -   **Dependencies**: `subprocess`, `mirror.logger`, `mirror.structure`, `mirror.toolbox`.
    -   **Metadata**: `module`, `name`, `required`, `options`.

### 11. Toolbox (`mirror/toolbox/`)

-   **`__init__.py`**
    -   **Role**: Provides a collection of general utility functions used across various modules of the `mirror` application.
    -   **Functions**:
        -   **`iso_duration_parser(iso8601)`**: Parses an ISO 8601 duration string (e.g., "P1DT1H") and converts it into a total number of seconds. Handles a special "PUSH" value.
        -   **`iso_duration_maker(duration)`**: Converts a duration in seconds back into an ISO 8601 duration string. Handles the "PUSH" special case and limits the maximum duration.
        -   **`set_rsync_user(url, user)`**: Modifies an rsync URL string to include a specified user.
        -   **`checkPermission()`**: Checks if the current user has root privileges or can execute commands via `sudo`.
        -   **`is_command_exists(command)`**: Verifies if a given shell command is available in the system's PATH.
        -   **`convert_bytes(size_bytes)`**: Converts a given number of bytes into a human-readable string (e.g., KB, MB, GB).
    -   **Dependencies**: `re`, `datetime`, `subprocess`, `os`.

### 12. Worker (`mirror/worker/`)

-   **`__init__.py`**
    -   **Role**: Exports the `process` module, which contains the core logic for managing worker subprocesses.
    -   **Exports**: `process`.

-   **`process.py`**
    -   **Role**: Manages the lifecycle of worker subprocesses, including starting, stopping, and monitoring them, as well as handling user/group ID switching and logging.
    -   **Global Variables**:
        -   `_jobs`: A dictionary registry of active `Job` objects.
    -   **Classes**:
        -   **`Job` (class)**:
            -   **Description**: Represents an individual worker process, encapsulating its command, environment, UID/GID, PID, and logging.
            -   **Fields**: `id`, `commandline`, `env`, `uid`, `gid`, `nice`, `log_path`, `process` (Popen object), `stdin`, `stdout`, `stderr`, `start_time`, `end_time`, `_log_thread`.
            -   **Methods**:
                -   `start()`: Spawns the worker subprocess, applies UID/GID/niceness using `preexec_fn`, and handles stdout/stderr redirection.
                -   `set_log_path(log_path)`: Configures a background thread to redirect stdout to a specified log file.
                -   `get_pipe(stream)`: Returns the file descriptor for a given stream (stdin, stdout, stderr).
                -   `pid` (property): Returns the process ID.
                -   `is_running` (property): Checks if the process is still active.
                -   `returncode` (property): Returns the exit code of the process.
                -   `stop(timeout)`: Terminates the worker process.
                -   `info()`: Returns a dictionary of worker status information.
            -   **Dependencies**: `os`, `subprocess`, `time`, `logging`, `threading`, `pathlib.Path`.
    -   **Functions**:
        -   **`create(job_id, commandline, env, uid, gid, nice, log_path)`**: Creates and starts a new `Job` instance. Raises `ValueError` if `job_id` already exists.
        -   **`get(job_id)`**: Retrieves a `Job` object by its ID.
        -   **`get_all()`**: Returns a list of all active `Job` objects.
        -   **`prune_finished()`**: Removes finished jobs from the global registry, optionally waiting for log threads to complete.
        -   **`set_log_path(job_id, log_path)`**: Sets the log path for an existing worker job.
    -   **Dependencies**: `os`, `subprocess`, `time`, `logging`, `threading`, `pathlib.Path`.
