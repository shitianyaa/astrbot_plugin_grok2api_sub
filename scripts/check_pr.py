"""Validate the machine-readable release-note JSON block in a pull request body.

Called by CI on pull_request events with a JSON GitHub event payload. The entry
exit code is 0 when the single mandatory release-note block is present and valid.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

RELEASE_NOTE_BLOCK_RE = re.compile(
    r"<!--\s*release-note:\s*(\{.*?\})\s*--\s*>?",
    re.DOTALL,
)

REPOSITORY_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]+$")
ISSUE_RE = re.compile(
    r"^https://github\.com/(?P<repository>[A-Za-z0-9-]+/[A-Za-z0-9_.-]+)/issues/(?P<number>[1-9]\d*)$"
)

ALLOWED_CATEGORIES = {
    "Added",
    "Changed",
    "Fixed",
    "Removed",
    "Security",
    "Documentation",
    "Maintenance",
    "None",
}


class CheckPrError(RuntimeError):
    """Raised when the pull request body does not satisfy the release-note contract."""


@dataclass
class PrCheckResult:
    body: str = ""
    block_match: re.Match[str] | None = None
    raw: dict[str, object] = field(default_factory=dict)
    category: str | None = None
    breaking: bool | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, object]:
        return {
            "found": self.block_match is not None,
            "category": self.category,
            "breaking": self.breaking,
            "errors": self.errors,
        }


def _require_readable(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CheckPrError(f"missing input file: {path}") from exc
    except UnicodeDecodeError as exc:
        raise CheckPrError(f"input is not valid UTF-8: {path}") from exc
    except OSError as exc:
        raise CheckPrError(f"unable to read {path}: {type(exc).__name__}") from exc


def _find_single_block(body: str) -> tuple[re.Match[str] | None, list[str]]:
    matches = list(RELEASE_NOTE_BLOCK_RE.finditer(body))
    errors: list[str] = []
    if not matches:
        return None, errors
    if len(matches) > 1:
        errors.append("exactly one release-note block is required; found several")
    return matches[0], errors


def _validate_issue(value: object, result: PrCheckResult, repository: str | None) -> None:
    if not isinstance(value, str):
        result.errors.append("issue must be empty, #N, or a same-repository GitHub Issue URL")
        return
    issue = value.strip()
    if not issue:
        return
    if issue.startswith("#"):
        if issue[1:].isdigit() and int(issue[1:]) > 0:
            return
        result.errors.append("issue must use a positive #N reference")
        return
    match = ISSUE_RE.fullmatch(issue)
    if match is None:
        result.errors.append("issue must be empty, #N, or a same-repository GitHub Issue URL")
        return
    if repository is None:
        result.errors.append("repository context is required to validate an Issue URL")
    elif match.group("repository").casefold() != repository.casefold():
        result.errors.append("issue URL must belong to the current repository")


def _validate_platforms(value: object, result: PrCheckResult) -> None:
    if not isinstance(value, list) or not value:
        result.errors.append("platforms must be a non-empty JSON array")
        return
    seen: set[str] = set()
    for index, platform in enumerate(value):
        if not isinstance(platform, str) or not platform.strip():
            result.errors.append(f"platforms[{index}] must be a non-empty string")
            continue
        key = platform.strip().casefold()
        if key in seen:
            result.errors.append(f"platforms[{index}] is duplicated")
        seen.add(key)


def check_pr_body(body: str, *, repository: str | None = None) -> PrCheckResult:
    """Validate the PR body and its single release-note block. Never prints secrets."""
    result = PrCheckResult(body=body)
    if not body.strip():
        result.errors.append("pull request body is empty")
        return result

    match, block_errors = _find_single_block(body)
    result.block_match = match
    result.errors.extend(block_errors)
    if match is None:
        result.errors.append("missing release-note <!-- release-note: {...} --> block")
        return result

    raw_text = match.group(1)
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        result.errors.append(f"release-note block is not valid JSON: {exc}")
        return result
    if not isinstance(raw, dict):
        result.errors.append("release-note block must be a JSON object")
        return result
    result.raw = raw
    if repository is not None and not REPOSITORY_RE.fullmatch(repository):
        result.errors.append("repository context is invalid")
        repository = None

    category = raw.get("category")
    if category not in ALLOWED_CATEGORIES:
        result.errors.append(
            f"category must be one of {sorted(ALLOWED_CATEGORIES)}; got {category!r}"
        )
    elif isinstance(category, str):
        result.category = category

    breaking = raw.get("breaking")
    if not isinstance(breaking, bool):
        result.errors.append("breaking must be a boolean true/false")
    else:
        result.breaking = breaking

    summary_zh_cn = raw.get("summary_zh_cn")
    summary_en = raw.get("summary_en")
    none_reason = raw.get("none_reason")
    migration_zh_cn = raw.get("migration_zh_cn")
    migration_en = raw.get("migration_en")

    _validate_issue(raw.get("issue", ""), result, repository)
    _validate_platforms(raw.get("platforms"), result)

    if category == "None":
        if breaking is True:
            result.errors.append("category=None cannot set breaking=true")
        for _, value in (
            ("summary_zh_cn", summary_zh_cn),
            ("summary_en", summary_en),
            ("migration_zh_cn", migration_zh_cn),
            ("migration_en", migration_en),
        ):
            if isinstance(value, str) and value.strip():
                result.errors.append(f"category=None must leave {_} empty")
        if not isinstance(none_reason, str) or not none_reason.strip():
            result.errors.append("category=None requires a non-empty none_reason")
    else:
        if not isinstance(summary_zh_cn, str) or not summary_zh_cn.strip():
            result.errors.append("non-None category requires a non-empty summary_zh_cn")
        if not isinstance(summary_en, str) or not summary_en.strip():
            result.errors.append("non-None category requires a non-empty summary_en")
        if breaking is True:
            if not isinstance(migration_zh_cn, str) or not migration_zh_cn.strip():
                result.errors.append("breaking=true requires a migration_zh_cn note")
            if not isinstance(migration_en, str) or not migration_en.strip():
                result.errors.append("breaking=true requires a migration_en note")
        elif breaking is False:
            if (isinstance(migration_zh_cn, str) and migration_zh_cn.strip()) or (
                isinstance(migration_en, str) and migration_en.strip()
            ):
                result.errors.append(
                    "migration instructions require breaking=true (or a compatible note)"
                )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event",
        type=Path,
        required=True,
        help="path to the GitHub webhook payload JSON (GITHUB_EVENT_PATH)",
    )
    parser.add_argument("--body-file", type=Path, help="explicit PR body file (testing)")
    parser.add_argument("--repository", help="repository owner/name (testing or custom payloads)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        body_text = _require_readable(args.body_file) if args.body_file else None
        if body_text is None:
            event = json.loads(_require_readable(args.event))
            body_text = str((event.get("pull_request") or {}).get("body") or "")
            repository_data = event.get("repository")
            event_repository = (
                repository_data.get("full_name") if isinstance(repository_data, dict) else None
            )
        else:
            event_repository = None
        result = check_pr_body(body_text, repository=args.repository or event_repository)
    except (CheckPrError, json.JSONDecodeError, OSError) as exc:
        print(f"check_pr failed: {exc}", file=sys.stderr)
        return 1
    for error in result.errors:
        print(f"release-note error: {error}", file=sys.stderr)
    if result.ok:
        print("release-note checks passed")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
