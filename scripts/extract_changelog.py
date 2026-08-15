#!/usr/bin/env python3
"""Extract version-specific release notes from CHANGELOG.md."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

VERSION_HEADING_RE = re.compile(
    r"^##\s+\[?(?P<tag>v?(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))\]?(?:\s+\((?P<date>\d{4}-\d{2}-\d{2})\))?\s*$",
    re.IGNORECASE,
)


class ChangelogExtractError(RuntimeError):
    """Raised when release notes extraction from changelog fails."""


def _normalise_tag(tag: str) -> str:
    cleaned = tag.strip()
    if not cleaned.startswith("v") and not cleaned.startswith("V"):
        cleaned = f"v{cleaned}"
    return cleaned.lower()


def extract_release_notes(changelog_text: str, tag: str) -> str:
    """Extract release notes section for a given tag from changelog text.

    Args:
        changelog_text: Full text of CHANGELOG.md.
        tag: Version tag, e.g. 'v0.1.5' or '0.1.5'.

    Returns:
        The extracted release notes as a markdown string.

    Raises:
        ChangelogExtractError: If the tag heading is not found or the section is empty.
    """
    target_tag = _normalise_tag(tag)
    lines = changelog_text.splitlines()

    section_start: int | None = None
    section_end: int | None = None

    for idx, line in enumerate(lines):
        if not line.startswith("## "):
            continue

        match = VERSION_HEADING_RE.match(line)
        if match:
            found_tag = _normalise_tag(match.group("tag"))
            if found_tag == target_tag:
                section_start = idx + 1
                continue

        if section_start is not None:
            section_end = idx
            break

    if section_start is None:
        raise ChangelogExtractError(f"No release section found for tag '{tag}' in CHANGELOG.md")

    if section_end is None:
        section_end = len(lines)

    extracted_lines = lines[section_start:section_end]

    # Trim leading and trailing empty lines
    while extracted_lines and not extracted_lines[0].strip():
        extracted_lines.pop(0)
    while extracted_lines and not extracted_lines[-1].strip():
        extracted_lines.pop()

    content = "\n".join(extracted_lines).strip()
    if not content:
        raise ChangelogExtractError(f"Release section for tag '{tag}' in CHANGELOG.md is empty")

    return f"{content}\n"


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract version-specific release notes from CHANGELOG.md"
    )
    parser.add_argument(
        "--tag",
        required=True,
        help="Release version tag, e.g. v0.1.5 or 0.1.5",
    )
    parser.add_argument(
        "--changelog",
        type=Path,
        default=Path("CHANGELOG.md"),
        help="Path to CHANGELOG.md (default: CHANGELOG.md)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Path to output markdown file (default: stdout)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.changelog.exists():
        print(f"Error: changelog file not found: {args.changelog}", file=sys.stderr)
        return 1

    try:
        changelog_text = args.changelog.read_text(encoding="utf-8")
        notes = extract_release_notes(changelog_text, args.tag)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.output:
        try:
            _atomic_write_text(args.output, notes)
            print(f"Extracted release notes for {args.tag} to {args.output}")
        except OSError as exc:
            print(f"Error writing output file {args.output}: {exc}", file=sys.stderr)
            return 1
    else:
        sys.stdout.write(notes)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
