import mirror
import mirror.structure
import mirror.logger
import os
import time
import logging
from pathlib import Path

module = "sync"
name = "lftp"

def execute(package: mirror.structure.Package, pkg_logger: logging.Logger):
    """
    Run the lftp Sync method (CORE)
    Args:
        package (mirror.structure.Package): Package object
        pkg_logger (logging.Logger): Logger object for this sync session
    """
    from mirror.socket.worker import WorkerClient

    package.set_status("SYNC")
    pkg_logger.info(f"Starting {module}.{name} for {package.name}")

    try:
        # 1. Prepare commandline
        src = package.settings.get("src", "")
        dst = package.settings.get("dst", "")
        
        # Using raw string to avoid invalid escape sequence warnings
        lftp_script = (
            f"set ftp:anon-pass mirror@{src}; "
            f"set cmd:verbose yes; "
            r"mirror --continue --delete --no-perms --verbose=3 "
            r"-X '\.(mirror|notar)' -x '\.in\..*\.' -X 'lost+found' "
            f"ftp://{src} {dst}"
        )

        command = [
            "lftp",
            "-c",
            lftp_script
        ]

        # 2. Delegate to Worker
        socket_path = Path("/run/mirror/worker.sock")
        
        with WorkerClient(socket_path) as client:
            pkg_logger.info(f"Delegating lftp sync to worker: {' '.join(command)}")
            
            response, fds = client.start_sync(
                job_id=package.pkgid,
                sync_method=name,
                commandline=command,
                env={},
                uid=os.getuid(),
                gid=os.getgid()
            )

            if response.get("status") == "started":
                pkg_logger.info(f"Worker started lftp sync (PID: {response.get('job_pid')})")
                package.lastsync = time.time()
                package.set_status("ACTIVE")
            else:
                raise RuntimeError(f"Worker failed to start lftp sync: {response.get('message')}")

    except Exception as e:
        pkg_logger.error(f"lftp sync for {package.pkgid} failed: {e}")
        package.set_status("ERROR")
    finally:
        mirror.logger.close_logger(pkg_logger)

def ftp(package: mirror.structure.Package):
    """Legacy helper"""
    pass