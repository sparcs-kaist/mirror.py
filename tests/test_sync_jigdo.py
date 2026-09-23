"""Tests for mirror.sync.jigdo — pure helpers and daemon/standalone entries."""

import gzip
import hashlib
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import mirror
import mirror.structure
import mirror.socket.worker
import mirror.sync
from mirror.sync.jigdo import (
    JIGDO_DEFAULT_TIMEOUT,
    JIGDO_EXCLUDE_DEFAULT,
    JIGDO_FINAL_INCLUDES,
    JIGDO_INCLUDE_DEFAULT,
    JIGDO_TEMPLATE_EXCLUDES,
    JIGDO_TRACE_PATH_DEFAULT,
    build_final_rsync_command,
    build_jigdo_set_conf,
    build_template_rsync_command,
    execute,
    generate_jigdo_images,
    iter_jigdo_sets,
    plugin,
    run_standalone,
    write_trace_file,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def standalone_hostname(monkeypatch):
    """Keep standalone host selection independent of preceding daemon tests."""
    monkeypatch.setattr(mirror, "conf", None, raising=False)
    monkeypatch.setattr("mirror.sync.jigdo.socket.getfqdn", lambda: "mirror.example")


def _make_package(
    pkgid: str = "jigdo",
    src: str = "rsync://mirror.example.com/debian-cd/",
    dst: str = "/srv/mirror/debian-cd",
    options: dict = None,
) -> mirror.structure.Package:
    """Build a minimal Package for jigdo sync tests."""
    settings = mirror.structure.PackageSettings(
        hidden=False,
        src=src,
        dst=dst,
        options=options if options is not None else {},
    )
    pkg = mirror.structure.Package(
        pkgid=pkgid,
        name=pkgid,
        status="UNKNOWN",
        href=f"/{pkgid}",
        synctype="rsync",
        syncrate=3600,
        link=[],
        settings=settings,
        lastsync=0.0,
    )
    return pkg


class FakeResult:
    """Minimal subprocess.CompletedProcess replacement."""

    def __init__(self, rc: int = 0):
        self.returncode = rc


def _make_fake_runner(returncode: int = 0):
    """Return a fake subprocess.run replacement that records its calls."""
    calls = []

    def runner(argv, **kwargs):
        calls.append(list(argv))
        return FakeResult(returncode)

    runner.calls = calls
    return runner


def _write_jigdo_set(
    base: Path,
    *,
    version: str = "v",
    arch: str = "amd64",
    set_name: str = "dvd",
    image_names: tuple[str, ...] = ("debian-v-amd64-DVD-1.iso",),
    create_images: bool = True,
    compressed_jigdo: bool = False,
) -> None:
    jigdo_dir = base / version / arch / f"jigdo-{set_name}"
    image_dir = base / version / arch / f"iso-{set_name}"
    jigdo_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    checksum_lines = []
    for index, image_name in enumerate(image_names, start=1):
        content = f"image-{index}".encode()
        template_name = f"template-{index}.template"
        metadata = (
            "[Image]\n"
            f"Filename={image_name}\n"
            f"Template={template_name}\n"
        ).encode()
        jigdo_path = jigdo_dir / f"image-{index}.jigdo"
        if compressed_jigdo:
            with gzip.open(jigdo_path, "wb") as stream:
                stream.write(metadata)
        else:
            jigdo_path.write_bytes(metadata)
        (jigdo_dir / template_name).write_bytes(b"template")
        if create_images:
            (image_dir / image_name).write_bytes(content)
        checksum_lines.append(f"{hashlib.sha512(content).hexdigest()}  {image_name}")
    (image_dir / "SHA512SUMS").write_text("\n".join(checksum_lines) + "\n")


# ---------------------------------------------------------------------------
# 1. build_template_rsync_command — required flags
# ---------------------------------------------------------------------------


def test_build_template_rsync_command_required_flags():
    cmd = build_template_rsync_command(
        "rsync://host/debian-cd",
        Path("/srv/mirror/debian-cd"),
        hostname="h",
    )
    assert "--exclude=*.iso" in cmd
    assert "--exclude=Archive-Update-in-Progress-h" in cmd
    assert "--exclude=/project/trace/h" in cmd
    assert "--delete" in cmd
    assert "--delete-after" in cmd
    assert f"--timeout={JIGDO_DEFAULT_TIMEOUT}" in cmd
    assert cmd[-2].endswith("/"), f"src arg must end with /: {cmd[-2]!r}"
    assert cmd[-1].endswith("/"), f"dst arg must end with /: {cmd[-1]!r}"


# ---------------------------------------------------------------------------
# 2. build_template_rsync_command — custom timeout
# ---------------------------------------------------------------------------


def test_build_template_rsync_command_custom_timeout():
    cmd = build_template_rsync_command(
        "rsync://host/debian-cd",
        Path("/srv/mirror"),
        hostname="h",
        timeout=99,
    )
    assert "--timeout=99" in cmd


# ---------------------------------------------------------------------------
# 3. build_final_rsync_command — size-only and ordering
# ---------------------------------------------------------------------------


def test_build_final_rsync_command_size_only_and_ordering():
    cmd = build_final_rsync_command(
        "rsync://host/debian-cd",
        Path("/srv/mirror"),
        hostname="h",
    )

    assert "--size-only" in cmd

    # All three default include patterns must be present
    for pat in JIGDO_FINAL_INCLUDES:
        assert f"--include={pat}" in cmd, f"Expected --include={pat} in {cmd}"

    # Ordering: trace/AUiP excludes come before the first --include=
    trace_idx = cmd.index("--exclude=/project/trace/h")
    first_include_idx = next(i for i, t in enumerate(cmd) if t.startswith("--include="))
    last_include_idx = max(i for i, t in enumerate(cmd) if t.startswith("--include="))
    trailing_exclude_idx = cmd.index("--exclude=*.iso")

    assert trace_idx < first_include_idx, (
        "--exclude=/project/trace/h must come before the first --include="
    )
    assert last_include_idx < trailing_exclude_idx, (
        "last --include= must come before trailing --exclude=*.iso"
    )


def test_build_final_rsync_command_protects_generated_images_before_includes():
    protected = "13.7.0/amd64/iso-dvd/debian-13.7.0-amd64-DVD-2.iso"
    cmd = build_final_rsync_command(
        "rsync://host/debian-cd",
        Path("/srv/mirror"),
        hostname="h",
        protected_paths=(protected,),
        extra_rsync_args=("--include=*.iso",),
    )

    protect_index = cmd.index(f"--filter=P /{protected}")
    include_index = next(i for i, arg in enumerate(cmd) if arg.startswith("--include="))
    assert protect_index < include_index


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is not installed")
@pytest.mark.parametrize(
    "builder", (build_template_rsync_command, build_final_rsync_command)
)
def test_rsync_commands_preserve_custom_trace_from_delete_and_overwrite(
    tmp_path, builder
):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    trace = dst / "custom" / "trace" / "custom.host"
    trace.parent.mkdir(parents=True)
    trace.write_text("local trace\n")
    (src / "marker").write_text("synced\n")

    command = builder(
        str(src),
        dst,
        hostname="mirror.example",
        trace_path="custom/trace",
        trace_hostname="custom.host",
        extra_rsync_args=("--include=*",),
    )
    trace_rule = "--exclude=/custom/trace/custom.host"
    assert command.index(trace_rule) < command.index("--include=*")

    assert subprocess.run(command, check=False).returncode == 0
    assert trace.read_text() == "local trace\n"
    assert (dst / "marker").read_text() == "synced\n"

    source_trace = src / "custom" / "trace" / "custom.host"
    source_trace.parent.mkdir(parents=True)
    source_trace.write_text("remote replacement trace\n")
    assert subprocess.run(command, check=False).returncode == 0
    assert trace.read_text() == "local trace\n"


# ---------------------------------------------------------------------------
# 4. build_jigdo_set_conf — structure and quoting
# ---------------------------------------------------------------------------


def test_build_jigdo_set_conf_structure():
    conf = build_jigdo_set_conf(
        "/jigdo/dir",
        "/image/dir",
        "/tmp/dir",
        jigdo_file="jigdo-file --cache=/x",
        debian_mirror="file:/mirror",
    )
    lines = conf.rstrip("\n").split("\n")
    assert len(lines) == 8, f"Expected 8 lines, got {len(lines)}: {lines!r}"

    assert lines[0] == "jigdoDir=/jigdo/dir"
    assert lines[1] == "templateDir=/jigdo/dir"
    assert lines[2] == "imageDir=/image/dir"
    assert lines[3] == "tmpDir=/tmp/dir"
    assert lines[4] == "jigdoFile='jigdo-file --cache=/x'"
    assert lines[5] == "debianMirror=file:/mirror"
    assert lines[6] == f"include='{JIGDO_INCLUDE_DEFAULT}'"
    assert lines[7] == f"exclude='{JIGDO_EXCLUDE_DEFAULT}'"


# ---------------------------------------------------------------------------
# 5. build_jigdo_set_conf — rejects \n in jigdo_dir
# ---------------------------------------------------------------------------


def test_build_jigdo_set_conf_rejects_newline_in_jigdo_dir():
    with pytest.raises(ValueError):
        build_jigdo_set_conf(
            "/jigdo\ndir",
            "/image/dir",
            "/tmp/dir",
            jigdo_file="jf",
            debian_mirror="file:/m",
        )


# ---------------------------------------------------------------------------
# 6. build_jigdo_set_conf — rejects \r in image_dir
# ---------------------------------------------------------------------------


def test_build_jigdo_set_conf_rejects_cr_in_image_dir():
    with pytest.raises(ValueError):
        build_jigdo_set_conf(
            "/jigdo/dir",
            "/image\rdir",
            "/tmp/dir",
            jigdo_file="jf",
            debian_mirror="file:/m",
        )


# ---------------------------------------------------------------------------
# 7. build_jigdo_set_conf — rejects double-quote in jigdo_file
# ---------------------------------------------------------------------------


def test_build_jigdo_set_conf_shell_quotes_all_values():
    conf = build_jigdo_set_conf(
        "/jigdo dir/'quoted",
        "/image dir",
        "/tmp dir",
        jigdo_file='jigdo-file --label="quoted value"',
        debian_mirror="file:/mirror path",
        include=".*DVD-[1-3]\\.iso$",
        exclude="don't-match",
    )

    assert "jigdoDir='/jigdo dir/'\"'\"'quoted'" in conf
    assert "jigdoFile='jigdo-file --label=\"quoted value\"'" in conf
    assert "exclude='don'\"'\"'t-match'" in conf


# ---------------------------------------------------------------------------
# 8. iter_jigdo_sets — happy path
# ---------------------------------------------------------------------------


def test_iter_jigdo_sets_happy_path(tmp_path):
    (tmp_path / "12.5.0" / "amd64").mkdir(parents=True)
    (tmp_path / "12.5.0" / "i386").mkdir(parents=True)
    os.symlink("12.5.0", tmp_path / "current")

    (tmp_path / "project" / "build" / "12.5.0").mkdir(parents=True)
    (tmp_path / "project" / "build" / "12.5.0" / "amd64").write_text("bd dvd")
    # No build file for i386 — should be skipped

    version, sets = iter_jigdo_sets(tmp_path)

    assert version == "12.5.0"
    assert sets == [("amd64", "bd"), ("amd64", "dvd")]


# ---------------------------------------------------------------------------
# 9. iter_jigdo_sets — missing current symlink
# ---------------------------------------------------------------------------


def test_iter_jigdo_sets_missing_current(tmp_path):
    with pytest.raises(ValueError):
        iter_jigdo_sets(tmp_path)


# ---------------------------------------------------------------------------
# 10. iter_jigdo_sets — target with "/" raises ValueError
# ---------------------------------------------------------------------------


def test_iter_jigdo_sets_target_with_slash(tmp_path):
    os.symlink("a/b", tmp_path / "current")
    with pytest.raises(ValueError):
        iter_jigdo_sets(tmp_path)


# ---------------------------------------------------------------------------
# 11. iter_jigdo_sets — dangerous target names raise ValueError (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_target", ["..", "/etc", "12.5.0;rm"])
def test_iter_jigdo_sets_dangerous_targets(tmp_path, bad_target):
    link = tmp_path / "current"
    if link.exists() or os.path.islink(link):
        link.unlink()
    os.symlink(bad_target, link)
    with pytest.raises(ValueError):
        iter_jigdo_sets(tmp_path)


# ---------------------------------------------------------------------------
# 12. iter_jigdo_sets — version dir is a symlink
# ---------------------------------------------------------------------------


def test_iter_jigdo_sets_version_dir_is_symlink(tmp_path, tmp_path_factory):
    real_dir = tmp_path_factory.mktemp("real_version")
    version_name = real_dir.name
    os.symlink(str(real_dir), tmp_path / version_name)
    os.symlink(version_name, tmp_path / "current")
    with pytest.raises(ValueError):
        iter_jigdo_sets(tmp_path)


# ---------------------------------------------------------------------------
# 13. iter_jigdo_sets — project dir is a symlink (arch skipped)
# ---------------------------------------------------------------------------


def test_iter_jigdo_sets_project_is_symlink(tmp_path, tmp_path_factory):
    # Build real version tree + current symlink
    (tmp_path / "12.5.0" / "amd64").mkdir(parents=True)
    os.symlink("12.5.0", tmp_path / "current")

    # Build the actual project/build/12.5.0/amd64 content in an external dir
    real_project = tmp_path_factory.mktemp("real_project")
    (real_project / "build" / "12.5.0").mkdir(parents=True)
    (real_project / "build" / "12.5.0" / "amd64").write_text("bd")

    # Make tmp_path/project a symlink to the external dir
    os.symlink(str(real_project), tmp_path / "project")

    with pytest.raises(ValueError, match="symlinked path component"):
        iter_jigdo_sets(tmp_path)


# ---------------------------------------------------------------------------
# 14. generate_jigdo_images — happy path
# ---------------------------------------------------------------------------


def test_generate_jigdo_images_happy_path(tmp_path):
    _write_jigdo_set(tmp_path)

    runner_calls = []
    conf_writes = []

    def fake_runner(argv, **kwargs):
        runner_calls.append(list(argv))
        return FakeResult(0)

    def fake_conf_writer(path, text):
        conf_writes.append((path, text))

    generate_jigdo_images(
        tmp_path,
        "v",
        [("amd64", "dvd")],
        jigdo_file="rsync://jf",
        debian_mirror="file:/m",
        runner=fake_runner,
        conf_writer=fake_conf_writer,
    )

    assert len(runner_calls) == 1
    assert runner_calls[0][0] == "jigdo-mirror"

    image_dir = tmp_path / "v" / "amd64" / "iso-dvd"
    assert image_dir.is_dir()

    assert not list(tmp_path.glob(".jigdo-*"))

    assert len(conf_writes) == 1
    conf_path, conf_text = conf_writes[0]

    assert f"jigdoDir={tmp_path / 'v' / 'amd64' / 'jigdo-dvd'}" in conf_text
    assert f"imageDir={image_dir}" in conf_text
    assert str(conf_path).startswith(str(tmp_path / ".jigdo-"))


# ---------------------------------------------------------------------------
# 15. generate_jigdo_images — runner rc=2 raises RuntimeError; tmp_root cleaned
# ---------------------------------------------------------------------------


def test_generate_jigdo_images_runner_failure(tmp_path):
    _write_jigdo_set(tmp_path)

    def bad_runner(argv, **kwargs):
        return FakeResult(2)

    def fake_conf_writer(path, text):
        pass

    with pytest.raises(RuntimeError):
        generate_jigdo_images(
            tmp_path,
            "v",
            [("amd64", "dvd")],
            jigdo_file="rsync://jf",
            debian_mirror="file:/m",
            runner=bad_runner,
            conf_writer=fake_conf_writer,
        )

    assert not list(tmp_path.glob(".jigdo-*"))


# ---------------------------------------------------------------------------
# 16. generate_jigdo_images — preexisting iso-bd as symlink raises ValueError
# ---------------------------------------------------------------------------


def test_generate_jigdo_images_image_dir_is_symlink(tmp_path, tmp_path_factory):
    _write_jigdo_set(tmp_path)
    external = tmp_path_factory.mktemp("ext_iso")
    image_dir = tmp_path / "v" / "amd64" / "iso-dvd"
    for child in image_dir.iterdir():
        child.unlink()
    image_dir.rmdir()
    os.symlink(str(external), image_dir)

    def fake_runner(argv, **kwargs):
        return FakeResult(0)

    def fake_conf_writer(path, text):
        pass

    with pytest.raises(ValueError):
        generate_jigdo_images(
            tmp_path,
            "v",
            [("amd64", "dvd")],
            jigdo_file="rsync://jf",
            debian_mirror="file:/m",
            runner=fake_runner,
            conf_writer=fake_conf_writer,
        )


# ---------------------------------------------------------------------------
# 17. generate_jigdo_images — tmp_root preexisting as symlink raises ValueError
# ---------------------------------------------------------------------------


def test_generate_jigdo_images_tmp_root_is_symlink(tmp_path, tmp_path_factory):
    _write_jigdo_set(tmp_path)
    external = tmp_path_factory.mktemp("ext_tmp")
    symlinked_tmp = tmp_path / "sym_tmp"
    os.symlink(str(external), symlinked_tmp)

    def fake_runner(argv, **kwargs):
        return FakeResult(0)

    def fake_conf_writer(path, text):
        pass

    with pytest.raises(ValueError):
        generate_jigdo_images(
            tmp_path,
            "v",
            [("amd64", "dvd")],
            jigdo_file="rsync://jf",
            debian_mirror="file:/m",
            tmp_root=symlinked_tmp,
            runner=fake_runner,
            conf_writer=fake_conf_writer,
        )


def test_generate_jigdo_images_reads_gzip_magic_and_selects_dvd_1_to_3(tmp_path):
    names = tuple(f"debian-v-amd64-DVD-{number}.iso" for number in range(1, 4))
    _write_jigdo_set(tmp_path, image_names=names, compressed_jigdo=True)
    calls = []

    def fake_runner(argv, **kwargs):
        calls.append((argv, kwargs))
        assert kwargs["env"]["LC_ALL"] == "C"
        return FakeResult(0)

    generated = generate_jigdo_images(
        tmp_path,
        "v",
        [("amd64", "dvd")],
        jigdo_file="jigdo-file --report=noprogress",
        debian_mirror="file:/mirror",
        runner=fake_runner,
    )

    assert [path.name for path in generated[0].images] == list(names)
    assert len(calls) == 1


def test_generate_jigdo_images_rc_zero_without_selected_image_fails(tmp_path):
    _write_jigdo_set(tmp_path, create_images=False)

    with pytest.raises(RuntimeError, match="was not created"):
        generate_jigdo_images(
            tmp_path,
            "v",
            [("amd64", "dvd")],
            jigdo_file="jigdo-file",
            debian_mirror="file:/mirror",
            runner=lambda *args, **kwargs: FakeResult(0),
        )


def test_generate_jigdo_images_bad_checksum_fails(tmp_path):
    _write_jigdo_set(tmp_path)
    checksum = tmp_path / "v" / "amd64" / "iso-dvd" / "SHA512SUMS"
    checksum.write_text("0" * 128 + "  debian-v-amd64-DVD-1.iso\n")

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        generate_jigdo_images(
            tmp_path,
            "v",
            [("amd64", "dvd")],
            jigdo_file="jigdo-file",
            debian_mirror="file:/mirror",
            runner=lambda *args, **kwargs: FakeResult(0),
        )


def test_generate_jigdo_images_uses_sha256_when_sha512_is_absent(tmp_path):
    _write_jigdo_set(tmp_path)
    image_dir = tmp_path / "v" / "amd64" / "iso-dvd"
    image_name = "debian-v-amd64-DVD-1.iso"
    content = (image_dir / image_name).read_bytes()
    (image_dir / "SHA512SUMS").unlink()
    (image_dir / "SHA256SUMS").write_text(
        f"{hashlib.sha256(content).hexdigest()}  {image_name}\n"
    )

    generated = generate_jigdo_images(
        tmp_path,
        "v",
        [("amd64", "dvd")],
        jigdo_file="jigdo-file",
        debian_mirror="file:/mirror",
        runner=lambda *args, **kwargs: FakeResult(0),
    )

    assert generated[0].images[0].name == image_name


def test_generate_jigdo_images_skips_empty_set_but_requires_global_selection(tmp_path):
    _write_jigdo_set(
        tmp_path,
        set_name="cd",
        image_names=("debian-v-amd64-netinst.iso",),
    )
    _write_jigdo_set(tmp_path, set_name="dvd")
    calls = []

    generated = generate_jigdo_images(
        tmp_path,
        "v",
        [("amd64", "cd"), ("amd64", "dvd")],
        jigdo_file="jigdo-file",
        debian_mirror="file:/mirror",
        runner=lambda argv, **kwargs: calls.append(argv) or FakeResult(0),
    )

    assert [(item.arch, item.name) for item in generated] == [("amd64", "dvd")]
    assert len(calls) == 1

    with pytest.raises(RuntimeError, match="no jigdo images matched"):
        generate_jigdo_images(
            tmp_path,
            "v",
            [("amd64", "cd")],
            jigdo_file="jigdo-file",
            debian_mirror="file:/mirror",
            runner=lambda *args, **kwargs: FakeResult(0),
        )


def test_generate_jigdo_images_rejects_output_component_symlink(tmp_path):
    image_name = "nested/debian-v-amd64-DVD-1.iso"
    _write_jigdo_set(tmp_path, image_names=(image_name,), create_images=False)
    image_dir = tmp_path / "v" / "amd64" / "iso-dvd"
    external = tmp_path / "external"
    external.mkdir()
    os.symlink(external, image_dir / "nested")

    with pytest.raises(ValueError, match="symlinked path component"):
        generate_jigdo_images(
            tmp_path,
            "v",
            [("amd64", "dvd")],
            jigdo_file="jigdo-file",
            debian_mirror="file:/mirror",
            runner=lambda *args, **kwargs: FakeResult(0),
        )


def test_generate_jigdo_images_rejects_nested_jigdo_file(tmp_path):
    _write_jigdo_set(tmp_path)
    jigdo_dir = tmp_path / "v" / "amd64" / "jigdo-dvd"
    nested = jigdo_dir / "nested"
    nested.mkdir()
    (jigdo_dir / "image-1.jigdo").replace(nested / "image-1.jigdo")

    with pytest.raises(ValueError, match="nested jigdo files"):
        generate_jigdo_images(
            tmp_path,
            "v",
            [("amd64", "dvd")],
            jigdo_file="jigdo-file",
            debian_mirror="file:/mirror",
            runner=lambda *args, **kwargs: FakeResult(0),
        )


# ---------------------------------------------------------------------------
# 18. run_standalone — phase order
# ---------------------------------------------------------------------------


def _build_jigdo_tree(base: Path) -> None:
    """Build minimal directory tree so iter_jigdo_sets yields one set."""
    (base / "12.5.0" / "amd64").mkdir(parents=True)
    os.symlink("12.5.0", base / "current")
    (base / "project" / "build" / "12.5.0").mkdir(parents=True)
    (base / "project" / "build" / "12.5.0" / "amd64").write_text("dvd")
    _write_jigdo_set(base, version="12.5.0")


def test_run_standalone_phase_order(tmp_path):
    _build_jigdo_tree(tmp_path)

    calls = []

    def multi_runner(argv, **kwargs):
        calls.append(list(argv))
        return FakeResult(0)

    run_standalone(
        "rsync://host/debian-cd",
        tmp_path,
        jigdo_file="rsync://jf",
        debian_mirror="file:/m",
        rsync_bin="rsync",
        jigdo_mirror_bin="jigdo-mirror",
        trace=False,
        runner=multi_runner,
    )

    # Should have at least 3 calls: phase1 rsync, jigdo-mirror, phase3 rsync
    assert len(calls) >= 3

    # First call is phase 1 rsync (template, excludes *.iso, no --size-only)
    assert calls[0][0] == "rsync"
    assert "--exclude=*.iso" in calls[0]
    assert "--size-only" not in calls[0]

    # Middle call(s): jigdo-mirror
    jigdo_calls = [c for c in calls if c[0] == "jigdo-mirror"]
    assert len(jigdo_calls) >= 1

    # Last call is phase 3 rsync (contains --size-only)
    assert calls[-1][0] == "rsync"
    assert "--size-only" in calls[-1]


# ---------------------------------------------------------------------------
# 19. run_standalone — aborts on phase 1 failure
# ---------------------------------------------------------------------------


def test_run_standalone_aborts_on_phase1_failure(tmp_path):
    _build_jigdo_tree(tmp_path)

    calls = []

    def failing_runner(argv, **kwargs):
        calls.append(list(argv))
        return FakeResult(2)

    with pytest.raises(SystemExit) as exc_info:
        run_standalone(
            "rsync://host/debian-cd",
            tmp_path,
            jigdo_file="rsync://jf",
            debian_mirror="file:/m",
            trace=False,
            runner=failing_runner,
        )

    assert exc_info.value.code == 2
    # Only phase 1 was called; jigdo-mirror and phase 3 were not reached
    jigdo_calls = [c for c in calls if c[0] == "jigdo-mirror"]
    size_only_calls = [c for c in calls if "--size-only" in c]
    assert len(jigdo_calls) == 0
    assert len(size_only_calls) == 0


# ---------------------------------------------------------------------------
# 20. run_standalone — aborts on phase 3 failure
# ---------------------------------------------------------------------------


def test_run_standalone_aborts_on_phase3_failure(tmp_path):
    _build_jigdo_tree(tmp_path)

    def selective_runner(argv, **kwargs):
        if "--size-only" in argv:
            return FakeResult(2)
        return FakeResult(0)

    with pytest.raises(SystemExit) as exc_info:
        run_standalone(
            "rsync://host/debian-cd",
            tmp_path,
            jigdo_file="rsync://jf",
            debian_mirror="file:/m",
            trace=False,
            runner=selective_runner,
        )

    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# 21. run_standalone — missing current -> SystemExit(1)
# ---------------------------------------------------------------------------


def test_run_standalone_missing_current(tmp_path):
    # Phase 1 rsync succeeds, but no current symlink -> iter_jigdo_sets raises
    def rc0_runner(argv, **kwargs):
        return FakeResult(0)

    with pytest.raises(SystemExit) as exc_info:
        run_standalone(
            "rsync://host/debian-cd",
            tmp_path,
            jigdo_file="rsync://jf",
            debian_mirror="file:/m",
            trace=False,
            runner=rc0_runner,
        )

    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# 22. run_standalone — creates missing dst
# ---------------------------------------------------------------------------


def test_run_standalone_creates_missing_dst(tmp_path):
    new_dst = tmp_path / "new"
    assert not new_dst.exists()

    # Phase 1 succeeds but no current -> exit(1) after dst creation
    def rc0_runner(argv, **kwargs):
        return FakeResult(0)

    with pytest.raises(SystemExit):
        run_standalone(
            "rsync://host/debian-cd",
            new_dst,
            jigdo_file="rsync://jf",
            debian_mirror="file:/m",
            trace=False,
            runner=rc0_runner,
        )

    assert new_dst.is_dir()


def test_run_standalone_rejects_invalid_ere_before_rsync(tmp_path):
    calls = []

    with pytest.raises(SystemExit) as exc_info:
        run_standalone(
            "rsync://host/debian-cd",
            tmp_path,
            jigdo_file="jigdo-file",
            debian_mirror="file:/mirror",
            jigdo_include="[",
            trace=False,
            runner=lambda argv, **kwargs: calls.append(argv) or FakeResult(0),
        )

    assert exc_info.value.code == 1
    assert calls == []


def test_run_standalone_rechecks_images_after_final_rsync_before_trace(tmp_path):
    _build_jigdo_tree(tmp_path)
    image = tmp_path / "12.5.0" / "amd64" / "iso-dvd" / "debian-v-amd64-DVD-1.iso"
    calls = []

    def deleting_runner(argv, **kwargs):
        calls.append(list(argv))
        if "--size-only" in argv:
            protect = f"--filter=P /{image.relative_to(tmp_path).as_posix()}"
            assert protect in argv
            image.unlink()
        return FakeResult(0)

    with pytest.raises(SystemExit) as exc_info:
        run_standalone(
            "rsync://host/debian-cd",
            tmp_path,
            jigdo_file="jigdo-file",
            debian_mirror="file:/mirror",
            hostname="mirror.example",
            runner=deleting_runner,
        )

    assert exc_info.value.code == 1
    assert not (tmp_path / "project" / "trace" / "mirror.example").exists()


def test_write_trace_file_rejects_source_symlink(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    (tmp_path / "project").mkdir()
    os.symlink(external, tmp_path / "project" / "trace")

    with pytest.raises(OSError):
        write_trace_file(tmp_path, trace_hostname="mirror.example")

    assert not (external / "mirror.example").exists()


def test_write_trace_file_atomically_replaces_regular_file(tmp_path):
    trace = write_trace_file(tmp_path, trace_hostname="mirror.example")
    first = trace.read_text()
    trace.write_text("old\n")

    assert write_trace_file(tmp_path, trace_hostname="mirror.example") == trace
    assert trace.read_text() == first
    assert not list(trace.parent.glob(".*.tmp"))


# ---------------------------------------------------------------------------
# 23. execute — delegates to worker with correct argv
# ---------------------------------------------------------------------------


def test_execute_delegates_to_worker(monkeypatch):
    worker_calls = []

    def fake_execute_command(**kwargs):
        worker_calls.append(kwargs)

    monkeypatch.setattr("mirror.socket.worker.execute_command", fake_execute_command)
    monkeypatch.setattr(
        mirror,
        "conf",
        SimpleNamespace(hostname="myhost", uid=1000, gid=1000),
        raising=False,
    )
    monkeypatch.setattr("mirror.sync.on_sync_done", lambda *a, **kw: None)

    pkg = _make_package(
        options={
            "jigdo_file": "jigdo-file --cache=/x",
            "debian_mirror": "file:/m",
            "jigdo_include": ".*DVD-[12]\\.iso$",
            "jigdo_exclude": ".*source.*",
        }
    )
    pkg_logger = logging.getLogger("test_execute_jigdo")
    pkg_logger.handlers = []

    execute(pkg, pkg_logger)

    assert len(worker_calls) == 1
    kwargs = worker_calls[0]
    assert kwargs["sync_method"] == "jigdo"

    cmd = kwargs["commandline"]
    assert cmd[0] == sys.executable
    assert cmd[1:5] == ["-m", "mirror", "worker-execute", "jigdo"]

    assert "--src" in cmd
    assert "--jigdo-file" in cmd
    assert "jigdo-file --cache=/x" in cmd

    include_idx = cmd.index("--jigdo-include")
    exclude_idx = cmd.index("--jigdo-exclude")
    assert cmd[include_idx + 1] == ".*DVD-[12]\\.iso$"
    assert cmd[exclude_idx + 1] == ".*source.*"

    hostname_idx = cmd.index("--hostname")
    assert cmd[hostname_idx + 1] == "myhost"


# ---------------------------------------------------------------------------
# 24. execute — user/password passed as env (not in commandline)
# ---------------------------------------------------------------------------


def test_execute_user_password_env(monkeypatch):
    worker_calls = []

    def fake_execute_command(**kwargs):
        worker_calls.append(kwargs)

    monkeypatch.setattr("mirror.socket.worker.execute_command", fake_execute_command)
    monkeypatch.setattr(
        mirror,
        "conf",
        SimpleNamespace(hostname="h", uid=1000, gid=1000),
        raising=False,
    )
    monkeypatch.setattr("mirror.sync.on_sync_done", lambda *a, **kw: None)

    pkg = _make_package(
        options={
            "jigdo_file": "rsync://jf",
            "debian_mirror": "file:/m",
            "user": "u",
            "password": "p",
        }
    )
    pkg_logger = logging.getLogger("test_execute_env")
    pkg_logger.handlers = []

    execute(pkg, pkg_logger)

    kwargs = worker_calls[0]
    assert kwargs["env"] == {"USER": "u", "RSYNC_PASSWORD": "p"}
    assert "p" not in kwargs["commandline"]


# ---------------------------------------------------------------------------
# 25. execute — missing required option -> on_sync_done called, worker not called
# ---------------------------------------------------------------------------


def test_execute_missing_required_option(monkeypatch):
    worker_calls = []
    sync_done_calls = []

    def fake_execute_command(**kwargs):
        worker_calls.append(kwargs)

    def fake_on_sync_done(pkgid, success, returncode):
        sync_done_calls.append((pkgid, success, returncode))

    monkeypatch.setattr("mirror.socket.worker.execute_command", fake_execute_command)
    monkeypatch.setattr(
        mirror,
        "conf",
        SimpleNamespace(hostname="h", uid=1000, gid=1000),
        raising=False,
    )
    monkeypatch.setattr("mirror.sync.on_sync_done", fake_on_sync_done)

    pkg = _make_package(options={})
    pkg_logger = logging.getLogger("test_execute_missing")
    pkg_logger.handlers = []

    execute(pkg, pkg_logger)

    assert len(worker_calls) == 0
    assert len(sync_done_calls) == 1
    assert sync_done_calls[0] == ("jigdo", False, None)


# ---------------------------------------------------------------------------
# 26. execute — trace flag controls --no-trace in commandline
# ---------------------------------------------------------------------------


def test_execute_trace_flag(monkeypatch):
    worker_calls = []

    def fake_execute_command(**kwargs):
        worker_calls.append(kwargs)

    monkeypatch.setattr("mirror.socket.worker.execute_command", fake_execute_command)
    monkeypatch.setattr(
        mirror,
        "conf",
        SimpleNamespace(hostname="h", uid=1000, gid=1000),
        raising=False,
    )
    monkeypatch.setattr("mirror.sync.on_sync_done", lambda *a, **kw: None)

    base_opts = {"jigdo_file": "rsync://jf", "debian_mirror": "file:/m"}

    # trace=False -> --no-trace in commandline
    pkg_no_trace = _make_package(options={**base_opts, "trace": False})
    pkg_logger = logging.getLogger("test_trace_false")
    pkg_logger.handlers = []
    execute(pkg_no_trace, pkg_logger)
    assert "--no-trace" in worker_calls[-1]["commandline"]

    # trace=True (default) -> --no-trace NOT in commandline
    pkg_trace = _make_package(options={**base_opts, "trace": True})
    pkg_logger2 = logging.getLogger("test_trace_true")
    pkg_logger2.handlers = []
    execute(pkg_trace, pkg_logger2)
    assert "--no-trace" not in worker_calls[-1]["commandline"]


# ---------------------------------------------------------------------------
# 27. plugin() — returns correct PluginRecord
# ---------------------------------------------------------------------------


def test_plugin_returns_correct_record():
    rec = plugin()
    assert rec.name == "jigdo"
    assert rec.execute is execute
    assert rec.type == "sync"
