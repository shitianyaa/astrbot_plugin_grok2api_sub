from __future__ import annotations

from pathlib import Path

import pytest

from scripts.extract_changelog import (
    ChangelogExtractError,
    extract_release_notes,
    main,
)

SAMPLE_CHANGELOG = """# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- Some unreleased feature

## v1.2.0 (2026-08-15)

### Added
- New major feature A
- New feature B

### Fixed
- Fixed bug X

## v1.1.0 (2026-08-10)

### Changed
- Refactored component Y

## v1.0.0 (2026-08-01)

### Added
- Initial release
"""


def test_extract_release_notes_middle_version() -> None:
    notes = extract_release_notes(SAMPLE_CHANGELOG, "v1.2.0")
    assert "### Added" in notes
    assert "New major feature A" in notes
    assert "### Fixed" in notes
    assert "Fixed bug X" in notes
    assert "Refactored component Y" not in notes
    assert "Some unreleased feature" not in notes


def test_extract_release_notes_without_v_prefix() -> None:
    notes = extract_release_notes(SAMPLE_CHANGELOG, "1.2.0")
    assert "### Added" in notes
    assert "New major feature A" in notes
    assert "Refactored component Y" not in notes


def test_extract_release_notes_last_version() -> None:
    notes = extract_release_notes(SAMPLE_CHANGELOG, "v1.0.0")
    assert "### Added" in notes
    assert "Initial release" in notes
    assert "Refactored component Y" not in notes


def test_extract_release_notes_missing_tag_raises_error() -> None:
    with pytest.raises(ChangelogExtractError, match="No release section found"):
        extract_release_notes(SAMPLE_CHANGELOG, "v9.9.9")


def test_extract_release_notes_empty_section_raises_error() -> None:
    empty_changelog = """# Changelog

## v1.0.0 (2026-08-01)

## v0.9.0 (2026-07-01)
### Added
- Something
"""
    with pytest.raises(ChangelogExtractError, match="is empty"):
        extract_release_notes(empty_changelog, "v1.0.0")


def test_main_cli_extract_to_file(tmp_path: Path) -> None:
    changelog_file = tmp_path / "CHANGELOG.md"
    changelog_file.write_text(SAMPLE_CHANGELOG, encoding="utf-8")
    output_file = tmp_path / "dist" / "release-notes.md"

    code = main(
        ["--tag", "v1.2.0", "--changelog", str(changelog_file), "--output", str(output_file)]
    )
    assert code == 0
    assert output_file.exists()
    content = output_file.read_text(encoding="utf-8")
    assert "New major feature A" in content


def test_main_cli_missing_changelog(tmp_path: Path) -> None:
    missing_file = tmp_path / "NON_EXISTENT.md"
    code = main(["--tag", "v1.2.0", "--changelog", str(missing_file)])
    assert code == 1


def test_main_cli_missing_tag(tmp_path: Path) -> None:
    changelog_file = tmp_path / "CHANGELOG.md"
    changelog_file.write_text(SAMPLE_CHANGELOG, encoding="utf-8")
    code = main(["--tag", "v9.9.9", "--changelog", str(changelog_file)])
    assert code == 1
