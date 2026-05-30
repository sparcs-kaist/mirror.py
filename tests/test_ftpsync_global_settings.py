"""Unit tests for global settings.ftpsync defaults in _config()."""
import shlex
import types
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


def _base_opts(extra: dict = None) -> dict:
    opts = {"email": "ops@example.com", "hub": "hub.example.com", "path": "/debian"}
    if extra:
        opts.update(extra)
    return opts


@pytest.fixture(autouse=True)
def _stub_mirror_conf(monkeypatch):
    import mirror
    fake_conf = MagicMock()
    fake_conf.name = "TestMirror"
    fake_conf.hostname = "ftp.example.org"
    fake_conf.logfolder = Path("/var/log/mirror")
    fake_conf.ftpsync = types.SimpleNamespace(
        maintainer="Admins <admins@example.com>",
        sponsor="Example <https://example.com>",
        country="KR",
        location="Seoul",
        throughput="1G",
        include="",
        exclude="",
    )
    monkeypatch.setattr(mirror, "conf", fake_conf, raising=False)


def _kv(key: str, value: str) -> str:
    """Return the KEY=VALUE line as it would appear in the config."""
    return f"{key}={shlex.quote(value)}"


def test_info_fields_fall_back_to_global_defaults():
    """All five INFO_* fields fall back to (non-empty) global values when absent from options."""
    pkg = _make_package(_base_opts())
    conf = _config(pkg)
    assert _kv("INFO_MAINTAINER", "Admins <admins@example.com>") in conf
    assert _kv("INFO_SPONSOR", "Example <https://example.com>") in conf
    assert _kv("INFO_COUNTRY", "KR") in conf
    assert _kv("INFO_LOCATION", "Seoul") in conf
    assert _kv("INFO_THROUGHPUT", "1G") in conf


def test_info_fields_overridden_by_per_package_options():
    """Per-package options take precedence over global INFO_* defaults."""
    opts = _base_opts({
        "maintainer": "Local Admins <local@example.com>",
        "sponsor": "Local Sponsor <https://local.example.com>",
        "country": "US",
        "location": "New York",
        "throughput": "10G",
    })
    pkg = _make_package(opts)
    conf = _config(pkg)
    assert _kv("INFO_MAINTAINER", "Local Admins <local@example.com>") in conf
    assert _kv("INFO_SPONSOR", "Local Sponsor <https://local.example.com>") in conf
    assert _kv("INFO_COUNTRY", "US") in conf
    assert _kv("INFO_LOCATION", "New York") in conf
    assert _kv("INFO_THROUGHPUT", "10G") in conf
    # Global values must not appear
    assert "Admins <admins@example.com>" not in conf
    assert "Seoul" not in conf


def test_arch_absent_when_global_empty_and_no_package_arch_keys():
    """ARCH_INCLUDE and ARCH_EXCLUDE are absent when both global and per-package values are empty."""
    pkg = _make_package(_base_opts())
    conf = _config(pkg)
    assert "ARCH_INCLUDE" not in conf
    assert "ARCH_EXCLUDE" not in conf


def test_arch_defaults_from_global_when_options_have_no_arch_keys(monkeypatch):
    """ARCH_INCLUDE/ARCH_EXCLUDE are emitted with global values when options lack arch keys."""
    import mirror
    mirror.conf.ftpsync.include = "amd64"
    mirror.conf.ftpsync.exclude = "i386"
    pkg = _make_package(_base_opts())
    conf = _config(pkg)
    assert _kv("ARCH_INCLUDE", "amd64") in conf
    assert _kv("ARCH_EXCLUDE", "i386") in conf


def test_per_package_arch_overrides_global(monkeypatch):
    """Per-package arch_include overrides the global include value."""
    import mirror
    mirror.conf.ftpsync.include = "amd64"
    mirror.conf.ftpsync.exclude = "i386"
    opts = _base_opts({"arch_include": "arm64"})
    pkg = _make_package(opts)
    conf = _config(pkg)
    assert _kv("ARCH_INCLUDE", "arm64") in conf
    assert "amd64" not in conf


def test_explicit_empty_arch_option_suppresses_global_default(monkeypatch):
    """An explicit empty string in options suppresses a non-empty global arch default."""
    import mirror
    mirror.conf.ftpsync.include = "amd64"
    mirror.conf.ftpsync.exclude = "i386"
    opts = _base_opts({"arch_include": "", "arch_exclude": ""})
    pkg = _make_package(opts)
    conf = _config(pkg)
    assert "ARCH_INCLUDE" not in conf
    assert "ARCH_EXCLUDE" not in conf


def test_info_fields_omitted_when_global_empty_and_option_absent(monkeypatch):
    """INFO_* fields are not emitted when global defaults are empty and options lack them."""
    import mirror
    mirror.conf.ftpsync.maintainer = ""
    mirror.conf.ftpsync.sponsor = ""
    mirror.conf.ftpsync.country = ""
    mirror.conf.ftpsync.location = ""
    mirror.conf.ftpsync.throughput = ""
    pkg = _make_package(_base_opts())
    conf = _config(pkg)
    assert "INFO_MAINTAINER" not in conf
    assert "INFO_SPONSOR" not in conf
    assert "INFO_COUNTRY" not in conf
    assert "INFO_LOCATION" not in conf
    assert "INFO_THROUGHPUT" not in conf


def test_explicit_empty_info_option_suppresses_non_empty_global(monkeypatch):
    """An explicit empty per-package INFO_* value suppresses a non-empty global default."""
    import mirror
    # fixture already sets maintainer to "Admins <admins@example.com>"
    opts = _base_opts({"maintainer": ""})
    pkg = _make_package(opts)
    conf = _config(pkg)
    assert "INFO_MAINTAINER" not in conf


def test_mailto_omitted_when_email_absent():
    """MAILTO is not emitted when email is absent from options."""
    opts = {"hub": "hub.example.com", "path": "/debian"}
    pkg = _make_package(opts)
    conf = _config(pkg)
    assert "MAILTO" not in conf


def test_mailto_present_when_email_given():
    """MAILTO is emitted when email is present in options."""
    pkg = _make_package(_base_opts())
    conf = _config(pkg)
    assert _kv("MAILTO", "ops@example.com") in conf


def test_hub_defaults_to_false_when_absent():
    """HUB defaults to the string 'false' when hub is absent from options."""
    opts = {"path": "/debian"}
    pkg = _make_package(opts)
    conf = _config(pkg)
    assert _kv("HUB", "false") in conf


def test_hub_uses_provided_value():
    """HUB uses the provided value when hub is present in options."""
    opts = {"hub": "true", "path": "/debian"}
    pkg = _make_package(opts)
    conf = _config(pkg)
    assert _kv("HUB", "true") in conf
