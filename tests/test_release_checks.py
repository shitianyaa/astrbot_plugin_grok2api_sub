from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from scripts.check_repository import check_release


def test_dev_dependencies_are_declared_in_pyproject() -> None:
    root = Path(__file__).parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    dev = pyproject["project"]["optional-dependencies"]["dev"]
    assert {item.split("=", 1)[0].split(">", 1)[0] for item in dev} == {
        "AstrBot",
        "Jinja2",
        "pytest",
        "pytest-asyncio",
        "PyYAML",
        "ruff",
        "tomli",
    }
    assert not (root / "requirements-dev.txt").exists()


def _write_fixture(
    root: Path,
    *,
    metadata: str = "v1.2.3",
    pyproject: str = "1.2.3",
    config: str = "v1.2.3",
    readme: str = "1.2.3",
    changelog: str | None = None,
) -> None:
    (root / "core").mkdir(parents=True)
    (root / "metadata.yaml").write_text(f"version: {metadata}\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(f'[project]\nversion = "{pyproject}"\n', encoding="utf-8")
    (root / "core/config.py").write_text(
        f'def version() -> str:\n    return "{config}"\n', encoding="utf-8"
    )
    (root / "README.md").write_text(
        f"![Version](https://img.shields.io/badge/version-{readme}-22c55e)\n", encoding="utf-8"
    )
    changelog = changelog or (
        "# Changelog\n\n"
        "## [Unreleased]\n\n### Changed\n\n- pending\n\n"
        "## v1.2.3 (2026-08-15)\n\n### Fixed\n\n- fixed\n"
    )
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")


def test_rejects_duplicate_unreleased_sections(tmp_path: Path) -> None:
    _write_fixture(
        tmp_path,
        changelog=(
            "# Changelog\n\n## [Unreleased]\n\n### Changed\n\n- one\n\n"
            "## Unreleased\n\n### Changed\n\n- two\n\n"
            "## v1.2.3 (2026-08-15)\n\n### Fixed\n\n- fixed\n"
        ),
    )

    result = check_release(tmp_path, "v1.2.3")

    assert not result.ok
    assert any(error.code == "duplicate_unreleased" for error in result.errors)


def test_rejects_version_drift_between_metadata_and_pyproject(tmp_path: Path) -> None:
    _write_fixture(tmp_path, metadata="v1.2.4")

    result = check_release(tmp_path, "v1.2.3")

    assert not result.ok
    assert any(
        error.code == "version_mismatch" and error.path == "metadata.yaml"
        for error in result.errors
    )


def test_accepts_v_prefixed_metadata_and_bare_pyproject_version(tmp_path: Path) -> None:
    _write_fixture(tmp_path, metadata="v1.2.3", pyproject="1.2.3", config="v1.2.3", readme="1.2.3")

    result = check_release(tmp_path, "v1.2.3")

    assert result.ok, result.errors
    assert result.versions == {
        "tag": "1.2.3",
        "metadata.yaml": "1.2.3",
        "pyproject.toml": "1.2.3",
        "core/config.py": "1.2.3",
        "README.md": "1.2.3",
    }


def test_requires_exact_release_heading_for_tag(tmp_path: Path) -> None:
    _write_fixture(
        tmp_path,
        changelog=(
            "# Changelog\n\n## [Unreleased]\n\n### Changed\n\n- pending\n\n"
            "## 1.2.3 (2026-08-15)\n\n### Fixed\n\n- fixed\n"
        ),
    )

    result = check_release(tmp_path, "v1.2.3")

    assert not result.ok
    assert any(error.code == "missing_release_heading" for error in result.errors)


def test_rejects_sensitive_runtime_files_in_release_inputs(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "runtime.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".env").write_text("KEY=redacted\n", encoding="utf-8")

    result = check_release(tmp_path, "v1.2.3")

    assert not result.ok
    sensitive = {error.path for error in result.errors if error.code == "sensitive_input"}
    assert {"data/runtime.json", ".env"} <= sensitive


def test_json_result_contains_machine_readable_error_fields(tmp_path: Path) -> None:
    _write_fixture(tmp_path, pyproject="9.9.9")

    result = check_release(tmp_path, "v1.2.3")
    payload = result.as_dict()

    assert payload["tag"] == "v1.2.3"
    assert all({"code", "path", "message"} <= set(error) for error in payload["errors"])
    json.dumps(payload, ensure_ascii=False)


def test_ci_workflow_has_read_only_fixed_action_contract() -> None:
    workflow_path = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
    text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)

    assert workflow["permissions"] == {}
    assert set(workflow["on"]) == {"pull_request", "push"}
    assert workflow["on"]["push"]["branches"] == ["main"]

    jobs = workflow["jobs"]
    assert set(jobs) == {"quality"}
    quality = jobs["quality"]
    assert quality["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert {"3.10", "3.12"} <= set(quality["strategy"]["matrix"]["python-version"])

    action_refs = re.findall(r"uses:\s*([^\s]+)", text)
    assert action_refs
    for reference in action_refs:
        owner_repo, _, commit = reference.partition("@")
        assert owner_repo.startswith("actions/")
        assert re.fullmatch(r"[0-9a-f]{40}", commit)

    assert "persist-credentials: false" in text
    assert 'python -m pip install -e ".[dev]"' in text
    assert "requirements-dev.txt" not in text
    assert "contents: write" not in text
    assert "gh release" not in text
    assert "git push" not in text
    for command in (
        "scripts/check_repository.py",
        "--json",
        "python -m json.tool",
        "python -m compileall",
        "python -m pytest",
        "ruff check",
        "ruff format --check",
        "git diff --check",
    ):
        assert command in text


def test_release_workflow_is_immutable_and_split_by_permissions() -> None:
    workflow_path = Path(__file__).parents[1] / ".github" / "workflows" / "release-plugin.yml"
    text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)

    assert workflow["permissions"] == {}
    assert set(workflow["on"]) == {"push", "workflow_dispatch"}
    assert workflow["on"]["push"]["tags"] == ["v*.*.*"]
    assert "branches" not in workflow["on"]["push"]
    jobs = workflow["jobs"]
    assert {"validate", "release_notes_audit", "build", "build_production", "publish"} <= set(jobs)
    assert jobs["publish"]["environment"] == "release"
    assert jobs["publish"]["permissions"] == {"contents": "write"}
    for name in ("validate", "release_notes_audit", "build", "build_production"):
        assert jobs[name]["permissions"]["contents"] == "read"
    assert "--clobber" not in text
    assert "git push" not in text
    assert "Create release tag" not in text
    assert 'git tag --merged "${tag}^" --sort=-version:refname' in text
    assert 'python -m pip install -e ".[dev]"' in text
    assert "requirements-dev.txt" not in text
    for required in (
        "collect_release_sources.py",
        "render_release_notes.py",
        "package_plugin.py",
        "manifest.json",
        ".sha256",
        "retention-days:",
        "persist-credentials: false",
    ):
        assert required in text
    assert "needs.validate.outputs.allow_unmapped_commits == 'true'" in text
    for reference in re.findall(r"uses:\s*([^\s]+)", text):
        owner_repo, _, commit = reference.partition("@")
        assert owner_repo.startswith("actions/")
        assert re.fullmatch(r"[0-9a-f]{40}", commit)
