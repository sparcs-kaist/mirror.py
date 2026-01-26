import mirror
import mirror.structure
import mirror.logger
import os
import time
import logging
import subprocess
from pathlib import Path

module = "sync"
name = "rsync"

def execute(package: mirror.structure.Package, pkg_logger: logging.Logger):
    """
    Run the Rsync Sync method (CORE)
    Args:
        package (mirror.structure.Package): Package object
        pkg_logger (logging.Logger): Logger object for this sync session
    """
    from mirror.socket.worker import WorkerClient

    # Set status to SYNC as soon as we enter execute
    package.set_status("SYNC")
    pkg_logger.info(f"Starting {module}.{name} for {package.name}")

    try:
        # 1. Get command and env from the internal rsync helper
        src = Path(str(package.settings.get("src", "")))
        dst = Path(str(package.settings.get("dst", "")))
        auth = bool(package.settings.get("auth", False))
        user = str(package.settings.get("user", ""))
        password = str(package.settings.get("password", ""))

        command, env = rsync(pkg_logger, package.pkgid, src, dst, auth, user, password)

        # 2. Delegate to Worker
        # TODO: Get socket path from config
        socket_path = Path("/run/mirror/worker.sock")
        
        with WorkerClient(socket_path) as client:
            pkg_logger.info(f"Delegating sync to worker: {' '.join(command)}")
            
            response, fds = client.start_sync(
                job_id=package.pkgid,
                sync_method=name,
                commandline=command,
                env=env,
                uid=mirror.conf.uid,
                gid=mirror.conf.gid
            )

            if response.get("status") == "started":
                pkg_logger.info(f"Worker started sync (PID: {response.get('job_pid')})")
                
                # Update status - the master now tracks this package as syncing
                package.lastsync = time.time()
                package.set_status("ACTIVE")
                pkg_logger.info(f"Sync for {package.pkgid} successfully delegated to worker.")
            else:
                raise RuntimeError(f"Worker failed to start sync: {response.get('message')}")

    except Exception as e:
        pkg_logger.error(f"Sync for {package.pkgid} failed: {e}")
        package.set_status("ERROR")
    finally:
        mirror.logger.close_logger(pkg_logger)



def rsync(logger: logging.Logger, pkgid: str, src: Path, dst: Path, auth: bool, user: str, password: str):
    """
    Generate rsync command and environment
    """
    command = [
        "rsync",
        "-vrlptDSH",
        "--exclude=*.~tmp~",
        "--delete-delay",
        "--delay-updates",
        f"{src}/",
        f"{dst}/",
    ]

    env = os.environ.copy()
    if auth:
        env["USER"] = user
        env["RSYNC_PASSWORD"] = password
    
    return command, env
    

def ffts(package: mirror.structure.Package, pkg_logger: logging.Logger):
    """Check if the mirror is up to date via Worker delegation"""
    from mirror.socket.worker import WorkerClient

    pkg_logger.info(f"Running FFTS check for {package.name}")
    if package.is_syncing(): 
        raise ValueError("Package is already syncing")
    
    package.set_status("SYNC")

    try:
        src = str(package.settings.get("src", ""))
        dst = str(package.settings.get("dst", ""))
        fftsfile = str(package.settings.get("fftsfile", ""))
        user = str(package.settings.get("user", ""))
        password = str(package.settings.get("password", ""))

        command = [
            "rsync",
            "--no-motd",
            "--dry-run",
            "--out-format=%n",
            f"{src}/{fftsfile}",
            f"{dst}/{fftsfile}",
        ]

        env = os.environ.copy()
        if user:
            env["USER"] = user
            env["RSYNC_PASSWORD"] = password

        # Delegate FFTS check to worker
        socket_path = Path("/run/mirror/worker.sock")
        with WorkerClient(socket_path) as client:
            pkg_logger.info(f"Delegating FFTS check to worker: {' '.join(command)}")
            
            response, fds = client.start_sync(
                job_id=f"{package.pkgid}_ffts",
                sync_method="ffts",
                commandline=command,
                env=env,
                uid=mirror.conf.uid,
                gid=mirror.conf.gid
            )

            if response.get("status") == "started":
                pkg_logger.info(f"Worker started FFTS check (PID: {response.get('job_pid')})")
                package.set_status("ACTIVE")
            else:
                raise RuntimeError(f"Worker failed to start FFTS check: {response.get('message')}")

    except Exception as e:
        pkg_logger.error(f"FFTS check for {package.pkgid} failed: {e}")
        package.set_status("ERROR")
    finally:
        mirror.logger.close_logger(pkg_logger)