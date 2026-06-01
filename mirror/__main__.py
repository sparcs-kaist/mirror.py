import click

import mirror
import mirror.command

from mirror import __version__
from mirror.command.config import config_group
from mirror.command.worker_execute import worker_execute_group

@click.version_option(prog_name="mirror", version=__version__)
@click.group()
def main() -> None:
    """Mirror.py is a tool for mirroring files and directories to a remote server."""
    pass

@main.command("setup")
def setup() -> None:
    """Setup the mirror environment."""
    mirror.command.setup()

@main.command("crontab")
@click.option("-u", "--user", default="root", help="User to run the cron job as.")
@click.option("-c", "--config", default="/etc/mirror/config.json", help="Path to the config file.")
def crontab(user: str, config: str) -> None:
    """Generate a crontab file from the config file.

    Args:
        user(str): User to run the cron job as.
        config(str): Path to the config file.
    """
    mirror.command.crontab(user, config)

@main.command("daemon")
@click.option("--config", default="/etc/mirror/config.json", help="Path to the config file.")
def daemon(config: str) -> None:
    """Run the daemon.

    Args:
        config(str): Path to the config file.
    """
    mirror.command.daemon(config)

@main.command("worker")
@click.option("--config", default="/etc/mirror/config.json", help="Path to the config file.")
def worker_cmd(config: str) -> None:
    """Run the worker server.

    Args:
        config(str): Path to the config file.
    """
    mirror.command.worker(config)

@main.command("push")
@click.argument("packageid")
@click.option("--config", default="/etc/mirror/config.json", help="Path to the config file.")
def push(packageid: str, config: str) -> None:
    """Trigger a one-shot push sync of the given package.

    Args:
        packageid(str): Package ID to push.
        config(str): Path to the config file.
    """
    mirror.command.push(packageid, config)

@main.command("tui")
@click.option("--socket", "socket_path", default=None,
              help="Master socket path (default: runtime metadata).")
def tui(socket_path: str | None) -> None:
    """Run the real-time mirror status TUI."""
    mirror.command.tui(socket_path)


main.add_command(config_group)
main.add_command(worker_execute_group)


if __name__ == "__main__":
    main()
