"""Regression tests for ftpsync shell-quoting (Commit 1, finding C1)."""
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mirror.sync.ftpsync import _config


def _make_package(opts: dict, src: str = "rsync.example.org", dst: str = "/tmp/dst"):
    pkg = MagicMock()
    pkg.settings.src = src
    pkg.settings.dst = dst
    pkg.settings.options = opts
    return pkg


@pytest.fixture(autouse=True)
def _stub_mirror_conf(monkeypatch):
    import mirror
    fake_conf = MagicMock()
    fake_conf.name = "TestMirror"
    fake_conf.logfolder = Path("/var/log/mirror")
    monkeypatch.setattr(mirror, "conf", fake_conf, raising=False)


@pytest.fixture
def base_opts():
    return {
        "email": "ops@example.com",
        "hub": "hub.example.com",
        "path": "/debian",
    }


def _required(extra: dict = None):
    opts = {"email": "ops@example.com", "hub": "hub.example.com", "path": "/debian"}
    if extra:
        opts.update(extra)
    return opts


def _eval_key(conf_text: str, key: str, tmp_path: Path) -> str:
    """Source the config in bash and echo the value of `key`."""
    conf_file = tmp_path / "ftpsync.conf"
    conf_file.write_text(conf_text)
    result = subprocess.run(
        ["bash", "-c", f"set -u; . {conf_file}; printf '%s' \"${key}\""],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_simple_values_round_trip(tmp_path, base_opts):
    pkg = _make_package(base_opts)
    conf = _config(pkg)
    assert _eval_key(conf, "MAILTO", tmp_path) == "ops@example.com"
    assert _eval_key(conf, "HUB", tmp_path) == "hub.example.com"
    assert _eval_key(conf, "RSYNC_PATH", tmp_path) == "/debian"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_command_substitution_is_neutralized(tmp_path, base_opts, monkeypatch):
    sentinel = tmp_path / "PWNED"
    base_opts["email"] = f'"; touch {sentinel}; #'
    pkg = _make_package(base_opts)
    conf = _config(pkg)
    value = _eval_key(conf, "MAILTO", tmp_path)
    assert value == base_opts["email"]
    assert not sentinel.exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_backtick_is_neutralized(tmp_path, base_opts):
    sentinel = tmp_path / "PWNED2"
    base_opts["hub"] = f"`touch {sentinel}`"
    pkg = _make_package(base_opts)
    conf = _config(pkg)
    _eval_key(conf, "HUB", tmp_path)
    assert not sentinel.exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_dollar_paren_is_neutralized(tmp_path, base_opts):
    sentinel = tmp_path / "PWNED3"
    base_opts["path"] = f"$(touch {sentinel})"
    pkg = _make_package(base_opts)
    conf = _config(pkg)
    _eval_key(conf, "RSYNC_PATH", tmp_path)
    assert not sentinel.exists()


def test_newline_in_value_raises(base_opts):
    base_opts["email"] = "a@b.com\nrm -rf /"
    pkg = _make_package(base_opts)
    with pytest.raises(ValueError, match="must not contain newlines"):
        _config(pkg)


def test_carriage_return_in_value_raises(base_opts):
    base_opts["hub"] = "x\rmalice"
    pkg = _make_package(base_opts)
    with pytest.raises(ValueError, match="must not contain newlines"):
        _config(pkg)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_optional_fields_are_quoted(tmp_path, base_opts):
    base_opts.update({
        "country": "KR; rm -rf /",
        "throughput": "$(echo PWNED)",
    })
    pkg = _make_package(base_opts)
    conf = _config(pkg)
    assert _eval_key(conf, "INFO_COUNTRY", tmp_path) == "KR; rm -rf /"
    assert _eval_key(conf, "INFO_THROUGHPUT", tmp_path) == "$(echo PWNED)"
