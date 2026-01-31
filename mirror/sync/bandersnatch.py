import mirror
import mirror.structure
import mirror.logger
import os
import time
import logging
from pathlib import Path

module = "sync"
name = "bandersnatch"

def setup():
    """Setup bandersnatch sync module"""
    pass

def execute(package: mirror.structure.Package, pkg_logger: logging.Logger):
    """Sync package using bandersnatch"""
    from mirror.socket.worker import WorkerClient
    
    package.set_status("SYNC")
    pkg_logger.info(f"Starting {module}.{name} for {package.name}")

    try:
        # 1. Prepare commandline
        # Bandersnatch usually uses a config file
        # For now, we assume mirror mode
        command = [
            "bandersnatch",
            "mirror"
        ]

        # 2. Delegate to Worker
        socket_path = Path("/run/mirror/worker.sock")
        
        log_path = None
        for handler in pkg_logger.handlers:
            if isinstance(handler, logging.FileHandler):
                log_path = handler.baseFilename
                break

        with WorkerClient(socket_path) as client:
            pkg_logger.info(f"Delegating bandersnatch sync to worker")
            
            response = client.start_sync(
                job_id=package.pkgid,
                sync_method=name,
                commandline=command,
                env={},
                uid=os.getuid(),
                gid=os.getgid(),
                log_path=log_path
            )

            if response.get("status") == "started":
                pkg_logger.info(f"Worker started bandersnatch sync (PID: {response.get('job_pid')})")
                package.lastsync = time.time()
                package.set_status("ACTIVE")
            else:
                raise RuntimeError(f"Worker failed to start sync: {response.get('message')}")

    except Exception as e:
        pkg_logger.error(f"bandersnatch sync for {package.pkgid} failed: {e}")
        package.set_status("ERROR")
    finally:
        mirror.logger.close_logger(pkg_logger)