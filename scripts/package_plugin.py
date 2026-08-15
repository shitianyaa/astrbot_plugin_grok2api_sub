#!/usr/bin/env python3
"""Build and verify the publishable AstrBot plugin archive."""

from __future__ import annotations

import argparse
import compileall
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

PLUGIN_NAME = "astrbot_plugin_grok2api_sub"
TAG_RE = re.compile(r"^v(?P<version>0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
REQUIRED_ROOT_FILES = (
    "__init__.py",
    "main.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "README.md",
    "LICENSE",
)
OPTIONAL_ROOT_FILES = ("logo.png",)
OPTIONAL_DIRECTORIES = ("assets", "templates")
EXCLUDED_NAMES = {"__pycache__", ".pytest_cache", ".ruff_cache"}
FORBIDDEN_NAMES = {
    ".env",
    "auth.json",
    "credentials.json",
    "secrets.json",
    "session.json",
}


class PackageError(RuntimeError):
    """Raised when the release archive contract cannot be satisfied."""


@dataclass(frozen=True)
class SourceFile:
    path: Path
    archive_path: str
    sha256: str


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata_version(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() == "version":
            return value.strip().strip("\"'")
    raise PackageError(f"unable to read version from {path}")


def _validate_tag(tag: str) -> str:
    match = TAG_RE.fullmatch(tag)
    if match is None:
        raise PackageError(f"release tag must be stable SemVer vX.Y.Z: {tag}")
    return match.group("version")


def _assert_regular_file(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise PackageError(f"release input escapes repository root: {path}") from exc

    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PackageError(f"symbolic links are not allowed in release input: {relative}")
    if not path.is_file():
        raise PackageError(f"required release input is not a regular file: {relative}")


def _is_forbidden_path(relative: Path) -> bool:
    lowered = tuple(part.lower() for part in relative.parts)
    if any(part in EXCLUDED_NAMES for part in lowered):
        return True
    name = relative.name.lower()
    return (
        name in FORBIDDEN_NAMES
        or name.startswith(".env.")
        or name.endswith((".pyc", ".pyo"))
        or name.endswith((".pem", ".key", ".p12", ".pfx"))
    )


def collect_source_files(root: Path) -> list[SourceFile]:
    root = root.resolve()
    if root.is_symlink() or not root.is_dir():
        raise PackageError(f"repository root is not a regular directory: {root}")

    paths: list[Path] = []
    for name in REQUIRED_ROOT_FILES:
        path = root / name
        _assert_regular_file(root, path)
        paths.append(path)

    for name in OPTIONAL_ROOT_FILES:
        path = root / name
        if path.exists():
            _assert_regular_file(root, path)
            paths.append(path)

    scan_dirs = [root / "core"]
    if scan_dirs[0].is_symlink() or not scan_dirs[0].is_dir():
        raise PackageError("release input requires a regular core/ directory")

    for dir_name in OPTIONAL_DIRECTORIES:
        opt_dir = root / dir_name
        if opt_dir.is_symlink():
            raise PackageError(f"symbolic links are not allowed in release input: {dir_name}")
        if opt_dir.exists():
            if not opt_dir.is_dir():
                raise PackageError(
                    f"release input requires a regular {dir_name}/ directory if present"
                )
            scan_dirs.append(opt_dir)

    for scan_dir in scan_dirs:
        for directory, dirnames, filenames in os.walk(scan_dir, followlinks=False):
            directory_path = Path(directory)
            for name in tuple(dirnames):
                child = directory_path / name
                relative = child.relative_to(root)
                if child.is_symlink():
                    raise PackageError(
                        f"symbolic links are not allowed in release input: {relative}"
                    )
                if _is_forbidden_path(relative):
                    dirnames.remove(name)
            for name in filenames:
                path = directory_path / name
                relative = path.relative_to(root)
                if path.is_symlink():
                    raise PackageError(
                        f"symbolic links are not allowed in release input: {relative}"
                    )
                if _is_forbidden_path(relative):
                    continue
                _assert_regular_file(root, path)
                paths.append(path)

    files: list[SourceFile] = []
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        files.append(
            SourceFile(
                path=path,
                archive_path=f"{PLUGIN_NAME}/{relative}",
                sha256=_sha256_file(path),
            )
        )
    return files


def _source_epoch(root: Path) -> int:
    raw = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError as exc:
            raise PackageError("SOURCE_DATE_EPOCH must be an integer") from exc
        if value < 0:
            raise PackageError("SOURCE_DATE_EPOCH must not be negative")
        return value

    result = subprocess.run(
        ["git", "show", "-s", "--format=%ct", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip().isdigit():
        return int(result.stdout.strip())
    return int(time.time())


def _zip_datetime(epoch: int) -> tuple[int, int, int, int, int, int]:
    value = datetime.fromtimestamp(epoch, tz=UTC)
    if value.year < 1980:
        value = datetime(1980, 1, 1, tzinfo=UTC)
    if value.year > 2107:
        value = datetime(2107, 12, 31, 23, 59, 58, tzinfo=UTC)
    return (value.year, value.month, value.day, value.hour, value.minute, value.second // 2 * 2)


def _source_commit(root: Path) -> str:
    github_sha = os.environ.get("GITHUB_SHA", "").strip()
    github_workspace = os.environ.get("GITHUB_WORKSPACE", "").strip()
    if (
        re.fullmatch(r"[0-9a-fA-F]{40}", github_sha)
        and github_workspace
        and root.resolve() == Path(github_workspace).resolve()
    ):
        return github_sha.lower()
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip().lower()
    return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else ""


def _source_dirty(root: Path) -> bool:
    """Return whether packaged root/core files differ from the current HEAD."""
    if not (root / ".git").exists():
        return False
    release_scope = [*REQUIRED_ROOT_FILES, *OPTIONAL_ROOT_FILES, "core"]
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", *release_scope],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode != 0 or bool(result.stdout.strip())


def _ensure_new_output(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise PackageError(f"refusing to overwrite existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_archive(path: Path, files: list[SourceFile], epoch: int) -> None:
    timestamp = _zip_datetime(epoch)
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in files:
            info = zipfile.ZipInfo(source.archive_path, date_time=timestamp)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, source.path.read_bytes(), compresslevel=9)


def _validate_member_name(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise PackageError(f"unsafe archive member: {name}")
    if path.parts[0] != PLUGIN_NAME:
        raise PackageError(f"archive member is outside plugin root: {name}")


def verify_archive(archive_path: Path, files: list[SourceFile], tag: str) -> None:
    expected = {source.archive_path: source.sha256 for source in files}
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise PackageError("archive contains duplicate members")
        for name in names:
            _validate_member_name(name)
        if set(names) != set(expected):
            missing = sorted(set(expected) - set(names))
            extra = sorted(set(names) - set(expected))
            raise PackageError(f"archive member mismatch; missing={missing}, extra={extra}")
        for name, expected_hash in expected.items():
            if _sha256_bytes(archive.read(name)) != expected_hash:
                raise PackageError(f"archive member hash mismatch: {name}")
        metadata = archive.read(f"{PLUGIN_NAME}/metadata.yaml").decode("utf-8")
        version_line = next(
            (line for line in metadata.splitlines() if line.partition(":")[0].strip() == "version"),
            "",
        )
        actual_version = version_line.partition(":")[2].strip().strip("\"'")
        if actual_version != tag:
            raise PackageError(f"archive metadata version {actual_version!r} does not match {tag}")

        with tempfile.TemporaryDirectory(prefix="grok2api-release-smoke-") as temp_dir:
            archive.extractall(temp_dir)
            plugin_root = Path(temp_dir) / PLUGIN_NAME
            if not compileall.compile_dir(plugin_root, quiet=1, force=True):
                raise PackageError("archive Python syntax smoke check failed")


def build_package(root: Path, tag: str, output_dir: Path) -> dict:
    _validate_tag(tag)
    root = root.resolve()
    if _metadata_version(root / "metadata.yaml") != tag:
        raise PackageError(f"metadata.yaml version must match release tag {tag}")

    output_dir = output_dir.resolve()
    archive_path = output_dir / f"{PLUGIN_NAME}-{tag}.zip"
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    for path in (archive_path, checksum_path):
        _ensure_new_output(path)

    files = collect_source_files(root)
    epoch = _source_epoch(root)
    try:
        _write_archive(archive_path, files, epoch)
        verify_archive(archive_path, files, tag)
        archive_hash = _sha256_file(archive_path)
        result = {
            "plugin": PLUGIN_NAME,
            "tag": tag,
            "archive": {"name": archive_path.name, "sha256": archive_hash},
        }
        checksum_path.write_text(f"{archive_hash}  {archive_path.name}\n", encoding="ascii")
    except Exception:
        for path in (archive_path, checksum_path):
            if path.exists():
                path.unlink()
        raise
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="Stable release tag, for example v0.1.4")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = build_package(args.root, args.tag, args.output_dir)
    except (OSError, PackageError, zipfile.BadZipFile) as exc:
        print(f"release package failed: {exc}", file=os.sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
