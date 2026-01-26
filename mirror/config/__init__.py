import mirror
import mirror.structure
import mirror.config.config
import mirror.config.stat
import mirror.config.status
import mirror.toolbox
import time

from pathlib import Path
import json

# --- Global Path Variables ---
CONFIG_PATH: Path
STAT_DATA_PATH: Path
STATUS_PATH: Path
SOCKET_PATH: str

# --- Loading Functions ---

def load(conf_path: Path):
    """
    Loads the main config file, derives other paths from it, synchronizes
    with the persistent stat file, and loads the state into the application.
    """
    global CONFIG_PATH, STAT_DATA_PATH, STATUS_PATH, SOCKET_PATH
    CONFIG_PATH = conf_path

    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Configuration file not found: {CONFIG_PATH}")

    # 1. Load the primary config file to get settings and other paths
    config_dict = json.loads(CONFIG_PATH.read_text())
    config = config_dict.get("settings", {})
    stat_path_str = config.get("statfile")
    status_path_str = config.get("statusfile")
    SOCKET_PATH = config.get("socket_path", "/tmp/mirror_worker.sock")


    if not stat_path_str or not status_path_str:
        raise ValueError("Config file must contain 'statfile' and 'statusfile' settings.")

    STAT_DATA_PATH = Path(stat_path_str)
    STATUS_PATH = Path(status_path_str)

    # 2. Load stat file and synchronize with config
    stat_dict = json.loads(STAT_DATA_PATH.read_text()) if STAT_DATA_PATH.exists() else {"packages": {}}
    config_packages = config_dict.get("packages", {})
    final_stat_packages = stat_dict.get("packages", {})

    for pkg_id in list(final_stat_packages.keys()):
        if pkg_id not in config_packages:
            del final_stat_packages[pkg_id]

    for pkg_id, pkg_config in config_packages.items():
        existing_stat = final_stat_packages.get(pkg_id)
        status_to_preserve = existing_stat.get("status") if existing_stat else None
        final_stat_packages[pkg_id] = pkg_config.copy()
        if status_to_preserve:
            final_stat_packages[pkg_id]["status"] = status_to_preserve
        else:
            final_stat_packages[pkg_id]["status"] = {
                "status": "UNKNOWN",
                "statusinfo": {"errorcount": 0, "lastsync": 0.0}
            }

    # 3. Construct the full stat dictionary and save it
    full_stat_to_save = {
        "mirrorname": config_dict.get("mirrorname"),
        "packages": final_stat_packages
    }
    try:
        STAT_DATA_PATH.write_text(json.dumps(full_stat_to_save, indent=4))
    except Exception as e:
        mirror.log.error(f"Failed to save merged stat data to {STAT_DATA_PATH}: {e}")
        raise

    # 4. Prepare for in-memory loading
    loader_packages = {}
    for pkg_id, pkg_data in full_stat_to_save.get("packages", {}).items():
        loader_dict = pkg_data.copy()
        status_obj = loader_dict.get("status", {})
        status_info = status_obj.get("statusinfo", {})
        loader_dict["status"] = status_obj.get("status", "UNKNOWN")
        loader_dict["errorcount"] = status_info.get("errorcount", 0)
        loader_dict["lastsync"] = status_info.get("lastsync", 0.0)
        loader_packages[pkg_id] = loader_dict

    # 5. Load into application
    mirror.conf = mirror.structure.Config.load_from_dict(config_dict)
    mirror.packages = mirror.structure.Packages(loader_packages)
    
    # 6. Load the web status file
    _load_web_status_data()

def _load_web_status_data():
    """Loads the data for the web status page."""
    if STATUS_PATH and STATUS_PATH.exists():
        mirror.status = json.loads(STATUS_PATH.read_text())
    else:
        mirror.log.warning(f"Web status file not found at {STATUS_PATH}. Web status will be unavailable.")
        mirror.status = {}

def reload():
    """Reloads all configurations."""
    if not CONFIG_PATH:
        raise RuntimeError("Cannot reload, configuration path not set. Call load() first.")
    load(CONFIG_PATH)

def generate_and_save_web_status():
    """
    Generates the web status dictionary from the current package states
    and saves it to the status.json file.
    """
    if not STATUS_PATH:
        mirror.log.error("Cannot save web status, path not set.")
        return

    web_status = {
        "mirrorname": mirror.conf.name,
        "lastupdate": time.time() * 1000,
        "lists": list(mirror.packages.keys()),
    }

    for pkg_id in mirror.packages.keys():
        package = getattr(mirror.packages, pkg_id)
        
        web_status[pkg_id] = {
            "name": package.name,
            "id": package.pkgid,
            "status": package.status,
            "synctype": package.synctype,
            "syncrate": mirror.toolbox.iso_duration_maker(package.syncrate),
            "syncurl": package.settings.src,
            "href": package.href,
            "lastsync": package.lastsync,
            "links": [link.to_dict() for link in package.link],
        }

    try:
        STATUS_PATH.write_text(json.dumps(web_status, indent=4))
        mirror.log.info(f"Web status successfully generated and saved to {STATUS_PATH}")
    except Exception as e:
        mirror.log.error(f"Failed to save web status to {STATUS_PATH}: {e}")
