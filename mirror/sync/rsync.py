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
    # Set status to SYNC as soon as we enter execute
    package.set_status("SYNC")
    pkg_logger.info(f"Starting {module}.{name} for {package.name}")

    try:
        # 1. Get settings
        src = package.settings.src
        dst = Path(package.settings.dst)
        ffts_val = package.settings.options.get("ffts", False)

        user = str(package.settings.get("user", ""))
        password = str(package.settings.get("password", ""))
        
        # 2. FFTS Check
        if ffts_val:
            if not ffts(package, pkg_logger):
                pkg_logger.info("FFTS check: Up to date. Skipping sync.")
                package.lastsync = time.time()
                package.set_status("ACTIVE")
                return

        # 3. Prepare command and env
        command, env = rsync(pkg_logger, package.pkgid, src, dst, user, password)

        # 4. Execute sync directly
        pkg_logger.info(f"ENV: src={src}")
        pkg_logger.info(f"ENV: dst={dst}")
        pkg_logger.info(f"Running rsync: {' '.join(command)}")
        
        log_file = None
        for handler in pkg_logger.handlers:
            if isinstance(handler, logging.FileHandler):
                log_file = open(handler.baseFilename, "a")
                break
        
        try:
            result = subprocess.run(
                command,
                env=env,
                stdout=log_file,
                stderr=log_file,
                text=True
            )

            if result.returncode == 0:
                pkg_logger.info(f"Sync for {package.pkgid} completed successfully.")
                package.lastsync = time.time()
                package.set_status("ACTIVE")
            else:
                raise RuntimeError(f"rsync failed with return code {result.returncode}")
        finally:
            if log_file:
                log_file.close()

    except AttributeError as e:
        pkg_logger.error(f"Sync for {package.pkgid} failed: value not found")
        pkg_logger.error(e)
        package.set_status("ERROR")
    except Exception as e:
        pkg_logger.error(f"Sync for {package.pkgid} failed: {e}")
        package.set_status("ERROR")
    finally:
        #mirror.logger.close_logger(pkg_logger)
        pass



def rsync(logger: logging.Logger, pkgid: str, src: str, dst: Path, user: str, password: str):
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

    env = {}
    if user:
        env["USER"] = user
        env["RSYNC_PASSWORD"] = password
    
    return command, env
    

def ffts(package: mirror.structure.Package, pkg_logger: logging.Logger) -> bool:
    """Check if the mirror is up to date via direct rsync call"""
    pkg_logger.info(f"Running FFTS check for {package.name}")
    
    try:
        src = package.settings.src
        dst = Path(package.settings.dst)
        fftsfile = package.settings.options.get("fftsfile", "")
        timeout = 10

        user = str(package.settings.options.get("user", ""))
        password = str(package.settings.options.get("password", ""))

        command = [
            "rsync",
            "--no-motd",
            "--dry-run",
            "--out-format=%n",
            f"--contimeout={timeout}",
            f"{src}/{fftsfile}",
            f"{dst}/{fftsfile}",
        ]

        env = os.environ.copy()
        if user:
            env["USER"] = user
            env["RSYNC_PASSWORD"] = password

        pkg_logger.info(f"Executing FFTS check: {' '.join(command)}")
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        
        if result.returncode == 0:
            if result.stdout.strip():
                pkg_logger.info("FFTS check: Update needed.")
                return True
            else:
                pkg_logger.info("FFTS check: Up to date.")
                return False
        else:
            pkg_logger.warning(f"FFTS check failed with return code {result.returncode}: {result.stderr}")
            return True # Assume update needed on error
            
    except Exception as e:
        pkg_logger.error(f"FFTS check for {package.pkgid} failed: {e}")
        return True # Assume update needed on error
