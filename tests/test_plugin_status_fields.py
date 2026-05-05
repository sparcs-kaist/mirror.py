"""Unit tests for status plug-in field contribution in save_stat_data() and
generate_and_save_web_status()."""
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import mirror
import mirror.config
import mirror.event
import mirror.plugin
import mirror.structure
import mirror.sync
from mirror.plugin import _register_status, status_plugin
from mirror.structure import Package, PackageSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_package(pkgid: str = "pkg1") -> Package:
    settings = PackageSettings(hidden=False, src="rsync://example.com/debian", dst="/srv/debian", options={})
    return Package(
        pkgid=pkgid,
        name=pkgid,
        status="ACTIVE",
        href=f"/{pkgid}",
        synctype="rsync",
        syncrate=3600,
        link=[],
        settings=settings,
    )


def _make_packages(*pkgids: str):
    pkgs = {pid: _make_package(pid) for pid in pkgids}
    packages = MagicMock()
    packages.keys.return_value = list(pkgids)
    packages.get.side_effect = lambda pid: pkgs.get(pid)
    for pid, pkg in pkgs.items():
        setattr(packages, pid, pkg)
    return packages


def _make_conf(name: str = "test-mirror") -> MagicMock:
    conf = MagicMock()
    conf.name = name
    return conf


# ---------------------------------------------------------------------------
# Fixture: restore plug-in state after each test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_plugin_state():
    """Snapshot stat/web hooks and restore them after each test.

    We only snapshot hooks (not the full registry) because status tests add
    temporary hooks that must not pollute subsequent tests.
    """
    orig_stat_hooks = list(mirror.plugin._status_stat_hooks)
    orig_web_hooks = list(mirror.plugin._status_web_hooks)
    orig_registry = dict(mirror.plugin._registry)
    orig_methods = list(mirror.sync.methods)
    yield
    mirror.plugin._status_stat_hooks[:] = orig_stat_hooks
    mirror.plugin._status_web_hooks[:] = orig_web_hooks
    mirror.plugin._registry.clear()
    mirror.plugin._registry.update(orig_registry)
    mirror.sync.methods[:] = orig_methods


# ---------------------------------------------------------------------------
# save_stat_data — stat hooks
# ---------------------------------------------------------------------------

def test_two_status_plugins_contribute_independently(tmp_path, monkeypatch):
    """Two status plug-ins writing different fields both appear under their own key."""
    stat_file = tmp_path / "stat.json"

    def hook_a(package):
        return {"field-a": "value-a"}

    def hook_b(package):
        return {"field-b": "value-b"}

    record_a = status_plugin(name="plugin-a", extend_stat_fields=hook_a)
    record_b = status_plugin(name="plugin-b", extend_stat_fields=hook_b)
    _register_status(record_a)
    _register_status(record_b)

    monkeypatch.setattr(mirror, "packages", _make_packages("pkg1"), raising=False)
    monkeypatch.setattr(mirror, "conf", _make_conf(), raising=False)
    monkeypatch.setattr(mirror.config, "STAT_DATA_PATH", stat_file, raising=False)
    monkeypatch.setattr("mirror.event.post_event", lambda *a, **kw: None)

    mirror.config.save_stat_data()

    data = json.loads(stat_file.read_text())
    plugins = data["packages"]["pkg1"]["status"]["statusinfo"]["plugins"]
    assert plugins["plugin-a"] == {"field-a": "value-a"}
    assert plugins["plugin-b"] == {"field-b": "value-b"}


def test_same_field_name_no_overwrite(tmp_path, monkeypatch):
    """Two plug-ins contributing the same field name each store under their own key."""
    stat_file = tmp_path / "stat.json"

    def hook_x(package):
        return {"shared-field": "from-x"}

    def hook_y(package):
        return {"shared-field": "from-y"}

    _register_status(status_plugin(name="plugin-x", extend_stat_fields=hook_x))
    _register_status(status_plugin(name="plugin-y", extend_stat_fields=hook_y))

    monkeypatch.setattr(mirror, "packages", _make_packages("pkg1"), raising=False)
    monkeypatch.setattr(mirror, "conf", _make_conf(), raising=False)
    monkeypatch.setattr(mirror.config, "STAT_DATA_PATH", stat_file, raising=False)
    monkeypatch.setattr("mirror.event.post_event", lambda *a, **kw: None)

    mirror.config.save_stat_data()

    data = json.loads(stat_file.read_text())
    plugins = data["packages"]["pkg1"]["status"]["statusinfo"]["plugins"]
    assert plugins["plugin-x"]["shared-field"] == "from-x"
    assert plugins["plugin-y"]["shared-field"] == "from-y"


def test_raising_hook_does_not_abort_save(tmp_path, monkeypatch, caplog):
    """A hook that raises must not prevent the save or other hooks from running."""
    stat_file = tmp_path / "stat.json"

    def bad_hook(package):
        raise RuntimeError("hook error")

    def good_hook(package):
        return {"good-field": "ok"}

    _register_status(status_plugin(name="bad-plugin", extend_stat_fields=bad_hook))
    _register_status(status_plugin(name="good-plugin", extend_stat_fields=good_hook))

    monkeypatch.setattr(mirror, "packages", _make_packages("pkg1"), raising=False)
    monkeypatch.setattr(mirror, "conf", _make_conf(), raising=False)
    monkeypatch.setattr(mirror.config, "STAT_DATA_PATH", stat_file, raising=False)
    monkeypatch.setattr("mirror.event.post_event", lambda *a, **kw: None)

    with caplog.at_level(logging.WARNING, logger="mirror"):
        mirror.config.save_stat_data()

    # File must have been written despite the bad hook
    assert stat_file.exists()
    data = json.loads(stat_file.read_text())
    plugins = data["packages"]["pkg1"]["status"]["statusinfo"]["plugins"]
    # Good hook still contributed
    assert plugins["good-plugin"] == {"good-field": "ok"}
    # Bad plugin key must not appear
    assert "bad-plugin" not in plugins


# ---------------------------------------------------------------------------
# generate_and_save_web_status — web hooks
# ---------------------------------------------------------------------------

def test_web_status_hook_contributes_under_plugin_name(tmp_path, monkeypatch):
    """Web status hooks appear nested under web_status[pkg_id]['plugins'][plugin_name]."""
    status_file = tmp_path / "status.json"

    def web_hook(package):
        return {"web-field": "web-value"}

    _register_status(status_plugin(name="web-plugin", extend_web_status_fields=web_hook))

    monkeypatch.setattr(mirror, "packages", _make_packages("pkg1"), raising=False)
    monkeypatch.setattr(mirror, "conf", _make_conf(), raising=False)
    monkeypatch.setattr(mirror.config, "STATUS_PATH", status_file, raising=False)
    monkeypatch.setattr("mirror.toolbox.format_iso_duration", lambda s: "PT1H")
    monkeypatch.setattr("mirror.event.post_event", lambda *a, **kw: None)

    mirror.config.generate_and_save_web_status()

    data = json.loads(status_file.read_text())
    plugins = data["pkg1"]["plugins"]
    assert plugins["web-plugin"] == {"web-field": "web-value"}


def test_two_web_hooks_independent(tmp_path, monkeypatch):
    """Two web hooks for different plug-ins appear side by side without collision."""
    status_file = tmp_path / "status.json"

    def hook_m(package):
        return {"m-field": "m-val"}

    def hook_n(package):
        return {"n-field": "n-val"}

    _register_status(status_plugin(name="plugin-m", extend_web_status_fields=hook_m))
    _register_status(status_plugin(name="plugin-n", extend_web_status_fields=hook_n))

    monkeypatch.setattr(mirror, "packages", _make_packages("pkg1"), raising=False)
    monkeypatch.setattr(mirror, "conf", _make_conf(), raising=False)
    monkeypatch.setattr(mirror.config, "STATUS_PATH", status_file, raising=False)
    monkeypatch.setattr("mirror.toolbox.format_iso_duration", lambda s: "PT1H")
    monkeypatch.setattr("mirror.event.post_event", lambda *a, **kw: None)

    mirror.config.generate_and_save_web_status()

    data = json.loads(status_file.read_text())
    plugins = data["pkg1"]["plugins"]
    assert plugins["plugin-m"] == {"m-field": "m-val"}
    assert plugins["plugin-n"] == {"n-field": "n-val"}
