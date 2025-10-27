import click
import logging
import pathlib

import mirror
import mirror.command
import mirror.config


__version__ = "1.0.0-pre3"
mirror.__version__ = __version__

@click.version_option(prog_name="mirror", version=__version__)
@click.group()
def main():
    """
    Mirror.py is a tool for mirroring files and directories to a remote server.
    """
    pass

@main.command("crontab")
@click.option("-u", "--user", default="root", help="User to run the cron job as.")
@click.option("-c", "--config", default="config.json", help="Path to the config file.")
def crontab(user, config):
    """
    Generate a crontab file from the config file.
    """
    mirror.command.crontab(user, config)

@main.command("daemon")
@click.argument("config", default="/etc/mirror/daemon.json")
def daemon(config):
    """
    Run the daemon.
    """
    mirror.command.daemon(config)
