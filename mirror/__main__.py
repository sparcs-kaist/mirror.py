import click

import mirror
import mirror.command


from mirror import __version__

@click.version_option(prog_name="mirror", version=__version__)
@click.group()
def main():
    """
    Mirror.py is a tool for mirroring files and directories to a remote server.
    """
    pass

@main.command("setup")
def setup(config):
    """
    Setup the mirror environment.
    """
    mirror.command.setup()

@main.command("crontab")
@click.option("-u", "--user", default="root", help="User to run the cron job as.")
@click.option("-c", "--config", default="/etc/mirror/config.json", help="Path to the config file.")
def crontab(user, config):
    """
    Generate a crontab file from the config file.
    """
    mirror.command.crontab(user, config)

@main.command("daemon")
@click.option("--config", default="/etc/mirror/config.json", help="Path to the config file.")
def daemon(config):
    """
    Run the daemon.
    """
    mirror.command.daemon(config)

@main.command("worker")
@click.option("--config", default="/etc/mirror/config.json", help="Path to the config file.")
def worker_cmd(config):
    """
    Run the worker server.
    """
    mirror.command.worker(config)
