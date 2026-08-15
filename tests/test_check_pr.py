from __future__ import annotations

import json

from scripts.check_pr import check_pr_body

REPOSITORY = "fixture-owner/fixture-plugin"


def _body(**overrides: object) -> str:
    payload: dict[str, object] = {
        "category": "Fixed",
        "breaking": False,
        "summary_zh_cn": "修复",
        "summary_en": "Fix",
        "migration_zh_cn": "",
        "migration_en": "",
        "issue": "",
        "platforms": ["platform-neutral"],
        "none_reason": "",
    }
    payload.update(overrides)
    return f"<!-- release-note: {json.dumps(payload, ensure_ascii=False)} -->"


def test_accepts_same_repository_issue_url_and_platforms() -> None:
    result = check_pr_body(
        _body(issue=f"https://github.com/{REPOSITORY}/issues/12", platforms=["astrbot", "onebot"]),
        repository=REPOSITORY,
    )

    assert result.ok, result.errors


def test_rejects_missing_platforms_before_release() -> None:
    result = check_pr_body(_body(platforms=[]), repository=REPOSITORY)

    assert not result.ok
    assert "platforms must be a non-empty JSON array" in result.errors


def test_rejects_cross_repository_issue_url_before_release() -> None:
    result = check_pr_body(
        _body(issue="https://github.com/other-owner/other-repo/issues/12"),
        repository=REPOSITORY,
    )

    assert not result.ok
    assert "issue URL must belong to the current repository" in result.errors


def test_rejects_duplicate_platforms_case_insensitively() -> None:
    result = check_pr_body(_body(platforms=["OneBot", "onebot"]), repository=REPOSITORY)

    assert not result.ok
    assert "platforms[1] is duplicated" in result.errors
