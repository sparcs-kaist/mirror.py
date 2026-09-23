"""Regression tests for ftpsync shell-quoting (Commit 1, finding C1)."""
import shutil
import subprocess
from pathlib import Path

import pytest

from mirror.sync.ftpsync import _config, _split_rsync_src


pytestmark = pytest.mark.usefixtures("stub_ftpsync_conf")


@pytest.fixture
def base_opts():
    return {
        "email": "ops@example.com",
        "hub": "hub.example.com",
        "path": "/debian",
    }


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
def test_simple_values_round_trip(tmp_path, base_opts, ftpsync_package_factory):
    pkg = ftpsync_package_factory(base_opts)
    conf = _config(pkg)
    assert _eval_key(conf, "MAILTO", tmp_path) == "ops@example.com"
    assert _eval_key(conf, "HUB", tmp_path) == "hub.example.com"
    assert _eval_key(conf, "RSYNC_PATH", tmp_path) == "/debian"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_command_substitution_is_neutralized(
    tmp_path,
    base_opts,
    monkeypatch,
    ftpsync_package_factory,
):
    sentinel = tmp_path / "PWNED"
    base_opts["email"] = f'"; touch {sentinel}; #'
    pkg = ftpsync_package_factory(base_opts)
    conf = _config(pkg)
    value = _eval_key(conf, "MAILTO", tmp_path)
    assert value == base_opts["email"]
    assert not sentinel.exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_backtick_is_neutralized(tmp_path, base_opts, ftpsync_package_factory):
    sentinel = tmp_path / "PWNED2"
    base_opts["hub"] = f"`touch {sentinel}`"
    pkg = ftpsync_package_factory(base_opts)
    conf = _config(pkg)
    _eval_key(conf, "HUB", tmp_path)
    assert not sentinel.exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_dollar_paren_is_neutralized(tmp_path, base_opts, ftpsync_package_factory):
    sentinel = tmp_path / "PWNED3"
    base_opts["path"] = f"$(touch {sentinel})"
    pkg = ftpsync_package_factory(base_opts)
    conf = _config(pkg)
    _eval_key(conf, "RSYNC_PATH", tmp_path)
    assert not sentinel.exists()


def test_newline_in_value_raises(base_opts, ftpsync_package_factory):
    base_opts["email"] = "a@b.com\nrm -rf /"
    pkg = ftpsync_package_factory(base_opts)
    with pytest.raises(ValueError, match="must not contain newlines"):
        _config(pkg)


def test_carriage_return_in_value_raises(base_opts, ftpsync_package_factory):
    base_opts["hub"] = "x\rmalice"
    pkg = ftpsync_package_factory(base_opts)
    with pytest.raises(ValueError, match="must not contain newlines"):
        _config(pkg)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_logdir_and_name_can_be_overridden_for_run(
    tmp_path,
    base_opts,
    ftpsync_package_factory,
):
    pkg = ftpsync_package_factory(base_opts)
    log_dir = tmp_path / "ftpsync-runs" / "pkg" / "token"
    conf = _config(pkg, log_dir=log_dir, log_name="pkg-token")
    assert _eval_key(conf, "LOGDIR", tmp_path) == str(log_dir)
    assert _eval_key(conf, "NAME", tmp_path) == "pkg-token"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_optional_fields_are_quoted(tmp_path, base_opts, ftpsync_package_factory):
    base_opts.update({
        "country": "KR; rm -rf /",
        "throughput": "$(echo PWNED)",
    })
    pkg = ftpsync_package_factory(base_opts)
    conf = _config(pkg)
    assert _eval_key(conf, "INFO_COUNTRY", tmp_path) == "KR; rm -rf /"
    assert _eval_key(conf, "INFO_THROUGHPUT", tmp_path) == "$(echo PWNED)"


def test_split_rsync_src_url_splits_host_and_path():
    host, path = _split_rsync_src("rsync://syncproxy2.wna.debian.org/debian", {})
    assert host == "syncproxy2.wna.debian.org"
    assert path == "debian"


def test_split_rsync_src_url_keeps_trailing_slash():
    host, path = _split_rsync_src("rsync://host.example.org/debian/", {})
    assert host == "host.example.org"
    assert path == "debian/"


def test_split_rsync_src_explicit_path_overrides_url():
    host, path = _split_rsync_src("rsync://host.example.org/debian", {"path": "ubuntu"})
    assert host == "host.example.org"
    assert path == "ubuntu"


def test_split_rsync_src_bare_host_uses_path_option():
    host, path = _split_rsync_src("rsync.example.org", {"path": "/debian"})
    assert host == "rsync.example.org"
    assert path == "/debian"


def test_split_rsync_src_url_without_host_raises():
    with pytest.raises(ValueError):
        _split_rsync_src("rsync:///debian", {})


def test_split_rsync_src_bare_host_without_path_raises():
    with pytest.raises(ValueError):
        _split_rsync_src("rsync.example.org", {})


def test_config_accepts_rsync_url_without_path_option(
    tmp_path,
    ftpsync_package_factory,
):
    pkg = ftpsync_package_factory(
        {"hub": "false"},
        src="rsync://syncproxy2.wna.debian.org/debian",
    )
    conf = _config(pkg)
    assert "RSYNC_HOST='syncproxy2.wna.debian.org'" in conf or \
        "RSYNC_HOST=syncproxy2.wna.debian.org" in conf
    assert "RSYNC_PATH=debian" in conf


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_mirrorname_uses_config_hostname(
    tmp_path,
    base_opts,
    ftpsync_package_factory,
):
    pkg = ftpsync_package_factory(base_opts)
    conf = _config(pkg)
    assert _eval_key(conf, "MIRRORNAME", tmp_path) == "ftp.example.org"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_tracehost_defaults_to_config_hostname(
    tmp_path,
    base_opts,
    ftpsync_package_factory,
):
    pkg = ftpsync_package_factory(base_opts)
    conf = _config(pkg)
    assert _eval_key(conf, "TRACEHOST", tmp_path) == "ftp.example.org"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_tracehost_ignores_package_option(
    tmp_path,
    base_opts,
    ftpsync_package_factory,
):
    base_opts["tracehost"] = "mirror.override.org"
    pkg = ftpsync_package_factory(base_opts)
    conf = _config(pkg)
    assert _eval_key(conf, "TRACEHOST", tmp_path) == "ftp.example.org"


def test_tracehost_omitted_when_hostname_empty(
    monkeypatch,
    base_opts,
    ftpsync_package_factory,
):
    import mirror
    monkeypatch.setattr(mirror.conf, "hostname", "", raising=False)
    pkg = ftpsync_package_factory(base_opts)
    conf = _config(pkg)
    assert "TRACEHOST=" not in conf
