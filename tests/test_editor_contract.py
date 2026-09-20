import copy
import json
import logging
from pathlib import Path
import shutil
import subprocess

import pytest

import mirror.sync
from mirror.structure import Config, Package


SCHEMA_PATH = Path(__file__).parents[1] / "docs" / "editor" / "src" / "schema.js"
BUILTIN_METHODS = (
    "rsync",
    "ftpsync",
    "lftp",
    "bandersnatch",
    "ubuntu",
    "jigdo",
    "local",
    "debmirror",
    "apt-mirror2",
)


def _run_schema(expression: str, payload=None):
    if shutil.which("node") is None:
        pytest.skip("Node.js is required for the documentation editor contract tests")
    script = f"""
import * as schema from {json.dumps(SCHEMA_PATH.as_uri())};
const payload = JSON.parse(process.argv[1]);
const result = {expression};
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(payload)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _default_config():
    return _run_schema("schema.createDefaultConfig()")


def _package(package_id: str, method: str, src: str, options: dict) -> dict:
    package = _run_schema("schema.createPackage(payload.id, payload.method)", {
        "id": package_id,
        "method": method,
    })
    package["settings"]["src"] = src
    package["settings"]["options"] = options
    return package


@pytest.fixture
def method_packages():
    return {
        "rsync": _package("rsync", "rsync", "rsync://example.org/module", {
            "option_include": "p",
            "exclude": ["*.tmp"],
        }),
        "ftpsync": _package("ftpsync", "ftpsync", "rsync://example.org/debian", {
            "hub": False,
        }),
        "lftp": _package("lftp", "lftp", "ftp://ftp.example.org/pub", {
            "list_options": "-a",
            "max_retries": 5,
        }),
        "bandersnatch": _package("bandersnatch", "bandersnatch", "https://pypi.org", {}),
        "ubuntu": _package("ubuntu", "ubuntu", "rsync://example.org/ubuntu", {
            "trace": True,
            "extra_rsync_args": ["--bwlimit=50000"],
        }),
        "jigdo": _package("jigdo", "jigdo", "rsync://example.org/debian-cd", {
            "jigdo_file": "rsync://example.org/debian-cd/jigdo",
            "debian_mirror": "https://deb.debian.org/debian",
            "timeout": 7200,
        }),
        "local": _package("local", "local", "", {}),
        "debmirror": _package("debmirror", "debmirror", "https://deb.debian.org/debian", {
            "dist": ["bookworm"],
            "section": ["main"],
            "arch": ["amd64"],
            "check_gpg": False,
            "cleanup": "postcleanup",
        }),
        "apt-mirror2": _package("apt-mirror2", "apt-mirror2", "https://example.org/repo", {
            "config": [{
                "src": "https://example.org/repo",
                "dst": "repository",
                "dist": ["stable"],
                "section": ["main"],
                "arch": ["amd64"],
                "check_gpg": False,
            }],
            "nthreads": 4,
        }),
    }


def test_default_config_matches_python_loader_and_is_valid():
    config = _default_config()

    assert _run_schema("schema.validateConfig(payload)", config) == []
    loaded = Config.load_from_dict(config)
    assert loaded.hostname == "mirror.example.org"
    assert loaded.localtimezone == "UTC"


def test_schema_exposes_all_builtin_method_option_schemas():
    exports = _run_schema("({ methods: Object.keys(schema.methodSchemas), sameGlobal: schema.globalSchema === schema.configSchema })")

    assert set(exports["methods"]) == set(BUILTIN_METHODS)
    assert exports["sameGlobal"] is True


@pytest.mark.parametrize("method", BUILTIN_METHODS)
def test_representative_method_config_matches_editor_and_package_loader(
    monkeypatch, method_packages, method
):
    config = _default_config()
    config["packages"] = {method: copy.deepcopy(method_packages[method])}

    errors = [
        issue for issue in _run_schema("schema.validateConfig(payload)", config)
        if issue["severity"] == "error"
    ]
    assert errors == []

    monkeypatch.setattr(mirror.sync, "methods", list(BUILTIN_METHODS))
    package = Package.from_dict(config["packages"][method])
    assert package.synctype == method


def test_representative_options_pass_pure_runtime_builders(method_packages, monkeypatch):
    from mirror.sync import apt_mirror2, debmirror, ftpsync, jigdo, lftp, rsync, ubuntu

    logger = logging.getLogger("editor-contract")
    rsync_package = Package.from_dict(method_packages["rsync"])
    command, _ = rsync.rsync(
        logger,
        rsync_package.pkgid,
        rsync_package.settings.src,
        Path(rsync_package.settings.dst),
        "",
        "",
        option_include=rsync_package.settings.options["option_include"],
        excludes=rsync_package.settings.options["exclude"],
    )
    assert command[0] == "rsync"

    source = ftpsync._split_rsync_src(
        method_packages["ftpsync"]["settings"]["src"],
        method_packages["ftpsync"]["settings"]["options"],
    )
    assert source == ("example.org", "debian")

    lftp_settings = method_packages["lftp"]["settings"]
    assert "mirror" in lftp._build_lftp_script(
        lftp_settings["src"], lftp_settings["dst"], lftp_settings["options"]
    )

    ubuntu_settings = method_packages["ubuntu"]["settings"]
    stage1, stage2 = ubuntu.build_ubuntu_commands(
        ubuntu_settings["src"],
        Path(ubuntu_settings["dst"]),
        ubuntu_settings["options"]["extra_rsync_args"],
    )
    assert stage1[0] == stage2[0] == "rsync"

    jigdo_settings = method_packages["jigdo"]["settings"]
    assert jigdo.build_template_rsync_command(
        jigdo_settings["src"],
        Path(jigdo_settings["dst"]),
        hostname="mirror.example.org",
        timeout=jigdo_settings["options"]["timeout"],
    )[0] == "rsync"

    monkeypatch.setattr(mirror.sync, "get_extra_args", lambda _package_id: {})
    debmirror_package = Package.from_dict(method_packages["debmirror"])
    debmirror_command, _ = debmirror.build_command(debmirror_package)
    assert debmirror_command[0] == "debmirror"

    apt_package = Package.from_dict(method_packages["apt-mirror2"])
    payload = apt_mirror2.build_payload(apt_package)
    assert payload["repositories"][0]["dst"] == "repository"


def test_validation_preserves_unknown_plugin_with_warning():
    config = _default_config()
    package = _package("custom", "example-plugin", "plugin://source", {
        "plugin_specific": {"kept": True},
    })
    config["packages"] = {"custom": package}

    issues = _run_schema("schema.validateConfig(payload)", config)

    assert not [issue for issue in issues if issue["severity"] == "error"]
    assert any(issue["severity"] == "warning" and issue["path"][-1] == "synctype" for issue in issues)


def test_validation_reports_id_duration_and_apt_repository_conflicts():
    config = _default_config()
    package = _package("apt", "apt-mirror2", "https://example.org", {
        "config": [
            {"src": "https://example.org/one", "dst": "repo", "dist": ["./"]},
            {"src": "https://example.org/two", "dst": "repo/child", "check_gpg": False},
        ],
    })
    package["id"] = "different"
    package["syncrate"] = "P1W"
    package["settings"]["options"]["config"][0]["section"] = ["main"]
    config["packages"] = {"apt": package}

    issues = _run_schema("schema.validateConfig(payload)", config)
    messages = [issue["message"] for issue in issues if issue["severity"] == "error"]

    assert any("must match" in message for message in messages)
    assert any("ISO 8601" in message for message in messages)
    assert any("overlaps" in message for message in messages)
    assert any("keyring" in message for message in messages)
    assert any("Flat repositories" in message for message in messages)
