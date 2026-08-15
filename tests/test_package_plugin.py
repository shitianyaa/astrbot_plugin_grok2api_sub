from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "package_plugin.py"
_SPEC = importlib.util.spec_from_file_location("package_plugin", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
package_plugin = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = package_plugin
_SPEC.loader.exec_module(package_plugin)


def _fixture_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "core" / "assets").mkdir(parents=True)
    files = {
        "__init__.py": "\n",
        "main.py": "VALUE = 1\n",
        "metadata.yaml": "name: astrbot_plugin_grok2api_sub\nversion: v0.1.4\n",
        "_conf_schema.json": "{}\n",
        "requirements.txt": "aiohttp>=3.9.0\n",
        "README.md": "# Plugin\n",
        "LICENSE": "MIT\n",
        "logo.png": "logo\n",
        "core/__init__.py": "\n",
        "core/module.py": "def value():\n    return 1\n",
        "core/assets/font.txt": "font\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def test_build_package_uses_allowlist_and_verifies_manifest(tmp_path: Path, monkeypatch) -> None:
    root = _fixture_repo(tmp_path)
    for relative in (
        "tests/test_secret.py",
        "docs/internal.md",
        ".github/workflows/release.yml",
        "Progress/notes.md",
        "data/auth.json",
        ".env",
        "core/__pycache__/module.pyc",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("excluded", encoding="utf-8")

    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1704067200")
    output = tmp_path / "dist"
    manifest = package_plugin.build_package(root, "v0.1.4", output, None)

    archive_path = output / "astrbot_plugin_grok2api_sub-v0.1.4.zip"
    checksum_path = archive_path.with_suffix(".zip.sha256")
    manifest_path = output / "manifest.json"
    assert archive_path.is_file()
    assert checksum_path.read_text(encoding="ascii").endswith(
        "  astrbot_plugin_grok2api_sub-v0.1.4.zip\n"
    )
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest
    assert manifest["archive"]["sha256"] == package_plugin._sha256_file(archive_path)

    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
    assert names == sorted(names)
    assert "astrbot_plugin_grok2api_sub/main.py" in names
    assert "astrbot_plugin_grok2api_sub/core/assets/font.txt" in names
    assert not any(
        part in name
        for name in names
        for part in ("tests/", "docs/", ".github/", "Progress/", "data/", ".env", ".pyc")
    )
    assert manifest["source_commit"] == ""
    assert manifest["source_dirty"] is False


def test_build_package_marks_dirty_source_without_claiming_head(
    tmp_path: Path, monkeypatch
) -> None:
    root = _fixture_repo(tmp_path)
    monkeypatch.setattr(package_plugin, "_source_dirty", lambda _root: True)
    monkeypatch.setattr(package_plugin, "_source_commit", lambda _root: "a" * 40)

    manifest = package_plugin.build_package(root, "v0.1.4", tmp_path / "dist", None)

    assert manifest["source_commit"] == ""
    assert manifest["source_dirty"] is True


def test_source_dirty_detects_untracked_packaged_file(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    assert package_plugin._source_dirty(root) is False

    (root / "core" / "new_module.py").write_text("VALUE = 2\n", encoding="utf-8")

    assert package_plugin._source_dirty(root) is True


def test_build_package_is_reproducible_with_source_date_epoch(tmp_path: Path, monkeypatch) -> None:
    root = _fixture_repo(tmp_path)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1704067200")
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_manifest = package_plugin.build_package(root, "v0.1.4", first, None)
    second_manifest = package_plugin.build_package(root, "v0.1.4", second, None)

    assert first_manifest["archive"]["sha256"] == second_manifest["archive"]["sha256"]
    assert (first / first_manifest["archive"]["name"]).read_bytes() == (
        second / second_manifest["archive"]["name"]
    ).read_bytes()


def test_build_package_rejects_version_mismatch_and_existing_output(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    output = tmp_path / "dist"

    with pytest.raises(package_plugin.PackageError, match="metadata.yaml version"):
        package_plugin.build_package(root, "v0.1.5", output, None)

    package_plugin.build_package(root, "v0.1.4", output, None)
    with pytest.raises(package_plugin.PackageError, match="refusing to overwrite"):
        package_plugin.build_package(root, "v0.1.4", output, None)


def test_collect_source_files_rejects_symlinks(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    target = root / "outside.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    link = root / "core" / "linked.py"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable in this environment")

    with pytest.raises(package_plugin.PackageError, match="symbolic links"):
        package_plugin.collect_source_files(root)


@pytest.mark.parametrize("name", ["../escape.py", "/absolute.py", "other/main.py"])
def test_validate_member_name_rejects_unsafe_paths(name: str) -> None:
    with pytest.raises(package_plugin.PackageError):
        package_plugin._validate_member_name(name)
