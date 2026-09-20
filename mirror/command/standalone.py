"""CLI command for running a single sync directly without the daemon."""

import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from typing import Optional

import click
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.shortcuts import print_formatted_text


def _coerce_scalar(value: str):
    """Coerce a string value to bool, int, or str.

    Converts "true"/"false" (case-insensitive) to bool, optional-leading-minus
    all-digits strings to int, and everything else is returned as str.

    Args:
        value(str): Raw string value to coerce.

    Return:
        coerced: Bool, int, or str depending on content.
    """
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    # Optional leading minus, then all digits.
    stripped = value[1:] if value.startswith("-") else value
    if stripped.isdigit():
        return int(value)
    return value


def _parse_options(option: tuple, options_json: Optional[str]) -> dict:
    """Parse -o key=value entries and optional JSON override into an options dict.

    Each entry in option must contain "=". Entries of the form "key[]=value" always
    append to a list at options[key] (a single "key[]=v" yields [v]). Entries of the
    form "key=value" set a scalar. Scalar values (including list elements) are coerced:
    "true"/"false" -> bool, all-digits (with optional leading minus) -> int, else str.

    If options_json is given it is parsed as a JSON object and merged over the -o
    result (options_json values take precedence per key).

    Args:
        option(tuple): Sequence of "key=value" or "key[]=value" strings from -o flags.
        options_json(str, optional): JSON object string merged after -o parsing.

    Return:
        opts(dict): Merged options dictionary.

    Raises:
        click.UsageError: If any entry has no "=" or options_json is not a JSON object.
    """
    opts: dict = {}

    for entry in option:
        if "=" not in entry:
            raise click.UsageError(f"-o entry must be 'key=value' or 'key[]=value', got: {entry!r}")
        eq_pos = entry.index("=")
        raw_key = entry[:eq_pos]
        raw_val = entry[eq_pos + 1:]

        if raw_key.endswith("[]"):
            key = raw_key[:-2]
            coerced = _coerce_scalar(raw_val)
            existing = opts.get(key)
            if isinstance(existing, list):
                existing.append(coerced)
            else:
                opts[key] = [coerced]
        else:
            opts[raw_key] = _coerce_scalar(raw_val)

    if options_json is not None:
        try:
            parsed = json.loads(options_json)
        except json.JSONDecodeError as exc:
            raise click.UsageError(f"--options-json is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise click.UsageError("--options-json must be a JSON object (dict)")
        opts.update(parsed)

    return opts


def _resolve_state_dir(state_dir: Optional[str]) -> Path:
    """Resolve the directory to use for ephemeral sync state (e.g. ftpsync temp tree).

    If state_dir is given, use it (creating it if needed); raises if not writable.
    Otherwise, if mirror.STATE_PATH exists and is writable, use that. Otherwise,
    create a temporary directory with prefix "mirror_standalone_".

    Args:
        state_dir(str, optional): Explicit path supplied via --state-dir, or None.

    Return:
        resolved(Path): Writable directory path to use as mirror.STATE_PATH.
    """
    import mirror

    if state_dir is not None:
        p = Path(state_dir)
        p.mkdir(parents=True, exist_ok=True)
        if not os.access(p, os.W_OK):
            raise click.UsageError(f"--state-dir {state_dir!r} is not writable")
        return p

    default = mirror.STATE_PATH
    if default.exists() and os.access(default, os.W_OK):
        return default

    return Path(tempfile.mkdtemp(prefix="mirror_standalone_"))


def _build_minimal_config():
    """Build a minimal Config instance without any file IO.

    All required Config fields are populated with safe defaults suitable for
    a standalone run. logfolder and webroot are set to a writable temp directory.
    uid and gid are set to the current process owner.

    Return:
        conf(mirror.structure.Config): Minimal Config instance.
    """
    import mirror.structure

    tmpdir = Path(tempfile.mkdtemp(prefix="mirror_standalone_conf_"))
    return mirror.structure.Config(
        name="standalone",
        hostname=socket.getfqdn(),
        lastsettingmodified=0,
        errorcontinuetime=0,
        logfolder=tmpdir,
        webroot=tmpdir,
        statusfile=tmpdir / "status.json",
        ftpsync=mirror.structure.Config.FTPSync(),
        uid=os.getuid(),
        gid=os.getgid(),
        maintainer={},
        localtimezone="UTC",
        logger={},
    )


def _print_error(message: str) -> None:
    """Print an error message using prompt_toolkit with a plain-text fallback.

    Args:
        message(str): Error message to display.
    """
    try:
        print_formatted_text(FormattedText([("class:error", message)]))
    except Exception:
        print(message, file=sys.stderr)


@click.command("standalone", no_args_is_help=True)
@click.argument("synctype")
@click.option("--src", default=None, help="Sync source URL or path.")
@click.option("--dst", default=None, help="Local destination directory.")
@click.option(
    "-o", "--option", "option", multiple=True, metavar="KEY=VALUE",
    help=(
        "Set a sync option. Use 'key=value' for a scalar or 'key[]=value' "
        "for a list element (repeatable). Scalar values are coerced: "
        "true/false -> bool, digits -> int."
    ),
)
@click.option(
    "--options-json", default=None,
    help="JSON object merged over -o options (takes per-key precedence).",
)
@click.option("--uid", default=None, type=int, help="User ID for the subprocess.")
@click.option("--gid", default=None, type=int, help="Group ID for the subprocess.")
@click.option("--nice", default=0, type=int, help="Niceness value for the subprocess.")
@click.option(
    "--config", default=None,
    help="Path to an existing config.json for global settings (hostname, ftpsync, etc.).",
)
@click.option(
    "--id", "pkgid", default="standalone",
    help="Ad-hoc package ID used internally (default: standalone).",
)
@click.option("--state-dir", default=None, help="Directory for ephemeral sync state.")
def standalone(
    synctype: str,
    src: Optional[str],
    dst: Optional[str],
    option: tuple,
    options_json: Optional[str],
    uid: Optional[int],
    gid: Optional[int],
    nice: int,
    config: Optional[str],
    pkgid: str,
    state_dir: Optional[str],
) -> None:
    """Run a single sync directly without the daemon.

    SYNCTYPE is the sync method to use (e.g. rsync, ftpsync, lftp, local).
    """
    import mirror
    import mirror.structure
    import mirror.sync
    import mirror.plugin

    # 1. Validate synctype.
    if synctype not in mirror.sync.methods:
        click.echo(
            f"Error: unknown synctype {synctype!r}. "
            f"Available: {', '.join(mirror.sync.methods)}",
            err=True,
        )
        sys.exit(2)

    # 2. Build or load config.
    if config is not None:
        config_text = Path(config).read_text(encoding="utf-8")
        mirror.conf = mirror.structure.Config.load_from_dict(json.loads(config_text))
    else:
        mirror.conf = _build_minimal_config()

    mirror.conf.uid = uid if uid is not None else os.getuid()
    mirror.conf.gid = gid if gid is not None else os.getgid()

    # 2b. Resolve state dir for ftpsync temp tree BEFORE execute().
    mirror.STATE_PATH = _resolve_state_dir(state_dir)

    # 3. Build options and ad-hoc package.
    opts = _parse_options(option, options_json)
    cfg = {
        "id": pkgid,
        "name": pkgid,
        "href": "",
        "synctype": synctype,
        "syncrate": "",
        "link": [],
        "settings": {
            "hidden": True,
            "src": src or "",
            "dst": dst or "",
            "options": opts,
        },
    }
    mirror.packages = mirror.structure.Packages({pkgid: cfg})
    package = mirror.packages.get(pkgid)

    # 4. Run the sync.
    mirror.sync.set_standalone_mode(True)
    record = mirror.plugin.get_record(synctype)
    try:
        record.execute(package, mirror.log, "standalone")
    except Exception as exc:
        _print_error(f"[ERROR] standalone {synctype} failed: {exc}")

    result = mirror.sync.get_standalone_result(pkgid)
    success, rc = result if result is not None else (False, None)

    # 5. Exit with appropriate code.
    sys.exit(0 if success else (rc if isinstance(rc, int) and rc else 1))
