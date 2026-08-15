from __future__ import annotations

import json

from scripts import collect_release_sources

REPOSITORY = "fixture-owner/fixture-plugin"
RELEASE_NOTE = {
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


def _commit(sha: str, login: str) -> dict[str, object]:
    return {
        "sha": sha,
        "author": {"login": login},
        "commit": {
            "author": {"date": "2026-08-15T00:00:00Z"},
            "committer": {"date": "2026-08-15T00:00:00Z"},
            "message": "Subject\n\nCo-authored-by: guessed-name <guess@example.invalid>",
        },
    }


def test_commit_authors_use_only_github_api_logins() -> None:
    authors = collect_release_sources._commit_authors(_commit("a" * 40, "api-author"))

    assert authors == [{"login": "api-author", "is_bot": False}]


def test_collect_merges_authors_across_commits_in_one_pr(monkeypatch) -> None:
    first = _commit("a" * 40, "first-author")
    second = _commit("b" * 40, "second-author")

    def fake_gh(endpoint: str, *_args: str) -> object:
        if endpoint == f"repos/{REPOSITORY}/commits/v0.1.3":
            return {"commit": {"committer": {"date": "2026-08-01T00:00:00Z"}}}
        if endpoint == f"repos/{REPOSITORY}/compare/v0.1.3...v0.1.4":
            return {"commits": [first, second]}
        if endpoint.endswith("/pulls"):
            return [{"number": 12}]
        if endpoint.endswith("/pulls/12"):
            return {
                "number": 12,
                "title": "Fix",
                "user": {"login": "pr-owner"},
                "merged_at": "2026-08-14T00:00:00Z",
                "body": f"<!-- release-note: {json.dumps(RELEASE_NOTE, ensure_ascii=False)} -->",
            }
        raise AssertionError(endpoint)

    monkeypatch.setattr(collect_release_sources, "_gh", fake_gh)
    monkeypatch.setattr(collect_release_sources, "_gh_lines", lambda *_args: [])

    prs, direct, history = collect_release_sources.collect(REPOSITORY, "v0.1.3", "v0.1.4")

    assert direct == {"direct_commits": [], "unmapped_commits": []}
    assert history == []
    assert [author["login"] for author in prs["prs"][0]["coauthors"]] == [
        "first-author",
        "second-author",
    ]
