import json
import os
import platform
from pathlib import Path
from mirror.config.config import DEFAULT_CONFIG

def setup():
    if platform.system() != 'Linux':
        print('This command can only be run on Linux.')
        return

    if os.geteuid() != 0:
        print('This command must be run as root.')
        return

    try:
        etc_mirror_path = Path('/etc/mirror')
        if not etc_mirror_path.exists():
            etc_mirror_path.mkdir(parents=True, exist_ok=True)
        
        json_config_content = json.dumps(DEFAULT_CONFIG, indent=4)
        (etc_mirror_path / 'config.json').write_text(json_config_content)
            
        var_run_mirror_path = Path('/var/run/mirror')
        if not var_run_mirror_path.exists():
            var_run_mirror_path.mkdir(parents=True, exist_ok=True)

        var_lib_mirror_path = Path('/var/lib/mirror')
        if not var_lib_mirror_path.exists():
            var_lib_mirror_path.mkdir(parents=True, exist_ok=True)

        systemd_path = Path('/etc/systemd/system')
        
        mirror_service = """[Unit]
Description=Mirror Daemon
After=network.target

[Service]
ExecStart=mirror daemon --config /etc/mirror/config.json
Restart=always
User=root
Group=root

[Install]
WantedBy=multi-user.target
"""
        (systemd_path / 'mirror.service').write_text(mirror_service)

        mirror_worker_service = """[Unit]
Description=Mirror Worker
After=network.target

[Service]
ExecStart=mirror worker --config /etc/mirror/config.json
Restart=always
User=root
Group=root

[Install]
WantedBy=multi-user.target
"""
        (systemd_path / 'mirror-worker.service').write_text(mirror_worker_service)
            
    except Exception as e:
        print(f"An error occurred during setup: {e}")
