# Repository Development Rules

## Scope

This repository is an AstrBot plugin providing Grok2API search, image, image-edit, video, and panel features. Keep changes focused on the requested behavior and preserve unrelated user work.

## Safety

- Never read, print, commit, or hardcode `.env` values, Client Keys, tokens, passwords, cookies, JWTs, or private URLs.
- Do not place credentials, Bearer/JWT values, Base64 media, signed URLs, userinfo, media URLs, request IDs, or upstream response bodies in logs.
- Treat remote API responses and image URLs as untrusted. Validate schemes, redirects, image bytes, size, decoding, dimensions, and aspect ratio before use.

## Repository Workflow

- If `.codegraph/` exists, run `codegraph explore` before searching or reading source files.
- Keep factual task notes in `Progress/YYYY-MM-DD*.md`; never commit `Progress/`.
- Use `apply_patch` for manual edits. Do not use destructive Git commands or discard unrelated changes.
- Do not add dependencies without checking the existing stack and documenting the reason.

## Code Conventions

- Follow existing Python, aiohttp, AstrBot, and pytest patterns.
- Keep INFO logs concise task blocks; put transport, polling, model-attempt, panel subrequest, and delivery details at DEBUG.
- Preserve the retry contract: exhaust one model's retry group before fallback; only stable model-selection errors switch candidates.
- Preserve the media background fallback order and cache/CSS fallback behavior unless the task explicitly changes it.

## Version and Git

- Runtime, packaging, metadata, README badge, and CHANGELOG versions must agree.
- Do not change versions, commit, push, publish, or create releases unless the user explicitly authorizes it.
- Never add AI attribution trailers to commits or release text.
- Before staging, inspect `git diff` and stage only files belonging to the requested change.

## Validation

Run the relevant focused tests first, then:

```powershell
python -m json.tool _conf_schema.json
python -m compileall main.py core tests
python -m pytest -q
ruff check .
ruff format --check .
git diff --check
```

Report warnings, skipped checks, external-service limitations, and remaining risks explicitly.
