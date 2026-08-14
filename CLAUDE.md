# Project Instructions

Read `AGENTS.md` before changing this repository; it is the canonical source for safety, workflow, testing, logging, and Git rules.

AstrBot plugin changes must preserve platform routing, message delivery, configured proxy/TLS behavior, model retry/fallback semantics, and user-visible error contracts. Remote image sources are optional: try the configured source order, validate the result, and fall back to the local cache or CSS default when all sources fail.

Do not inspect or expose credentials from `.env` or local test fixtures. Do not log signed URLs, media URLs, request IDs, upstream bodies, or raw authentication material. Keep Progress notes untracked and run the repository validation commands before reporting completion.
