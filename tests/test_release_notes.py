from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from scripts.render_release_notes import ReleaseNotesError, main, render_release_notes

FIXTURES = Path(__file__).parent / "fixtures" / "release"
REPOSITORY = "fixture-owner/fixture-plugin"
TAG = "v0.1.4"
PREVIOUS_TAG = "v0.1.3"


def _render(
    *,
    prs: Path | None = None,
    commits: Path | None = None,
    history: Path | None = None,
    allow_unmapped_commits: bool = False,
):
    return render_release_notes(
        tag=TAG,
        previous_tag=PREVIOUS_TAG,
        repository=REPOSITORY,
        changelog_path=FIXTURES / "changelog.md",
        prs_json_path=prs or FIXTURES / "prs.json",
        commits_json_path=commits or FIXTURES / "commits.json",
        history_contributors_json_path=history or FIXTURES / "history-contributors.json",
        allow_unmapped_commits=allow_unmapped_commits,
    )


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _unmapped_commit() -> dict[str, object]:
    sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    return {
        "sha": sha,
        "url": f"https://github.com/{REPOSITORY}/commit/{sha}",
        "reason": "no_associated_pr",
        "subject": "do not expose token fixture-secret",
        "author_email": "nobody@example.invalid",
    }


def test_renders_bilingual_release_sources_and_contributors() -> None:
    rendered = _render()
    markdown = rendered.markdown

    expected_order_en = [
        "## Added",
        "## Changed",
        "## Fixed",
        "## Documentation",
        "## Maintenance",
    ]
    expected_order_zh = ["## 新增", "## 变更", "## 修复", "## 文档", "## 维护"]
    assert [markdown.index(heading) for heading in expected_order_en] == sorted(
        markdown.index(heading) for heading in expected_order_en
    )
    assert [markdown.index(heading) for heading in expected_order_zh] == sorted(
        markdown.index(heading) for heading in expected_order_zh
    )
    assert "## Removed" not in markdown
    assert "## Security" not in markdown
    assert "## 移除" not in markdown
    assert "## 安全" not in markdown

    pr_link = f"https://github.com/{REPOSITORY}/pull/12"
    issue_link = f"https://github.com/{REPOSITORY}/issues/102"
    commit_link = f"https://github.com/{REPOSITORY}/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    compare_link = f"https://github.com/{REPOSITORY}/compare/{PREVIOUS_TAG}...{TAG}"
    assert markdown.count(pr_link) >= 6
    assert markdown.count(issue_link) == 2
    assert markdown.count(commit_link) >= 6
    assert markdown.count(compare_link) == 2
    assert "## Breaking Changes" in markdown
    assert "## 破坏性变更" in markdown
    assert "## Upgrade Guide" in markdown
    assert "## 升级说明" in markdown

    assert "[@returning-user](https://github.com/returning-user)" in markdown
    assert "[@new-contributor](https://github.com/new-contributor)" in markdown
    assert "[@CoAuthor](https://github.com/CoAuthor)" in markdown
    assert "[@direct-maintainer](https://github.com/direct-maintainer)" in markdown
    assert "[@fixture-owner](https://github.com/fixture-owner)" in markdown
    assert "helper-bot[bot]" not in markdown
    assert "dependabot[bot]" not in markdown
    assert "SYNTHETIC_TOKEN_SHOULD_NOT_APPEAR" not in markdown
    assert "internal.example.invalid" not in markdown

    assert markdown.count("made their first contribution") == 2
    assert markdown.count("完成首次贡献") == 2
    assert "returning-user) made their first contribution" not in markdown
    assert "fixture-owner) made their first contribution" not in markdown

    english, chinese = markdown.split("\n---\n", maxsplit=1)
    source_pattern = re.compile(
        rf"https://github\.com/{re.escape(REPOSITORY)}/(?:pull|issues|commit)/[^)]+"
    )
    assert sorted(source_pattern.findall(english)) == sorted(source_pattern.findall(chinese))
    assert markdown.endswith("\n")
    assert "\r\n" not in markdown


def test_audit_includes_returning_new_coauthors_and_direct_commit_authors() -> None:
    audit = _render().audit

    contributors = [item["login"] for item in audit["contributors"]]
    new_contributors = [item["login"] for item in audit["new_contributors"]]
    assert contributors == [
        "CoAuthor",
        "direct-maintainer",
        "fixture-owner",
        "new-contributor",
        "returning-user",
    ]
    assert new_contributors == ["CoAuthor", "new-contributor"]
    assert audit["direct_commits"] == [
        {
            "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "url": (
                f"https://github.com/{REPOSITORY}/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            ),
            "category": "Maintenance",
            "breaking": False,
            "contributors": ["direct-maintainer", "returning-user"],
        }
    ]
    assert audit["unmapped_commits"] == []
    assert audit["warnings"] == []


def test_category_none_still_attributes_author_without_rendering_a_change(
    tmp_path: Path,
) -> None:
    payload = _load("prs.json")
    assert isinstance(payload, dict)
    none_pr = next(pr for pr in payload["prs"] if pr["number"] == 14)
    none_pr["author"] = {"login": "none-note-newcomer", "is_bot": False}
    prs = _write_json(tmp_path / "prs.json", payload)

    rendered = _render(prs=prs)

    assert "none-note-newcomer" in rendered.markdown
    assert "only refactor" not in rendered.markdown
    assert [item["login"] for item in rendered.audit["new_contributors"]] == [
        "CoAuthor",
        "new-contributor",
        "none-note-newcomer",
    ]


def test_output_is_order_independent_and_sensitive_fields_are_ignored(tmp_path: Path) -> None:
    expected = _render()
    prs_payload = _load("prs.json")
    commits_payload = _load("commits.json")
    history_payload = _load("history-contributors.json")
    assert isinstance(prs_payload, dict)
    assert isinstance(commits_payload, dict)
    assert isinstance(history_payload, list)
    prs_payload["prs"].reverse()
    for pr in prs_payload["prs"]:
        pr["coauthors"].reverse()
    commits_payload["direct_commits"].reverse()
    for commit in commits_payload["direct_commits"]:
        commit["authors"].reverse()
    history_payload.reverse()

    actual = _render(
        prs=_write_json(tmp_path / "prs.json", prs_payload),
        commits=_write_json(tmp_path / "commits.json", commits_payload),
        history=_write_json(tmp_path / "history.json", history_payload),
    )

    assert actual.markdown == expected.markdown
    assert actual.audit == expected.audit


def test_unmapped_direct_commit_fails_without_overwriting_markdown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commits_payload = {"direct_commits": [], "unmapped_commits": [_unmapped_commit()]}
    commits = _write_json(tmp_path / "commits.json", commits_payload)
    markdown = tmp_path / "release-notes.md"
    audit = tmp_path / "audit.json"
    markdown.write_text("keep existing\n", encoding="utf-8")

    exit_code = main(
        [
            "--tag",
            TAG,
            "--previous-tag",
            PREVIOUS_TAG,
            "--repository",
            REPOSITORY,
            "--changelog",
            str(FIXTURES / "changelog.md"),
            "--prs-json",
            str(FIXTURES / "prs.json"),
            "--commits-json",
            str(commits),
            "--history-contributors-json",
            str(FIXTURES / "history-contributors.json"),
            "--output",
            str(markdown),
            "--audit-output",
            str(audit),
        ]
    )

    assert exit_code == 1
    assert markdown.read_text(encoding="utf-8") == "keep existing\n"
    audit_payload = json.loads(audit.read_text(encoding="utf-8"))
    assert audit_payload["status"] == "failed"
    assert audit_payload["unmapped_commits"][0]["reason"] == "no_associated_pr"
    assert audit_payload["contributors"]
    assert "nobody" not in json.dumps(audit_payload)
    assert "unmapped_commits" in capsys.readouterr().err


def test_allow_unmapped_commit_renders_explicit_bilingual_warning(tmp_path: Path) -> None:
    commits = _write_json(
        tmp_path / "commits.json",
        {"direct_commits": [], "unmapped_commits": [_unmapped_commit()]},
    )

    rendered = _render(commits=commits, allow_unmapped_commits=True)

    assert "## Attribution Required" in rendered.markdown
    assert "## 需人工归因" in rendered.markdown
    assert "no associated pull request" in rendered.markdown
    assert "未关联 Pull Request" in rendered.markdown
    assert "fixture-secret" not in rendered.markdown
    assert "example.invalid" not in rendered.markdown
    assert rendered.audit["warnings"] == [
        "1 commit(s) require manual attribution; release override used"
    ]


def test_direct_commit_with_only_bot_authors_is_unmapped(tmp_path: Path) -> None:
    payload = _load("commits.json")
    assert isinstance(payload, dict)
    payload["direct_commits"][0]["authors"] = [{"login": "github-actions[bot]", "is_bot": True}]
    commits = _write_json(tmp_path / "commits.json", payload)

    with pytest.raises(ReleaseNotesError) as caught:
        _render(commits=commits)

    assert caught.value.audit is not None
    assert caught.value.audit["unmapped_commits"][0]["reason"] == ("missing_public_contributor")


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda note: note.__setitem__("category", "Unknown"),
            "invalid_category",
        ),
        (
            lambda note: note.__setitem__("summary_en", ""),
            "missing_bilingual_summary",
        ),
        (
            lambda note: note.__setitem__("migration_en", ""),
            "missing_bilingual_migration",
        ),
        (
            lambda note: note.__setitem__(
                "issue", "https://github.com/another/repository/issues/9"
            ),
            "repository_mismatch",
        ),
    ],
)
def test_rejects_invalid_release_note_metadata(tmp_path: Path, mutate, expected_code: str) -> None:
    payload = copy.deepcopy(_load("prs.json"))
    assert isinstance(payload, dict)
    target = next(pr for pr in payload["prs"] if pr["number"] == 12)
    mutate(target["release_note"])
    prs = _write_json(tmp_path / "prs.json", payload)

    with pytest.raises(ReleaseNotesError) as caught:
        _render(prs=prs)

    assert caught.value.issues[0].code == expected_code


def test_rejects_duplicate_pr_and_out_of_range_pr(tmp_path: Path) -> None:
    duplicate_payload = _load("prs.json")
    assert isinstance(duplicate_payload, dict)
    duplicate_payload["prs"].append(copy.deepcopy(duplicate_payload["prs"][0]))
    duplicate_path = _write_json(tmp_path / "duplicate.json", duplicate_payload)
    with pytest.raises(ReleaseNotesError) as duplicate:
        _render(prs=duplicate_path)
    assert duplicate.value.issues[0].code == "duplicate_pr"

    range_payload = _load("prs.json")
    assert isinstance(range_payload, dict)
    range_payload["prs"][0]["in_range"] = False
    range_path = _write_json(tmp_path / "range.json", range_payload)
    with pytest.raises(ReleaseNotesError) as out_of_range:
        _render(prs=range_path)
    assert out_of_range.value.issues[0].code == "source_out_of_range"


def test_cli_success_writes_markdown_audit_and_validate_only_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    markdown = tmp_path / "release-notes.md"
    audit = tmp_path / "audit.json"
    common_args = [
        "--tag",
        TAG,
        "--previous-tag",
        PREVIOUS_TAG,
        "--repository",
        REPOSITORY,
        "--changelog",
        str(FIXTURES / "changelog.md"),
        "--prs-json",
        str(FIXTURES / "prs.json"),
        "--commits-json",
        str(FIXTURES / "commits.json"),
        "--history-contributors-json",
        str(FIXTURES / "history-contributors.json"),
    ]

    assert main([*common_args, "--output", str(markdown), "--audit-output", str(audit)]) == 0
    assert markdown.read_bytes().endswith(b"\n")
    assert b"\r\n" not in markdown.read_bytes()
    assert json.loads(audit.read_text(encoding="utf-8"))["status"] == "ok"

    validate_markdown = tmp_path / "validate-only.md"
    validate_audit = tmp_path / "validate-only.json"
    assert (
        main(
            [
                *common_args,
                "--output",
                str(validate_markdown),
                "--audit-output",
                str(validate_audit),
                "--validate-only",
            ]
        )
        == 0
    )
    assert not validate_markdown.exists()
    assert not validate_audit.exists()
    assert "release notes validated" in capsys.readouterr().out
