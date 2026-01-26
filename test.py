import os
import sys
import json
import shutil
import code
from pathlib import Path

# Add project root to path
sys.path.append(os.getcwd())

import mirror
import mirror.config
import mirror.structure
import mirror.logger

def main():
    print("Initializing Mirror Test Environment...")

    # 1. Setup Test Directory
    test_dir = Path("test_env")
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir()
    (test_dir / "logs").mkdir()
    (test_dir / "web").mkdir()
    
    # 2. Prepare Config
    config_src = Path("config-example.json")
    if not config_src.exists():
        print("Error: config-example.json not found.")
        return

    config_data = json.loads(config_src.read_text())
    
    # Adjust paths to be local
    config_data["settings"]["logfolder"] = str(test_dir / "logs")
    config_data["settings"]["webroot"] = str(test_dir / "web")
    config_data["settings"]["statusfile"] = str(test_dir / "web" / "status.json")
    config_data["settings"]["statfile"] = str(test_dir / "stat.json") # Add missing statfile

    config_path = test_dir / "config.json"
    config_path.write_text(json.dumps(config_data, indent=4))
    
    # Initialize stat file
    (test_dir / "stat.json").write_text(json.dumps({"packages": {}}))

    print(f"Temporary config created at {config_path}")

    # 3. Patch mirror.logger to fix AttributeError (module vs object)
    # The codebase calls mirror.logger.info(), but mirror.logger is a module.
    # We map module-level calls to the internal logger object.
    if not hasattr(mirror.logger, "info"):
        mirror.logger.info = mirror.logger.logger.info
    if not hasattr(mirror.logger, "error"):
        mirror.logger.error = mirror.logger.logger.error
    if not hasattr(mirror.logger, "warning"):
        mirror.logger.warning = mirror.logger.logger.warning

    # 4. Load Config
    try:
        mirror.config.load(config_path)
        print("Config loaded successfully.")
    except Exception as e:
        print(f"Error loading config: {e}")
        import traceback
        traceback.print_exc()
        return

    # 5. Interactive Shell
    print("\n" + "="*60)
    print("Welcome to the Mirror Interactive Shell")
    print("="*60)
    print("Available variables:")
    print("  mirror          - The main mirror module")
    print("  mirror.conf     - Loaded configuration")
    print("  mirror.packages - Loaded packages")
    print("="*60)
    
    # Prepare local variables for the shell
    shell_locals = globals().copy()
    shell_locals.update(locals())
    
    code.interact(local=shell_locals)

if __name__ == "__main__":
    main()
