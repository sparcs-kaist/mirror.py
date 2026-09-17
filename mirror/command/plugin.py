"""CLI group for plug-in management commands."""

import json
import sys
from pathlib import Path

import click
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.shortcuts import print_formatted_text

import mirror
import mirror.plugin
import mirror.structure
from mirror.plugin import ConfigCreateResult


def _bootstrap_config(config_path: Path) -> None:
    """Populate ``mirror.conf`` and register external plug-ins without heavy side effects.

    This deliberately avoids calling ``mirror.config.load``, which rewrites
    ``stat.json``, builds the full package list, and loads the web status file.
    For plug-in sub-commands that only need to know which plug-ins are available,
    those side effects are unnecessary and potentially destructive at CLI-invocation
    time (e.g. when called outside the daemon context).

    Args:
        config_path(pathlib.Path): Path to the main ``config.json`` file.
    """
    config_dict = json.loads(config_path.read_text())
    mirror.conf = mirror.structure.Config.load_from_dict(config_dict)
    mirror.plugin.load_external_plugins(mirror.conf.plugins)


def _run_create_config(plugin_name: str, force: bool) -> ConfigCreateResult:
    """Invoke the create_config callback for a named plug-in.

    Args:
        plugin_name(str): Registered name of the plug-in.
        force(bool): Whether to overwrite an existing config file.

    Return:
        result(ConfigCreateResult): Dataclass with ``path`` and ``created`` fields.

    Raises:
        KeyError: If no plug-in with ``plugin_name`` is registered.
        ValueError: If the plug-in does not support config creation.
    """
    record = mirror.plugin.get_record(plugin_name)
    if record is None:
        raise KeyError(f"No plug-in named '{plugin_name}' is registered")
    if record.create_config is None:
        raise ValueError(f"Plug-in '{plugin_name}' does not support config creation")
    return record.create_config(force=force)


@click.group("plugin")
def plugin_group() -> None:
    """Plug-in management commands."""


@plugin_group.group("config")
def plugin_config_group() -> None:
    """Plug-in config file commands."""


@plugin_config_group.command("create", no_args_is_help=True)
@click.argument("plugin")
@click.option(
    "--config", "config_path",
    default="/etc/mirror/config.json",
    show_default=True,
    help="Path to the main config file.",
)
@click.option(
    "--force/--no-force",
    default=False,
    show_default=True,
    help="Overwrite the plug-in config file if it already exists.",
)
def create_cmd(plugin: str, config_path: str, force: bool) -> None:
    """Create the default config file for a plug-in."""
    try:
        _bootstrap_config(Path(config_path))
    except FileNotFoundError as exc:
        print_formatted_text(FormattedText([
            ("class:error", f"[ERROR] Config file not found: {exc}")
        ]))
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print_formatted_text(FormattedText([
            ("class:error", f"[ERROR] Failed to parse config file: {exc}")
        ]))
        sys.exit(1)
    except Exception as exc:
        print_formatted_text(FormattedText([
            ("class:error", f"[ERROR] Failed to load config: {exc}")
        ]))
        sys.exit(1)

    try:
        result = _run_create_config(plugin, force)
    except (KeyError, ValueError) as exc:
        message = exc.args[0] if exc.args else str(exc)
        print_formatted_text(FormattedText([
            ("class:error", f"[ERROR] {message}")
        ]))
        sys.exit(1)
    except Exception as exc:
        print_formatted_text(FormattedText([
            ("class:error", f"[ERROR] Failed to create config for '{plugin}': {exc}")
        ]))
        sys.exit(1)

    if result.created:
        print_formatted_text(FormattedText([
            ("class:success", f"[OK] Created config at {result.path}")
        ]))
    else:
        print_formatted_text(FormattedText([
            ("class:warning", f"[SKIP] Config already exists at {result.path} (use --force to overwrite)")
        ]))
    sys.exit(0)
