import mirror

from pathlib import Path
import importlib.util

loadable_module = ["sync", "logger", "plugin",]

def _load_module_from_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return None

def plugin_loader():
    """Load the plugins"""
    for plugin in mirror.conf.plugins:
        pluginPath = Path(plugin).resolve()
        if not pluginPath.exists():
            raise FileNotFoundError(f"Plugin {plugin} does not exist!")

        # Load temporarily to check attributes
        this = _load_module_from_path("", pluginPath)
        if this is None:
             raise ImportError(f"Failed to load plugin {plugin}")

        check = ["setup", "module", "name", "entry"]
        for attr in check:
            if not hasattr(this, attr):
                raise AttributeError(f"Plugin {plugin} does not have attribute {attr}!")
        
        try:
            if this.module not in loadable_module:
                raise AttributeError(f"Plugin {plugin} does not have a valid module!")
            
            _ = getattr(mirror, this.module) # Check module exists.
            
        except AttributeError:
            raise AttributeError(f"Plugin mirror does not have module {this.module}!")
        
        # Load properly with correct name
        this = _load_module_from_path(f"mirror.{this.module}.{this.name}", pluginPath)
        if this:
            setattr(getattr(mirror, this.module), this.name, this)
            this.setup()

        pass
