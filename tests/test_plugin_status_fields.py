"""Unit tests for status plug-in field contribution in save_stat_data() and
generate_and_save_web_status()."""
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import mirror
import mirror.config
import mirror.event
import mirror.plugin
import mirror.structure
import mirror.sync
from mirror.plugin import (
    StatusOutput,
    _register_status,
    _unregister,
    status_plugin,
    sync_plugin,
)
from mirror.structure import Package, PackageSettings, PluginSettings


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
    """Snapshot all plug-in state stores and restore them after each test.

    Covers the original hooks/registry/methods plus the new transform-owner
    and named-output state introduced in the transform/output feature.
    """
    orig_stat_hooks = list(mirror.plugin._status_stat_hooks)
    orig_web_hooks = list(mirror.plugin._status_web_hooks)
    orig_registry = dict(mirror.plugin._registry)
    orig_methods = list(mirror.sync.methods)
    orig_stat_transform_owner = mirror.plugin._stat_transform_owner
    orig_web_status_transform_owner = mirror.plugin._web_status_transform_owner
    orig_status_outputs = dict(mirror.plugin._status_outputs)
    yield
    mirror.plugin._status_stat_hooks[:] = orig_stat_hooks
    mirror.plugin._status_web_hooks[:] = orig_web_hooks
    mirror.plugin._registry.clear()
    mirror.plugin._registry.update(orig_registry)
    mirror.sync.methods[:] = orig_methods
    mirror.plugin._stat_transform_owner = orig_stat_transform_owner
    mirror.plugin._web_status_transform_owner = orig_web_status_transform_owner
    mirror.plugin._status_outputs.clear()
    mirror.plugin._status_outputs.update(orig_status_outputs)


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


# ---------------------------------------------------------------------------
# New tests — transform, StatusOutput, _unregister, atomic write
# ---------------------------------------------------------------------------

def test_transform_stat_payload_after_extend(tmp_path: Path, monkeypatch) -> None:
    """extend_stat_fields runs first, then transform_stat_payload sees the extended dict."""
    stat_file = tmp_path / "stat.json"

    def extend_hook(package):
        return {"my_key": "extended"}

    saw_extended_at_transform_time = {"value": False}

    def transform_fn(payload: dict) -> dict:
        # Verify the extend hook's contribution is already present when transform runs.
        try:
            plugins = payload["packages"]["pkg1"]["status"]["statusinfo"]["plugins"]
            saw_extended_at_transform_time["value"] = (
                plugins.get("t-stat") == {"my_key": "extended"}
            )
        except (KeyError, TypeError):
            pass
        payload["transformed"] = True
        return payload

    record = status_plugin(
        name="t-stat",
        extend_stat_fields=extend_hook,
        transform_stat_payload=transform_fn,
    )
    _register_status(record)

    monkeypatch.setattr(mirror, "packages", _make_packages("pkg1"), raising=False)
    monkeypatch.setattr(mirror, "conf", _make_conf(), raising=False)
    monkeypatch.setattr(mirror.config, "STAT_DATA_PATH", stat_file, raising=False)
    monkeypatch.setattr("mirror.event.post_event", lambda *a, **kw: None)

    mirror.config.save_stat_data()

    data = json.loads(stat_file.read_text())
    # Extended key must be present (extend ran before transform)
    plugins = data["packages"]["pkg1"]["status"]["statusinfo"]["plugins"]
    assert plugins["t-stat"] == {"my_key": "extended"}
    # Transform result must also be present at the top level
    assert data["transformed"] is True
    # And the transform must have observed the extended payload at call time
    # (i.e. ordering is extend → transform, not the reverse).
    assert saw_extended_at_transform_time["value"], (
        "transform ran before extend; ordering invariant broken"
    )


def test_transform_web_status_payload_after_extend(tmp_path: Path, monkeypatch) -> None:
    """extend_web_status_fields runs first, then transform_web_status_payload modifies the result."""
    status_file = tmp_path / "status.json"

    def extend_hook(package):
        return {"web_key": "extended"}

    saw_extended_at_transform_time = {"value": False}

    def transform_fn(payload: dict) -> dict:
        try:
            plugins = payload["pkg1"]["plugins"]
            saw_extended_at_transform_time["value"] = (
                plugins.get("t-web") == {"web_key": "extended"}
            )
        except (KeyError, TypeError):
            pass
        payload["web_transformed"] = True
        return payload

    record = status_plugin(
        name="t-web",
        extend_web_status_fields=extend_hook,
        transform_web_status_payload=transform_fn,
    )
    _register_status(record)

    monkeypatch.setattr(mirror, "packages", _make_packages("pkg1"), raising=False)
    monkeypatch.setattr(mirror, "conf", _make_conf(), raising=False)
    monkeypatch.setattr(mirror.config, "STATUS_PATH", status_file, raising=False)
    monkeypatch.setattr("mirror.toolbox.format_iso_duration", lambda s: "PT1H")
    monkeypatch.setattr("mirror.event.post_event", lambda *a, **kw: None)

    mirror.config.generate_and_save_web_status()

    data = json.loads(status_file.read_text())
    plugins = data["pkg1"]["plugins"]
    assert plugins["t-web"] == {"web_key": "extended"}
    assert data["web_transformed"] is True
    assert saw_extended_at_transform_time["value"], (
        "transform ran before extend; ordering invariant broken"
    )


def test_duplicate_stat_transform_owner_raises() -> None:
    """A second plug-in registering transform_stat_payload raises ValueError."""
    _register_status(status_plugin(name="owner-a", transform_stat_payload=lambda p: p))

    with pytest.raises(ValueError, match="stat transform already owned by 'owner-a'"):
        _register_status(status_plugin(name="owner-b", transform_stat_payload=lambda p: p))

    # First plug-in must still be properly registered
    assert "owner-a" in mirror.plugin._registry
    assert mirror.plugin._stat_transform_owner is not None
    assert mirror.plugin._stat_transform_owner[0] == "owner-a"


def test_duplicate_web_status_transform_owner_raises() -> None:
    """A second plug-in registering transform_web_status_payload raises ValueError."""
    _register_status(status_plugin(name="web-owner-a", transform_web_status_payload=lambda p: p))

    with pytest.raises(ValueError, match="web_status transform already owned by 'web-owner-a'"):
        _register_status(status_plugin(name="web-owner-b", transform_web_status_payload=lambda p: p))

    assert "web-owner-a" in mirror.plugin._registry
    assert mirror.plugin._web_status_transform_owner is not None
    assert mirror.plugin._web_status_transform_owner[0] == "web-owner-a"


def test_register_status_atomic_validation_no_partial_state() -> None:
    """When registration fails mid-way, no partial state is left behind."""
    # Pre-claim the stat transform owner slot.
    _register_status(status_plugin(name="existing-owner", transform_stat_payload=lambda p: p))

    snapshot_stat_hooks = list(mirror.plugin._status_stat_hooks)

    def extend_fn(pkg):
        return {"k": "v"}

    conflicting = status_plugin(
        name="conflicting-plugin",
        extend_stat_fields=extend_fn,
        transform_stat_payload=lambda p: p,
    )

    with pytest.raises(ValueError):
        _register_status(conflicting)

    # Registry must not contain the failed plug-in.
    assert "conflicting-plugin" not in mirror.plugin._registry
    # The extend hook must NOT have been appended.
    hook_names = [n for n, _ in mirror.plugin._status_stat_hooks]
    assert "conflicting-plugin" not in hook_names
    # Hook list must be identical to snapshot (no partial mutation).
    assert [n for n, _ in mirror.plugin._status_stat_hooks] == [n for n, _ in snapshot_stat_hooks]


def test_status_output_writes_to_default_path(tmp_path: Path, monkeypatch) -> None:
    """StatusOutput.build result is written to default_path."""
    output_file = tmp_path / "my-output.json"
    pkg1 = _make_package("pkg1")
    packages_mock = _make_packages("pkg1")
    packages_mock.values = MagicMock(return_value=[pkg1])

    def build_fn(packages):
        return {"output": "data"}

    output = StatusOutput(name="my-output", default_path=str(output_file), build=build_fn)
    record = status_plugin(name="output-plugin", outputs=[output])
    _register_status(record)

    monkeypatch.setattr(mirror, "packages", packages_mock, raising=False)
    monkeypatch.setattr(mirror, "conf", _make_conf(), raising=False)

    mirror.config._write_status_outputs()

    assert output_file.exists()
    data = json.loads(output_file.read_text())
    assert data == {"output": "data"}


def test_status_output_config_path_override(tmp_path: Path, monkeypatch) -> None:
    """When config_path_key is set and the config has that key, the override path is used."""
    default_file = tmp_path / "default.json"
    override_file = tmp_path / "override.json"

    pkg1 = _make_package("pkg1")
    packages_mock = _make_packages("pkg1")
    packages_mock.values = MagicMock(return_value=[pkg1])

    def build_fn(packages):
        return {"override": True}

    output = StatusOutput(
        name="cfg-output",
        default_path=str(default_file),
        build=build_fn,
        config_path_key="output_path",
    )
    record = status_plugin(name="cfg-plugin", outputs=[output])
    _register_status(record)

    conf_mock = _make_conf()
    plugin_settings = {"cfg-plugin": PluginSettings(enabled=True, config={"output_path": str(override_file)})}
    conf_mock.plugins = plugin_settings
    monkeypatch.setattr(mirror, "packages", packages_mock, raising=False)
    monkeypatch.setattr(mirror, "conf", conf_mock, raising=False)

    mirror.config._write_status_outputs()

    assert override_file.exists()
    assert not default_file.exists()
    data = json.loads(override_file.read_text())
    assert data == {"override": True}


def test_status_output_config_path_key_falls_back_when_no_config_block(tmp_path: Path, monkeypatch) -> None:
    """If config_path_key is set but the plug-in has no entry in mirror.conf.plugins,
    the writer falls back to default_path without crashing."""
    default_file = tmp_path / "default.json"

    pkg1 = _make_package("pkg1")
    packages_mock = _make_packages("pkg1")
    packages_mock.values = MagicMock(return_value=[pkg1])

    output = StatusOutput(
        name="fallback-out",
        default_path=str(default_file),
        build=lambda pkgs: {"fallback": True},
        config_path_key="output_path",  # set but operator never configured
    )
    record = status_plugin(name="fallback-plug", outputs=[output])
    _register_status(record)

    # mirror.conf.plugins is a real (empty) dict — get_config returns {} per contract
    # because the registered name isn't in conf.plugins.
    conf_mock = _make_conf()
    conf_mock.plugins = {}
    monkeypatch.setattr(mirror, "packages", packages_mock, raising=False)
    monkeypatch.setattr(mirror, "conf", conf_mock, raising=False)

    mirror.config._write_status_outputs()

    assert default_file.exists(), "fallback to default_path failed"
    data = json.loads(default_file.read_text())
    assert data == {"fallback": True}


def test_duplicate_status_output_name_raises() -> None:
    """Two plug-ins registering a StatusOutput with the same name raises ValueError."""
    output_a = StatusOutput(name="report", default_path="/tmp/report-a.json", build=lambda pkgs: {})
    output_b = StatusOutput(name="report", default_path="/tmp/report-b.json", build=lambda pkgs: {})

    _register_status(status_plugin(name="report-plugin-a", outputs=[output_a]))

    with pytest.raises(ValueError, match="output 'report' already owned by 'report-plugin-a'"):
        _register_status(status_plugin(name="report-plugin-b", outputs=[output_b]))

    assert "report-plugin-a" in mirror.plugin._registry
    assert "report-plugin-b" not in mirror.plugin._registry


def test_within_record_duplicate_output_names_raise() -> None:
    """A single plug-in with duplicate output names in outputs list raises ValueError atomically."""
    output_x1 = StatusOutput(name="x", default_path="/tmp/x1.json", build=lambda pkgs: {})
    output_x2 = StatusOutput(name="x", default_path="/tmp/x2.json", build=lambda pkgs: {})

    with pytest.raises(ValueError, match="duplicate output name 'x'"):
        _register_status(status_plugin(name="dup-output-plugin", outputs=[output_x1, output_x2]))

    assert "dup-output-plugin" not in mirror.plugin._registry
    assert "x" not in mirror.plugin._status_outputs


def test_status_output_isolation_on_failure(tmp_path: Path, monkeypatch) -> None:
    """A failing build in one plug-in output does not block another, and a warning is logged."""
    good_file = tmp_path / "good.json"
    bad_file = tmp_path / "bad.json"

    pkg1 = _make_package("pkg1")
    packages_mock = _make_packages("pkg1")
    packages_mock.values = MagicMock(return_value=[pkg1])

    def bad_build(packages):
        raise RuntimeError("build exploded")

    def good_build(packages):
        return {"result": "ok"}

    output_bad = StatusOutput(name="bad-output", default_path=str(bad_file), build=bad_build)
    output_good = StatusOutput(name="good-output", default_path=str(good_file), build=good_build)

    _register_status(status_plugin(name="bad-output-plugin", outputs=[output_bad]))
    _register_status(status_plugin(name="good-output-plugin", outputs=[output_good]))

    warnings_logged: list[str] = []
    monkeypatch.setattr(mirror.log, "warning", lambda msg, *a, **kw: warnings_logged.append(msg % a if a else msg))
    monkeypatch.setattr(mirror, "packages", packages_mock, raising=False)
    monkeypatch.setattr(mirror, "conf", _make_conf(), raising=False)

    # Must not raise.
    mirror.config._write_status_outputs()

    assert good_file.exists()
    data = json.loads(good_file.read_text())
    assert data == {"result": "ok"}
    assert not bad_file.exists()
    assert any("bad-output" in w for w in warnings_logged)


def test_atomic_write_failure_preserves_old_content(tmp_path: Path, monkeypatch) -> None:
    """If os.replace fails, the original file is untouched and no temp file is left.

    Tests _atomic_write_json directly because save_stat_data absorbs the error;
    the atomicity guarantee lives in the helper.
    """
    target_file = tmp_path / "stat.json"
    original_content = {"mirrorname": "original", "packages": {}}
    target_file.write_text(json.dumps(original_content))

    def failing_replace(src: str, dst) -> None:
        raise OSError("simulated replace failure")

    # Patch the os.replace used inside mirror.config (the module's own 'os' binding).
    monkeypatch.setattr(mirror.config.os, "replace", failing_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        mirror.config._atomic_write_json(target_file, {"mirrorname": "new", "packages": {}})

    # Original content must be unchanged.
    on_disk = json.loads(target_file.read_text())
    assert on_disk == original_content
    # No leftover .tmp files.
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


def test_unregister_cleans_all_state(tmp_path: Path) -> None:
    """_unregister removes a plug-in from _registry, hooks, transform owner, and outputs."""
    output = StatusOutput(name="unregister-out", default_path=str(tmp_path / "out.json"), build=lambda pkgs: {})
    record = status_plugin(
        name="full-plugin",
        extend_stat_fields=lambda pkg: {"k": "v"},
        transform_stat_payload=lambda p: p,
        outputs=[output],
    )
    _register_status(record)

    # Verify all state stores are populated.
    assert "full-plugin" in mirror.plugin._registry
    assert any(n == "full-plugin" for n, _ in mirror.plugin._status_stat_hooks)
    assert mirror.plugin._stat_transform_owner is not None
    assert mirror.plugin._stat_transform_owner[0] == "full-plugin"
    assert "unregister-out" in mirror.plugin._status_outputs

    _unregister("full-plugin")

    assert "full-plugin" not in mirror.plugin._registry
    assert all(n != "full-plugin" for n, _ in mirror.plugin._status_stat_hooks)
    assert mirror.plugin._stat_transform_owner is None
    assert "unregister-out" not in mirror.plugin._status_outputs


def test_unregister_sync_removes_from_methods() -> None:
    """_unregister a sync plug-in removes it from mirror.sync.methods."""
    record = sync_plugin(name="test-sync", execute=lambda p, l: None)
    from mirror.plugin import _register_sync
    _register_sync(record)

    assert "test-sync" in mirror.sync.methods

    _unregister("test-sync")

    assert "test-sync" not in mirror.sync.methods
    assert "test-sync" not in mirror.plugin._registry


def test_load_external_plugins_disable_calls_unregister() -> None:
    """load_external_plugins with enabled=False unregisters a previously registered plug-in."""
    output = StatusOutput(name="disable-out", default_path="/tmp/disable-out.json", build=lambda pkgs: {})
    record = status_plugin(
        name="disable-me",
        extend_stat_fields=lambda pkg: {"k": "v"},
        transform_stat_payload=lambda p: p,
        outputs=[output],
    )
    _register_status(record)

    assert "disable-me" in mirror.plugin._registry

    mirror.plugin.load_external_plugins(
        {"disable-me": PluginSettings(enabled=False)}
    )

    assert "disable-me" not in mirror.plugin._registry
    assert all(n != "disable-me" for n, _ in mirror.plugin._status_stat_hooks)
    assert mirror.plugin._stat_transform_owner is None
    assert "disable-out" not in mirror.plugin._status_outputs
