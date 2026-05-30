"""Unit tests verifying that settings.ftpsync is fully optional in Config.load_from_dict."""
import pytest

import mirror.structure


def _minimal_config() -> dict:
    """Return the smallest valid config dict that omits settings.ftpsync entirely."""
    return {
        "settings": {
            "logfolder": "/tmp/mirror_logs",
            "webroot": "/tmp/mirror_web",
            "statusfile": "/tmp/mirror_status.json",
            "localtimezone": "UTC",
            "logger": {},
        }
    }


def test_config_loads_without_ftpsync_block():
    """Config.load_from_dict must not raise when settings.ftpsync is absent."""
    conf = mirror.structure.Config.load_from_dict(_minimal_config())
    assert conf is not None


def test_config_ftpsync_defaults_to_empty_strings():
    """When settings.ftpsync is absent, all FTPSync fields default to empty string."""
    conf = mirror.structure.Config.load_from_dict(_minimal_config())
    assert conf.ftpsync.maintainer == ""
    assert conf.ftpsync.sponsor == ""
    assert conf.ftpsync.country == ""
    assert conf.ftpsync.location == ""
    assert conf.ftpsync.throughput == ""
    assert conf.ftpsync.include == ""
    assert conf.ftpsync.exclude == ""
