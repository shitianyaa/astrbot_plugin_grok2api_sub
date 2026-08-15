#!/usr/bin/env python3
"""Collect public GitHub PR/commit metadata for the offline release renderer."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

RELEASE_NOTE_RE = re.compile(r"<!--\s*release-note:\s*(\{.*?\})\s*-->\s*", re.DOTALL)
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
BOT_RE = re.compile(r"(?:\[bot\]|^github-actions$)", re.IGNORECASE)


class CollectionError(RuntimeError):
    pass


def _gh(endpoint: str, *args: str) -> object:
    env = os.environ.copy()
    if not env.get("GH_TOKEN") and not env.get("GITHUB_TOKEN"):
        raise CollectionError("GH_TOKEN or GITHUB_TOKEN is required for GitHub API collection")
    result = subprocess.run(
        ["gh", "api", endpoint, *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise CollectionError(f"GitHub API request failed for {endpoint}: exit {result.returncode}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CollectionError(f"GitHub API returned invalid JSON for {endpoint}") from exc


def _gh_lines(endpoint: str, *args: str) -> list[str]:
    env = os.environ.copy()
    if not env.get("GH_TOKEN") and not env.get("GITHUB_TOKEN"):
        raise CollectionError("GH_TOKEN or GITHUB_TOKEN is required for GitHub API collection")
    result = subprocess.run(
        ["gh", "api", endpoint, *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise CollectionError(f"GitHub API request failed for {endpoint}: exit {result.returncode}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _login(user: object) -> str | None:
    if not isinstance(user, dict):
        return None
    login = user.get("login")
    return login if isinstance(login, str) and LOGIN_RE.fullmatch(login) else None


def _author(login: str | None) -> dict[str, object] | None:
    if login is None:
        return None
    return {"login": login, "is_bot": bool(BOT_RE.search(login))}


def _release_note(body: object) -> dict[str, object]:
    if not isinstance(body, str):
        raise CollectionError(
            "merged PR has no body; add the release-note JSON block before release"
        )
    matches = list(RELEASE_NOTE_RE.finditer(body))
    if len(matches) != 1:
        raise CollectionError("merged PR must contain exactly one release-note JSON block")
    try:
        value = json.loads(matches[0].group(1))
    except json.JSONDecodeError as exc:
        raise CollectionError("merged PR release-note block is invalid JSON") from exc
    if not isinstance(value, dict):
        raise CollectionError("merged PR release-note block must be a JSON object")
    return value


def _commit_authors(commit: dict[str, object]) -> list[dict[str, object]]:
    commit_data = commit.get("commit")
    if not isinstance(commit_data, dict):
        return []
    authors: dict[str, dict[str, object]] = {}
    for user in (commit.get("author"), commit.get("committer")):
        login = _login(user)
        if login is not None:
            authors[login.casefold()] = {"login": login, "is_bot": bool(BOT_RE.search(login))}
    return sorted(authors.values(), key=lambda item: str(item["login"]).casefold())


def _merge_authors(
    target: dict[str, dict[str, object]],
    authors: list[dict[str, object]],
    excluded_login: str | None,
) -> None:
    excluded_key = excluded_login.casefold() if excluded_login else None
    for author in authors:
        login = author.get("login")
        if not isinstance(login, str) or (excluded_key and login.casefold() == excluded_key):
            continue
        target[login.casefold()] = author


def collect(
    repo: str, previous_tag: str, tag: str
) -> tuple[dict[str, object], dict[str, object], list[str]]:
    previous_commit = _gh(f"repos/{repo}/commits/{previous_tag}")
    previous_data = previous_commit.get("commit") if isinstance(previous_commit, dict) else None
    previous_committer = previous_data.get("committer") if isinstance(previous_data, dict) else None
    previous_date = previous_committer.get("date") if isinstance(previous_committer, dict) else None
    if not isinstance(previous_date, str) or len(previous_date) < 10:
        raise CollectionError("unable to determine previous tag date")
    comparison = _gh(f"repos/{repo}/compare/{previous_tag}...{tag}")
    if not isinstance(comparison, dict) or not isinstance(comparison.get("commits"), list):
        raise CollectionError("compare API response has invalid commits")
    prs: dict[int, dict[str, object]] = {}
    pr_coauthors: dict[int, dict[str, dict[str, object]]] = {}
    direct: list[dict[str, object]] = []
    for commit in comparison["commits"]:
        if not isinstance(commit, dict):
            continue
        sha = commit.get("sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            continue
        associated = _gh(
            f"repos/{repo}/commits/{sha}/pulls", "-H", "Accept: application/vnd.github+json"
        )
        merged_numbers: list[int] = []
        for item in associated if isinstance(associated, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("number"), int):
                continue
            number = int(item["number"])
            full_pr = _gh(f"repos/{repo}/pulls/{number}")
            if not isinstance(full_pr, dict) or full_pr.get("merged_at") is None:
                continue
            merged_numbers.append(number)
            login = _login(full_pr.get("user"))
            if login is None:
                raise CollectionError(f"merged PR #{number} has no public GitHub login")
            if number not in prs:
                prs[number] = {
                    "number": number,
                    "url": f"https://github.com/{repo}/pull/{number}",
                    "title": str(full_pr.get("title") or ""),
                    "author": _author(login),
                    "coauthors": [],
                    "merged_at": full_pr["merged_at"],
                    "release_note": _release_note(full_pr.get("body")),
                }
                pr_coauthors[number] = {}
            _merge_authors(pr_coauthors[number], _commit_authors(commit), login)
        if not merged_numbers:
            commit_data = commit.get("commit") if isinstance(commit.get("commit"), dict) else {}
            author_data = commit_data.get("author") if isinstance(commit_data, dict) else {}
            direct.append(
                {
                    "sha": sha.lower(),
                    "url": f"https://github.com/{repo}/commit/{sha}",
                    "committed_at": author_data.get("date")
                    if isinstance(author_data, dict)
                    else None,
                    "associated_pr_numbers": [],
                    "authors": _commit_authors(commit),
                }
            )
    history = sorted(
        {
            login
            for login in _gh_lines(
                f"search/issues?q=repo:{repo}+is:pr+is:merged+merged:<{previous_date[:10]}",
                "--paginate",
                "--jq",
                ".items[].user.login",
            )
            if LOGIN_RE.fullmatch(login) and not BOT_RE.search(login)
        },
        key=str.casefold,
    )
    for number, authors in pr_coauthors.items():
        prs[number]["coauthors"] = sorted(
            authors.values(), key=lambda item: str(item["login"]).casefold()
        )
    return (
        {"prs": sorted(prs.values(), key=lambda item: item["number"])},
        {"direct_commits": direct, "unmapped_commits": []},
        history,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--previous-tag", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        prs, commits, history = collect(args.repository, args.previous_tag, args.tag)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, payload in (
            ("prs.json", prs),
            ("commits.json", commits),
            ("history-contributors.json", history),
        ):
            (args.output_dir / name).write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
    except CollectionError as exc:
        print(f"release source collection failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
