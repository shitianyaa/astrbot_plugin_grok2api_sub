"""Validate local version and repository inputs before building a plugin release."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - the development requirements provide PyYAML.
    yaml = None


SEMVER_RE = re.compile(r"^v?(?P<version>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$")
VERSION_HEADING_RE = re.compile(
    r"^##\s+(?P<tag>v\d+\.\d+\.\d+)\s+\((?P<date>\d{4}-\d{2}-\d{2})\)\s*$"
)
UNRELEASED_HEADING_RE = re.compile(r"^##\s+\[?Unreleased\]?\s*$", re.IGNORECASE)
CATEGORY_HEADING_RE = re.compile(r"^###\s+(?P<category>[^\s].*?)\s*$")
README_BADGE_RE = re.compile(r"version-(?P<version>\d+\.\d+\.\d+)-")
CONFIG_VERSION_RE = re.compile(
    r"(?ms)^def\s+version\(\)\s*->\s*str\s*:\s*\n\s*return\s+[\"'](?P<version>v?\d+\.\d+\.\d+)[\"']"
)

ALLOWED_CATEGORIES = {
    "Added",
    "Changed",
    "Fixed",
    "Removed",
    "Security",
    "Documentation",
    "Maintenance",
}

SENSITIVE_FILE_RE = re.compile(
    r"(^|/)(?:\.env(?:\.[^/]*)?|auth\.json|data|Progress|testignore|\.codegraph|"
    r"\.pytest_cache|\.ruff_cache|__pycache__|dist|build)(?:/|$)|"
    r"(?:^|/)[^/]+\.(?:pem|key|pyc)$",
    re.IGNORECASE,
)


@dataclass
class Error:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass
class CheckResult:
    tag: str
    versions: dict[str, str | None] = field(default_factory=dict)
    unreleased_count: int = 0
    release_heading: str | None = None
    errors: list[Error] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, object]:
        return {
            "tag": self.tag,
            "versions": self.versions,
            "unreleased_count": self.unreleased_count,
            "release_heading": self.release_heading,
            "errors": [error.as_dict() for error in self.errors],
        }


def _normalise_version(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = SEMVER_RE.fullmatch(value.strip())
    if not match:
        return None
    return ".".join((match.group("version"), match.group("minor"), match.group("patch")))


def _tag_version(tag: str) -> str | None:
    return _normalise_version(tag)


def _read_text(root: Path, relative: str, result: CheckResult) -> str | None:
    path = root / relative
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        result.errors.append(Error("missing_file", relative, "required release input is missing"))
    except UnicodeDecodeError:
        result.errors.append(
            Error("invalid_text", relative, "release input is not valid UTF-8 text")
        )
    except OSError as exc:
        result.errors.append(
            Error("read_failed", relative, f"unable to read release input: {type(exc).__name__}")
        )
    return None


def _metadata_version(root: Path, result: CheckResult) -> str | None:
    text = _read_text(root, "metadata.yaml", result)
    if text is None:
        return None
    if yaml is None:
        result.errors.append(
            Error(
                "missing_dependency", "metadata.yaml", "PyYAML is required to parse metadata.yaml"
            )
        )
        return None
    try:
        data = yaml.safe_load(text)
    except Exception as exc:  # pragma: no cover - exact parser exception varies by PyYAML version.
        result.errors.append(
            Error("invalid_yaml", "metadata.yaml", f"unable to parse YAML: {type(exc).__name__}")
        )
        return None
    version = _normalise_version(data.get("version") if isinstance(data, dict) else None)
    if version is None:
        result.errors.append(Error("invalid_version", "metadata.yaml", "version must be vX.Y.Z"))
    return version


def _pyproject_version(root: Path, result: CheckResult) -> str | None:
    text = _read_text(root, "pyproject.toml", result)
    if text is None:
        return None
    try:
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib

        data = tomllib.loads(text)
    except Exception as exc:  # pragma: no cover - exact parser exception varies by Python version.
        result.errors.append(
            Error("invalid_toml", "pyproject.toml", f"unable to parse TOML: {type(exc).__name__}")
        )
        return None
    version = _normalise_version(data.get("project", {}).get("version"))
    if version is None:
        result.errors.append(
            Error("invalid_version", "pyproject.toml", "project.version must be X.Y.Z")
        )
    return version


def _config_version(root: Path, result: CheckResult) -> str | None:
    config_path = (
        "core/common/config.py" if (root / "core/common/config.py").exists() else "core/config.py"
    )
    text = _read_text(root, config_path, result)
    if text is None:
        return None
    match = CONFIG_VERSION_RE.search(text)
    version = _normalise_version(match.group("version") if match else None)
    if version is None:
        result.errors.append(Error("invalid_version", config_path, "version() must return vX.Y.Z"))
    return version


def _readme_version(root: Path, result: CheckResult) -> str | None:
    text = _read_text(root, "README.md", result)
    if text is None:
        return None
    matches = README_BADGE_RE.findall(text)
    if len(matches) != 1:
        result.errors.append(
            Error("invalid_version", "README.md", "README must contain exactly one version badge")
        )
        return None
    return _normalise_version(matches[0])


def _changelog_sections(text: str) -> list[tuple[int, str, list[str]]]:
    lines = text.splitlines()
    headings = [(index, line) for index, line in enumerate(lines) if line.startswith("## ")]
    sections: list[tuple[int, str, list[str]]] = []
    for position, (index, heading) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        sections.append((index, heading, lines[index + 1 : end]))
    return sections


def _check_changelog(root: Path, tag: str, result: CheckResult) -> None:
    text = _read_text(root, "CHANGELOG.md", result)
    if text is None:
        return
    sections = _changelog_sections(text)
    unreleased = [
        (index, heading, body)
        for index, heading, body in sections
        if UNRELEASED_HEADING_RE.fullmatch(heading)
    ]
    result.unreleased_count = len(unreleased)
    if result.unreleased_count != 1:
        result.errors.append(
            Error(
                "duplicate_unreleased",
                "CHANGELOG.md",
                "CHANGELOG must contain exactly one Unreleased section",
            )
        )
    elif unreleased[0][1] != "## [Unreleased]":
        result.errors.append(
            Error(
                "noncanonical_unreleased",
                "CHANGELOG.md",
                "Unreleased heading must be exactly ## [Unreleased]",
            )
        )

    expected_heading = f"## {tag} ("
    release = next((item for item in sections if item[1].startswith(expected_heading)), None)
    if release is None:
        result.errors.append(
            Error(
                "missing_release_heading",
                "CHANGELOG.md",
                f"missing exact release heading for {tag}",
            )
        )
    else:
        match = VERSION_HEADING_RE.fullmatch(release[1])
        if match is None or match.group("tag") != tag:
            result.errors.append(
                Error(
                    "invalid_release_heading",
                    "CHANGELOG.md",
                    f"release heading must be ## {tag} (YYYY-MM-DD)",
                )
            )
        result.release_heading = release[1]
        meaningful = [line.strip() for line in release[2] if line.strip()]
        if not meaningful:
            result.errors.append(
                Error("empty_release", "CHANGELOG.md", f"release section for {tag} is empty")
            )
        for line in release[2]:
            category_match = CATEGORY_HEADING_RE.fullmatch(line)
            if category_match and category_match.group("category") not in ALLOWED_CATEGORIES:
                result.errors.append(
                    Error(
                        "invalid_category",
                        "CHANGELOG.md",
                        f"unsupported category: {category_match.group('category')}",
                    )
                )


def _git_tracked_paths(root: Path) -> list[str] | None:
    if not (root / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [item.decode("utf-8") for item in completed.stdout.split(b"\0") if item]


def _candidate_paths(root: Path, inputs: Iterable[str] | None = None) -> list[str]:
    if inputs is not None:
        return [str(Path(item).as_posix()).lstrip("./") for item in inputs]
    tracked = _git_tracked_paths(root)
    if tracked is not None:
        return tracked
    return [str(path.relative_to(root).as_posix()) for path in root.rglob("*") if path.is_file()]


def _check_sensitive_paths(
    root: Path, result: CheckResult, inputs: Iterable[str] | None = None
) -> None:
    for relative in _candidate_paths(root, inputs):
        if SENSITIVE_FILE_RE.search(relative):
            result.errors.append(
                Error(
                    "sensitive_input",
                    relative,
                    "sensitive runtime or credential path is not a release input",
                )
            )


def check_release(root: Path, tag: str, *, inputs: Iterable[str] | None = None) -> CheckResult:
    """Run all local checks and return a structured result without printing secrets."""

    result = CheckResult(tag=tag)
    tag_version = _tag_version(tag)
    if tag_version is None:
        result.errors.append(Error("invalid_tag", "--tag", "tag must use vX.Y.Z"))
        return result

    result.versions = {
        "tag": tag_version,
        "metadata.yaml": _metadata_version(root, result),
        "pyproject.toml": _pyproject_version(root, result),
        "core/config.py": _config_version(root, result),
        "README.md": _readme_version(root, result),
    }
    for path, version in result.versions.items():
        if path != "tag" and version is not None and version != tag_version:
            result.errors.append(Error("version_mismatch", path, f"version does not match {tag}"))
    _check_changelog(root, tag, result)
    _check_sensitive_paths(root, result, inputs)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="release tag in vX.Y.Z form")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument(
        "--json", dest="json_path", type=Path, help="write structured result to this JSON file"
    )
    parser.add_argument(
        "--input",
        dest="inputs",
        action="append",
        help="explicit release input path; may be repeated (default: tracked files)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = check_release(args.root.resolve(), args.tag, inputs=args.inputs)
    payload = result.as_dict()
    if args.json_path:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if result.ok:
        print(f"release checks passed: {args.tag}")
        return 0
    for error in result.errors:
        print(f"{error.code}: {error.path}: {error.message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
