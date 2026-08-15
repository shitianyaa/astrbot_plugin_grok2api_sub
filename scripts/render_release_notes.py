#!/usr/bin/env python3
"""Render deterministic bilingual release notes and contributor audit data."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

CATEGORIES = (
    "Added",
    "Changed",
    "Fixed",
    "Removed",
    "Security",
    "Documentation",
    "Maintenance",
)
CATEGORY_ZH_CN = {
    "Added": "新增",
    "Changed": "变更",
    "Fixed": "修复",
    "Removed": "移除",
    "Security": "安全",
    "Documentation": "文档",
    "Maintenance": "维护",
}
TAG_RE = re.compile(r"^v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")
REPOSITORY_RE = re.compile(
    r"^(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/(?P<repo>[A-Za-z0-9_.-]+)$"
)
REPOSITORY_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9][A-Za-z0-9-]{0,38})/"
    r"(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
PR_URL_RE = re.compile(
    r"^https://github\.com/(?P<repository>[A-Za-z0-9-]+/[A-Za-z0-9_.-]+)/"
    r"pull/(?P<number>[1-9]\d*)$"
)
ISSUE_URL_RE = re.compile(
    r"^https://github\.com/(?P<repository>[A-Za-z0-9-]+/[A-Za-z0-9_.-]+)/"
    r"issues/(?P<number>[1-9]\d*)$"
)
COMMIT_URL_RE = re.compile(
    r"^https://github\.com/(?P<repository>[A-Za-z0-9-]+/[A-Za-z0-9_.-]+)/"
    r"commit/(?P<sha>[0-9a-fA-F]{40})$"
)
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
BOT_LOGIN_RE = re.compile(r"^[A-Za-z0-9-]+\[bot\]$", re.IGNORECASE)
UNMAPPED_REASONS = {
    "no_associated_pr",
    "missing_release_note",
    "missing_public_contributor",
    "manual_review_required",
}
UNMAPPED_REASON_EN = {
    "no_associated_pr": "no associated pull request",
    "missing_release_note": "missing bilingual release-note metadata",
    "missing_public_contributor": "missing an explicitly attributed public GitHub login",
    "manual_review_required": "manual attribution review required",
}
UNMAPPED_REASON_ZH_CN = {
    "no_associated_pr": "未关联 Pull Request",
    "missing_release_note": "缺少双语 release-note metadata",
    "missing_public_contributor": "缺少显式归因的公开 GitHub login",
    "manual_review_required": "需要人工归因审核",
}


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


class ReleaseNotesError(RuntimeError):
    """Raised when release-note input cannot satisfy the audit contract."""

    def __init__(
        self,
        issues: ValidationIssue | Iterable[ValidationIssue],
        *,
        audit: dict[str, object] | None = None,
    ) -> None:
        if isinstance(issues, ValidationIssue):
            self.issues = [issues]
        else:
            self.issues = list(issues)
        self.audit = audit
        super().__init__("; ".join(issue.message for issue in self.issues))


@dataclass(frozen=True)
class Author:
    login: str
    is_bot: bool

    @property
    def key(self) -> str:
        return self.login.casefold()

    @property
    def profile_url(self) -> str:
        return f"https://github.com/{self.login}"


@dataclass(frozen=True)
class ReleaseNote:
    category: str
    breaking: bool
    summary_zh_cn: str
    summary_en: str
    migration_zh_cn: str
    migration_en: str
    issue_url: str | None
    issue_number: int | None
    platforms: tuple[str, ...]
    none_reason: str


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    merged_at: datetime
    author: Author
    coauthors: tuple[Author, ...]
    note: ReleaseNote


@dataclass(frozen=True)
class DirectCommit:
    sha: str
    url: str
    committed_at: datetime | None
    authors: tuple[Author, ...]
    note: ReleaseNote


@dataclass(frozen=True)
class UnmappedCommit:
    sha: str
    url: str
    reason: str


@dataclass(frozen=True)
class ChangeEntry:
    source_kind: str
    source_id: str
    source_url: str
    sort_key: tuple[str, int, str]
    note: ReleaseNote

    def source_markdown(self) -> str:
        if self.source_kind == "pr":
            return f"[#{self.source_id}]({self.source_url})"
        return f"[`{self.source_id[:7]}`]({self.source_url})"


@dataclass
class Contributor:
    login: str
    pull_requests: dict[int, str] = field(default_factory=dict)
    commits: dict[str, str] = field(default_factory=dict)

    @property
    def profile_url(self) -> str:
        return f"https://github.com/{self.login}"

    def add_author(self, author: Author) -> None:
        if (author.login.casefold(), author.login) < (self.login.casefold(), self.login):
            self.login = author.login


@dataclass(frozen=True)
class RenderedRelease:
    markdown: str
    audit: dict[str, object]


def _raise(code: str, path: str, message: str) -> None:
    raise ReleaseNotesError(ValidationIssue(code, path, message))


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _raise("missing_file", label, f"required input is missing: {path}")
    except UnicodeDecodeError:
        _raise("invalid_text", label, f"input is not valid UTF-8: {path}")
    except json.JSONDecodeError as exc:
        _raise("invalid_json", label, f"invalid JSON at line {exc.lineno}, column {exc.colno}")
    except OSError as exc:
        _raise("read_failed", label, f"unable to read input: {type(exc).__name__}")


def _mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _raise("invalid_type", path, "value must be a JSON object")
    return value


def _list(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        _raise("invalid_type", path, "value must be a JSON array")
    return value


def _single_line(value: object, path: str, *, required: bool = False) -> str:
    if not isinstance(value, str):
        _raise("invalid_type", path, "value must be a string")
    normalised = " ".join(value.split())
    if required and not normalised:
        _raise("missing_value", path, "value must not be empty")
    return normalised


def _integer(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _raise("invalid_number", path, "value must be a positive integer")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        _raise("invalid_type", path, "value must be a boolean")
    return value


def _parse_datetime(value: object, path: str) -> datetime:
    text = _single_line(value, path, required=True)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _raise("invalid_datetime", path, "value must be an ISO-8601 timestamp")
    if parsed.tzinfo is None:
        _raise("invalid_datetime", path, "timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _normalise_repository(value: str) -> str:
    candidate = value.strip().removesuffix("/")
    url_match = REPOSITORY_URL_RE.fullmatch(candidate)
    if url_match:
        candidate = f"{url_match.group('owner')}/{url_match.group('repo')}"
    match = REPOSITORY_RE.fullmatch(candidate)
    if match is None:
        _raise(
            "invalid_repository", "repository", "repository must be owner/name or its GitHub URL"
        )
    return f"{match.group('owner')}/{match.group('repo')}"


def _extract_repository(url: object) -> str | None:
    if not isinstance(url, str):
        return None
    for pattern in (PR_URL_RE, COMMIT_URL_RE, ISSUE_URL_RE):
        match = pattern.fullmatch(url.strip())
        if match:
            return match.group("repository")
    return None


def _resolve_repository(
    explicit: str | None,
    raw_prs: Sequence[object],
    raw_direct_commits: Sequence[object],
    raw_unmapped_commits: Sequence[object],
) -> str:
    repositories: list[str] = []
    for raw in (*raw_prs, *raw_direct_commits, *raw_unmapped_commits):
        if isinstance(raw, Mapping):
            repository = _extract_repository(raw.get("url"))
            if repository:
                repositories.append(repository)

    repository = _normalise_repository(explicit) if explicit else None
    if repository is None and repositories:
        repository = min(repositories, key=lambda item: (item.casefold(), item))
    if repository is None:
        _raise(
            "missing_repository",
            "repository",
            "repository cannot be inferred; provide --repository owner/name",
        )
    if any(item.casefold() != repository.casefold() for item in repositories):
        _raise("repository_mismatch", "sources", "all source URLs must belong to one repository")
    return repository


def _validate_source_url(
    value: object,
    path: str,
    *,
    repository: str,
    kind: str,
    identifier: int | str,
) -> str:
    url = _single_line(value, path, required=True)
    pattern = PR_URL_RE if kind == "pr" else COMMIT_URL_RE
    match = pattern.fullmatch(url)
    if match is None:
        _raise("invalid_url", path, f"value must be a canonical GitHub {kind} URL")
    if match.group("repository").casefold() != repository.casefold():
        _raise("repository_mismatch", path, "source URL belongs to another repository")
    matched_identifier = match.group("number" if kind == "pr" else "sha")
    if str(matched_identifier).casefold() != str(identifier).casefold():
        _raise("source_mismatch", path, "source URL does not match its identifier")
    return url


def _parse_author(value: object, path: str) -> Author:
    raw = _mapping(value, path)
    login = _single_line(raw.get("login"), f"{path}.login", required=True)
    is_bot = _boolean(raw.get("is_bot"), f"{path}.is_bot")
    if not LOGIN_RE.fullmatch(login) and not BOT_LOGIN_RE.fullmatch(login):
        _raise("invalid_login", f"{path}.login", "value must be a public GitHub login")
    detected_bot = (
        is_bot
        or login.casefold().endswith("[bot]")
        or login.casefold()
        in {
            "github-actions",
            "github-actions[bot]",
        }
    )
    return Author(login=login, is_bot=detected_bot)


def _parse_authors(value: object, path: str) -> tuple[Author, ...]:
    raw_authors = _list(value, path)
    authors: dict[str, Author] = {}
    for index, raw_author in enumerate(raw_authors):
        author = _parse_author(raw_author, f"{path}[{index}]")
        previous = authors.get(author.key)
        if previous is not None:
            _raise("duplicate_contributor", f"{path}[{index}]", "contributor login is duplicated")
        authors[author.key] = author
    return tuple(sorted(authors.values(), key=lambda item: (item.key, item.login)))


def _parse_issue(value: object, path: str, repository: str) -> tuple[str | None, int | None]:
    issue = _single_line(value, path)
    if not issue:
        return None, None
    if issue.startswith("#") and issue[1:].isdigit() and int(issue[1:]) > 0:
        number = int(issue[1:])
        return f"https://github.com/{repository}/issues/{number}", number
    match = ISSUE_URL_RE.fullmatch(issue)
    if match is None:
        _raise("invalid_issue", path, "issue must be #N or a same-repository GitHub Issue URL")
    if match.group("repository").casefold() != repository.casefold():
        _raise("repository_mismatch", path, "issue URL belongs to another repository")
    return issue, int(match.group("number"))


def _parse_release_note(value: object, path: str, repository: str) -> ReleaseNote:
    raw = _mapping(value, path)
    category = _single_line(raw.get("category"), f"{path}.category", required=True)
    if category not in (*CATEGORIES, "None"):
        _raise("invalid_category", f"{path}.category", "unsupported release-note category")
    breaking = _boolean(raw.get("breaking"), f"{path}.breaking")
    summary_zh_cn = _single_line(raw.get("summary_zh_cn"), f"{path}.summary_zh_cn")
    summary_en = _single_line(raw.get("summary_en"), f"{path}.summary_en")
    migration_zh_cn = _single_line(raw.get("migration_zh_cn"), f"{path}.migration_zh_cn")
    migration_en = _single_line(raw.get("migration_en"), f"{path}.migration_en")
    none_reason = _single_line(raw.get("none_reason", ""), f"{path}.none_reason")
    issue_url, issue_number = _parse_issue(raw.get("issue", ""), f"{path}.issue", repository)

    platforms_raw = _list(raw.get("platforms"), f"{path}.platforms")
    platforms: list[str] = []
    for index, platform in enumerate(platforms_raw):
        name = _single_line(platform, f"{path}.platforms[{index}]", required=True)
        if name.casefold() in {item.casefold() for item in platforms}:
            _raise("duplicate_platform", f"{path}.platforms[{index}]", "platform is duplicated")
        platforms.append(name)
    if not platforms:
        _raise("missing_value", f"{path}.platforms", "at least one platform is required")

    if category == "None":
        if breaking:
            _raise("invalid_none_note", f"{path}.breaking", "category=None cannot be breaking")
        if summary_zh_cn or summary_en or migration_zh_cn or migration_en:
            _raise(
                "invalid_none_note",
                path,
                "category=None must not contain summaries or migration instructions",
            )
        if not none_reason:
            _raise("missing_none_reason", f"{path}.none_reason", "category=None requires a reason")
    else:
        if not summary_zh_cn or not summary_en:
            _raise(
                "missing_bilingual_summary",
                path,
                "non-None notes require both Chinese and English summaries",
            )
        if breaking and (not migration_zh_cn or not migration_en):
            _raise(
                "missing_bilingual_migration",
                path,
                "breaking notes require both Chinese and English migration instructions",
            )
        if not breaking and (migration_zh_cn or migration_en):
            _raise(
                "unexpected_migration",
                path,
                "migration instructions require breaking=true",
            )

    return ReleaseNote(
        category=category,
        breaking=breaking,
        summary_zh_cn=summary_zh_cn,
        summary_en=summary_en,
        migration_zh_cn=migration_zh_cn,
        migration_en=migration_en,
        issue_url=issue_url,
        issue_number=issue_number,
        platforms=tuple(platforms),
        none_reason=none_reason,
    )


def _parse_pr(value: object, index: int, repository: str) -> PullRequest:
    path = f"prs[{index}]"
    raw = _mapping(value, path)
    number = _integer(raw.get("number"), f"{path}.number")
    url = _validate_source_url(
        raw.get("url"), f"{path}.url", repository=repository, kind="pr", identifier=number
    )
    if raw.get("in_range", True) is not True:
        _raise(
            "source_out_of_range", f"{path}.in_range", "pull request is outside the release range"
        )
    merged_at = _parse_datetime(raw.get("merged_at"), f"{path}.merged_at")
    author = _parse_author(raw.get("author"), f"{path}.author")
    coauthors = _parse_authors(raw.get("coauthors", []), f"{path}.coauthors")
    if author.key in {item.key for item in coauthors}:
        _raise("duplicate_contributor", f"{path}.coauthors", "PR author is repeated as a coauthor")
    note = _parse_release_note(raw.get("release_note"), f"{path}.release_note", repository)
    return PullRequest(number, url, merged_at, author, coauthors, note)


def _parse_direct_commit(
    value: object,
    index: int,
    repository: str,
) -> DirectCommit | UnmappedCommit:
    path = f"direct_commits[{index}]"
    raw = _mapping(value, path)
    sha = _single_line(raw.get("sha"), f"{path}.sha", required=True).lower()
    if SHA_RE.fullmatch(sha) is None:
        _raise("invalid_sha", f"{path}.sha", "commit SHA must contain exactly 40 hexadecimal chars")
    url = _validate_source_url(
        raw.get("url"), f"{path}.url", repository=repository, kind="commit", identifier=sha
    )
    associated = _list(raw.get("associated_pr_numbers", []), f"{path}.associated_pr_numbers")
    if associated:
        _raise(
            "unexpected_associated_pr",
            f"{path}.associated_pr_numbers",
            "direct commit input must not contain associated pull requests",
        )
    if "release_note" not in raw:
        return UnmappedCommit(sha, url, "missing_release_note")
    if "authors" not in raw or raw.get("authors") == []:
        return UnmappedCommit(sha, url, "missing_public_contributor")
    authors = _parse_authors(raw.get("authors"), f"{path}.authors")
    if not any(not author.is_bot for author in authors):
        return UnmappedCommit(sha, url, "missing_public_contributor")
    committed_at = None
    if raw.get("committed_at") not in (None, ""):
        committed_at = _parse_datetime(raw.get("committed_at"), f"{path}.committed_at")
    note = _parse_release_note(raw.get("release_note"), f"{path}.release_note", repository)
    return DirectCommit(sha, url, committed_at, authors, note)


def _parse_unmapped_commit(value: object, index: int, repository: str) -> UnmappedCommit:
    path = f"unmapped_commits[{index}]"
    raw = _mapping(value, path)
    sha = _single_line(raw.get("sha"), f"{path}.sha", required=True).lower()
    if SHA_RE.fullmatch(sha) is None:
        _raise("invalid_sha", f"{path}.sha", "commit SHA must contain exactly 40 hexadecimal chars")
    url = _validate_source_url(
        raw.get("url"), f"{path}.url", repository=repository, kind="commit", identifier=sha
    )
    reason = _single_line(raw.get("reason", "no_associated_pr"), f"{path}.reason", required=True)
    if reason not in UNMAPPED_REASONS:
        _raise("invalid_reason", f"{path}.reason", "unsupported unmapped-commit reason")
    return UnmappedCommit(sha, url, reason)


def _split_pr_payload(payload: object) -> tuple[list[object], list[object], list[object]]:
    if isinstance(payload, list):
        return payload, [], []
    raw = _mapping(payload, "prs_json")
    prs = _list(raw.get("prs"), "prs_json.prs")
    direct = _list(raw.get("direct_commits", []), "prs_json.direct_commits")
    unmapped = _list(raw.get("unmapped_commits", []), "prs_json.unmapped_commits")
    return prs, direct, unmapped


def _split_commits_payload(payload: object) -> tuple[list[object], list[object]]:
    if isinstance(payload, list):
        return payload, []
    raw = _mapping(payload, "commits_json")
    commits_value = raw.get("direct_commits", raw.get("commits", []))
    direct = _list(commits_value, "commits_json.direct_commits")
    unmapped = _list(raw.get("unmapped_commits", []), "commits_json.unmapped_commits")
    return direct, unmapped


def _parse_history(payload: object) -> set[str]:
    if isinstance(payload, Mapping):
        payload = payload.get("contributors")
    raw_logins = _list(payload, "history_contributors_json")
    history: set[str] = set()
    for index, raw_login in enumerate(raw_logins):
        login = _single_line(raw_login, f"history_contributors_json[{index}]", required=True)
        if not LOGIN_RE.fullmatch(login) and not BOT_LOGIN_RE.fullmatch(login):
            _raise(
                "invalid_login",
                f"history_contributors_json[{index}]",
                "value must be a public GitHub login",
            )
        key = login.casefold()
        if key in history:
            _raise(
                "duplicate_contributor",
                f"history_contributors_json[{index}]",
                "historical contributor is duplicated",
            )
        history.add(key)
    return history


def _validate_changelog(path: Path, tag: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        _raise("missing_file", "changelog", f"required changelog is missing: {path}")
    except UnicodeDecodeError:
        _raise("invalid_text", "changelog", "changelog is not valid UTF-8")
    except OSError as exc:
        _raise("read_failed", "changelog", f"unable to read changelog: {type(exc).__name__}")

    lines = text.splitlines()
    target_patterns = (
        re.compile(rf"^##\s+{re.escape(tag)}(?:\s+\([^)]*\))?\s*$"),
        re.compile(rf"^##\s+\[{re.escape(tag)}\](?:\s+\([^)]*\))?\s*$"),
    )
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if any(pattern.fullmatch(line) for pattern in target_patterns)
        ),
        None,
    )
    section_name = tag
    if start is None:
        start = next(
            (
                index
                for index, line in enumerate(lines)
                if re.fullmatch(r"##\s+\[?Unreleased\]?\s*", line, re.IGNORECASE)
            ),
            None,
        )
        section_name = "Unreleased"
    if start is None:
        _raise(
            "missing_changelog_section",
            "changelog",
            f"changelog must contain {tag} or Unreleased",
        )
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    categories = [line[4:].strip() for line in lines[start + 1 : end] if line.startswith("### ")]
    invalid = sorted({category for category in categories if category not in CATEGORIES})
    if invalid:
        _raise(
            "invalid_changelog_category",
            "changelog",
            f"unsupported category in {section_name}: {', '.join(invalid)}",
        )
    if not categories:
        _raise(
            "missing_changelog_category",
            "changelog",
            f"{section_name} must contain at least one supported category",
        )
    return section_name


def _changes(prs: Sequence[PullRequest], commits: Sequence[DirectCommit]) -> list[ChangeEntry]:
    entries: list[ChangeEntry] = []
    for pr in prs:
        if pr.note.category == "None":
            continue
        entries.append(
            ChangeEntry(
                source_kind="pr",
                source_id=str(pr.number),
                source_url=pr.url,
                sort_key=(pr.merged_at.isoformat(), 0, f"{pr.number:010d}"),
                note=pr.note,
            )
        )
    for commit in commits:
        if commit.note.category == "None":
            continue
        entries.append(
            ChangeEntry(
                source_kind="commit",
                source_id=commit.sha,
                source_url=commit.url,
                sort_key=(
                    commit.committed_at.isoformat() if commit.committed_at else "9999-12-31",
                    1,
                    commit.sha,
                ),
                note=commit.note,
            )
        )
    return sorted(entries, key=lambda item: item.sort_key)


def _contributors(
    prs: Sequence[PullRequest], commits: Sequence[DirectCommit]
) -> dict[str, Contributor]:
    contributors: dict[str, Contributor] = {}

    def add(
        author: Author, *, pr: PullRequest | None = None, commit: DirectCommit | None = None
    ) -> None:
        if author.is_bot:
            return
        contributor = contributors.setdefault(author.key, Contributor(author.login))
        contributor.add_author(author)
        if pr is not None:
            contributor.pull_requests[pr.number] = pr.url
        if commit is not None:
            contributor.commits[commit.sha] = commit.url

    for pr in prs:
        add(pr.author, pr=pr)
        for coauthor in pr.coauthors:
            add(coauthor, pr=pr)
    for commit in commits:
        for author in commit.authors:
            add(author, commit=commit)
    return contributors


def _contributor_sources(contributor: Contributor) -> list[str]:
    refs = [f"[#{number}]({url})" for number, url in sorted(contributor.pull_requests.items())]
    refs.extend(f"[`{sha[:7]}`]({url})" for sha, url in sorted(contributor.commits.items()))
    return refs


def _entry_line(entry: ChangeEntry, language: str) -> str:
    summary = entry.note.summary_en if language == "en" else entry.note.summary_zh_cn
    breaking = "**Breaking:** " if language == "en" else "**破坏性：** "
    prefix = breaking if entry.note.breaking else ""
    sources = [entry.source_markdown()]
    if entry.note.issue_url and entry.note.issue_number:
        sources.append(f"[Issue #{entry.note.issue_number}]({entry.note.issue_url})")
    separator = ", " if language == "en" else "、"
    return f"- {prefix}{summary} ({separator.join(sources)})"


def _render_language(
    *,
    language: str,
    repository: str,
    tag: str,
    previous_tag: str,
    changes: Sequence[ChangeEntry],
    contributors: Sequence[Contributor],
    new_contributors: Sequence[Contributor],
    unmapped_commits: Sequence[UnmappedCommit],
) -> list[str]:
    english = language == "en"
    lines = ["# English" if english else "# 简体中文", ""]
    for category in CATEGORIES:
        category_changes = [entry for entry in changes if entry.note.category == category]
        if not category_changes:
            continue
        lines.extend(
            [
                f"## {category if english else CATEGORY_ZH_CN[category]}",
                "",
                *[_entry_line(entry, language) for entry in category_changes],
                "",
            ]
        )

    breaking_changes = [entry for entry in changes if entry.note.breaking]
    if breaking_changes:
        lines.extend(["## Breaking Changes" if english else "## 破坏性变更", ""])
        for entry in breaking_changes:
            source = entry.source_markdown()
            description = (
                f"{source} is marked as a breaking change."
                if english
                else f"{source} 已标记为破坏性变更。"
            )
            lines.append(f"- {description}")
        lines.extend(["", "## Upgrade Guide" if english else "## 升级说明", ""])
        for entry in breaking_changes:
            migration = entry.note.migration_en if english else entry.note.migration_zh_cn
            separator = ": " if english else "："
            lines.append(f"- {entry.source_markdown()}{separator}{migration}")
        lines.append("")

    if contributors:
        lines.extend(["## Contributors" if english else "## 贡献者", ""])
        for contributor in contributors:
            refs = (", " if english else "、").join(_contributor_sources(contributor))
            lines.append(f"- [@{contributor.login}]({contributor.profile_url}) ({refs})")
        lines.append("")

    if new_contributors:
        lines.extend(["## New Contributors" if english else "## 新贡献者", ""])
        for contributor in new_contributors:
            refs = (", " if english else "、").join(
                f"[#{number}]({url})" for number, url in sorted(contributor.pull_requests.items())
            )
            if english:
                text = (
                    f"[@{contributor.login}]({contributor.profile_url}) made their first "
                    f"contribution in {refs}."
                )
            else:
                text = (
                    f"[@{contributor.login}]({contributor.profile_url}) 在 {refs} 中完成首次贡献。"
                )
            lines.append(f"- {text}")
        lines.append("")

    if unmapped_commits:
        lines.extend(["## Attribution Required" if english else "## 需人工归因", ""])
        intro = (
            "The following commits were released with an explicit manual override and still "
            "require contributor attribution:"
            if english
            else "以下提交经显式人工放行后进入发布，但仍需要补充贡献者归因："
        )
        lines.extend([intro, ""])
        reasons = UNMAPPED_REASON_EN if english else UNMAPPED_REASON_ZH_CN
        for commit in unmapped_commits:
            lines.append(f"- [`{commit.sha[:7]}`]({commit.url}): {reasons[commit.reason]}")
        lines.append("")

    compare_url = f"https://github.com/{repository}/compare/{previous_tag}...{tag}"
    label = "Full Changelog" if english else "完整变更"
    separator = ":" if english else "："
    lines.append(f"**{label}{separator}** [{previous_tag}...{tag}]({compare_url})")
    return lines


def _audit_contributor(contributor: Contributor) -> dict[str, object]:
    return {
        "login": contributor.login,
        "profile_url": contributor.profile_url,
        "pull_requests": [
            {"number": number, "url": url}
            for number, url in sorted(contributor.pull_requests.items())
        ],
        "commits": [{"sha": sha, "url": url} for sha, url in sorted(contributor.commits.items())],
    }


def render_release_notes(
    *,
    tag: str,
    previous_tag: str,
    changelog_path: Path,
    prs_json_path: Path,
    history_contributors_json_path: Path,
    commits_json_path: Path | None = None,
    repository: str | None = None,
    allow_unmapped_commits: bool = False,
) -> RenderedRelease:
    """Validate local release inputs and return deterministic Markdown and audit JSON."""

    if TAG_RE.fullmatch(tag) is None:
        _raise("invalid_tag", "tag", "release tag must be stable SemVer vX.Y.Z")
    if TAG_RE.fullmatch(previous_tag) is None:
        _raise("invalid_tag", "previous_tag", "previous tag must be stable SemVer vX.Y.Z")
    if tag == previous_tag:
        _raise("invalid_range", "previous_tag", "previous tag must differ from release tag")

    prs_payload = _read_json(prs_json_path, "prs_json")
    raw_prs, raw_direct_commits, raw_unmapped_commits = _split_pr_payload(prs_payload)
    if commits_json_path is not None:
        commits_payload = _read_json(commits_json_path, "commits_json")
        extra_direct, extra_unmapped = _split_commits_payload(commits_payload)
        raw_direct_commits.extend(extra_direct)
        raw_unmapped_commits.extend(extra_unmapped)
    history_payload = _read_json(history_contributors_json_path, "history_contributors_json")
    history = _parse_history(history_payload)
    repository = _resolve_repository(repository, raw_prs, raw_direct_commits, raw_unmapped_commits)
    changelog_section = _validate_changelog(changelog_path, tag)

    prs = [_parse_pr(raw, index, repository) for index, raw in enumerate(raw_prs)]
    pr_numbers = [pr.number for pr in prs]
    if len(set(pr_numbers)) != len(pr_numbers):
        _raise("duplicate_pr", "prs", "pull request numbers must be unique")
    prs.sort(key=lambda item: (item.merged_at, item.number))

    direct_commits: list[DirectCommit] = []
    unmapped_commits: list[UnmappedCommit] = []
    for index, raw in enumerate(raw_direct_commits):
        parsed = _parse_direct_commit(raw, index, repository)
        if isinstance(parsed, DirectCommit):
            direct_commits.append(parsed)
        else:
            unmapped_commits.append(parsed)
    unmapped_commits.extend(
        _parse_unmapped_commit(raw, index, repository)
        for index, raw in enumerate(raw_unmapped_commits)
    )

    all_commit_shas = [commit.sha for commit in direct_commits] + [
        commit.sha for commit in unmapped_commits
    ]
    if len(set(all_commit_shas)) != len(all_commit_shas):
        _raise("duplicate_commit", "commits", "commit SHAs must be unique")
    direct_commits.sort(
        key=lambda item: (
            item.committed_at.isoformat() if item.committed_at else "9999-12-31",
            item.sha,
        )
    )
    unmapped_commits.sort(key=lambda item: item.sha)

    changes = _changes(prs, direct_commits)
    contributor_map = _contributors(prs, direct_commits)
    contributors = sorted(
        contributor_map.values(), key=lambda item: (item.login.casefold(), item.login)
    )
    owner = repository.split("/", 1)[0].casefold()
    new_contributors = [
        contributor
        for contributor in contributors
        if contributor.login.casefold() not in history
        and contributor.login.casefold() != owner
        and contributor.pull_requests
    ]

    warnings: list[str] = []
    if unmapped_commits and allow_unmapped_commits:
        warnings.append(
            f"{len(unmapped_commits)} commit(s) require manual attribution; release override used"
        )

    audit: dict[str, object] = {
        "schema": 1,
        "status": "ok",
        "repository": repository,
        "tag": tag,
        "previous_tag": previous_tag,
        "changelog_section": changelog_section,
        "prs": [
            {
                "number": pr.number,
                "url": pr.url,
                "category": pr.note.category,
                "breaking": pr.note.breaking,
                "author": pr.author.login,
                "coauthors": [author.login for author in pr.coauthors],
            }
            for pr in prs
        ],
        "direct_commits": [
            {
                "sha": commit.sha,
                "url": commit.url,
                "category": commit.note.category,
                "breaking": commit.note.breaking,
                "contributors": [author.login for author in commit.authors if not author.is_bot],
            }
            for commit in direct_commits
        ],
        "contributors": [_audit_contributor(item) for item in contributors],
        "new_contributors": [_audit_contributor(item) for item in new_contributors],
        "unmapped_commits": [
            {"sha": commit.sha, "url": commit.url, "reason": commit.reason}
            for commit in unmapped_commits
        ],
        "warnings": warnings,
        "errors": [],
    }

    if unmapped_commits and not allow_unmapped_commits:
        issue = ValidationIssue(
            "unmapped_commits",
            "commits",
            "direct commits require explicit bilingual notes and public contributor attribution",
        )
        audit["status"] = "failed"
        audit["errors"] = [issue.as_dict()]
        raise ReleaseNotesError(issue, audit=audit)

    english = _render_language(
        language="en",
        repository=repository,
        tag=tag,
        previous_tag=previous_tag,
        changes=changes,
        contributors=contributors,
        new_contributors=new_contributors,
        unmapped_commits=unmapped_commits,
    )
    chinese = _render_language(
        language="zh_cn",
        repository=repository,
        tag=tag,
        previous_tag=previous_tag,
        changes=changes,
        contributors=contributors,
        new_contributors=new_contributors,
        unmapped_commits=unmapped_commits,
    )
    markdown = "\n".join([*english, "", "---", "", *chinese]).rstrip() + "\n"
    return RenderedRelease(markdown=markdown, audit=audit)


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


def _audit_text(audit: Mapping[str, object]) -> str:
    return json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _failed_audit(args: argparse.Namespace, error: ReleaseNotesError) -> dict[str, object]:
    if error.audit is not None:
        return error.audit
    return {
        "schema": 1,
        "status": "failed",
        "repository": args.repository,
        "tag": args.tag,
        "previous_tag": args.previous_tag,
        "prs": [],
        "direct_commits": [],
        "contributors": [],
        "new_contributors": [],
        "unmapped_commits": [],
        "warnings": [],
        "errors": [issue.as_dict() for issue in error.issues],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="release tag, for example v0.1.4")
    parser.add_argument("--previous-tag", required=True, help="previous stable release tag")
    parser.add_argument(
        "--repository", help="GitHub repository as owner/name; inferred when omitted"
    )
    parser.add_argument("--changelog", required=True, type=Path)
    parser.add_argument("--prs-json", required=True, type=Path)
    parser.add_argument("--commits-json", type=Path)
    parser.add_argument("--history-contributors-json", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="Markdown output path")
    parser.add_argument("--audit-output", type=Path, help="audit JSON output path")
    parser.add_argument(
        "--allow-unmapped-commits",
        action="store_true",
        help="allow release with an explicit bilingual attribution warning",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate inputs without writing Markdown or audit files",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.validate_only and args.output is None:
        parser.error("--output is required unless --validate-only is used")
    audit_output = args.audit_output
    if audit_output is None and args.output is not None:
        audit_output = args.output.parent / "release-notes-audit.json"

    try:
        rendered = render_release_notes(
            tag=args.tag,
            previous_tag=args.previous_tag,
            repository=args.repository,
            changelog_path=args.changelog,
            prs_json_path=args.prs_json,
            commits_json_path=args.commits_json,
            history_contributors_json_path=args.history_contributors_json,
            allow_unmapped_commits=args.allow_unmapped_commits,
        )
    except ReleaseNotesError as exc:
        if not args.validate_only and audit_output is not None:
            _atomic_write_text(audit_output, _audit_text(_failed_audit(args, exc)))
        for issue in exc.issues:
            print(f"{issue.code}: {issue.path}: {issue.message}", file=sys.stderr)
        return 1

    if args.validate_only:
        print(
            f"release notes validated: {rendered.audit['tag']} "
            f"({len(rendered.audit['prs'])} PRs, "
            f"{len(rendered.audit['direct_commits'])} direct commits)"
        )
        return 0

    assert args.output is not None
    assert audit_output is not None
    _atomic_write_text(args.output, rendered.markdown)
    _atomic_write_text(audit_output, _audit_text(rendered.audit))
    print(f"wrote release notes: {args.output}")
    print(f"wrote release audit: {audit_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
