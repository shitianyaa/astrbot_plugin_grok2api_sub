# Search, Media Progress, and Observability Implementation Plan

**Goal:** Improve media job feedback and safe operational visibility without changing `/g2搜索` from a forced remote search command.

**Architecture:** Keep search transport and model selection unchanged. Replace the video-only progress setting with one media-progress policy consumed by image generation, image editing, and video generation. Keep the existing allow-listed observability API, but emit trace-correlated media lifecycle events and debug-only HTTP completion events. The LLM Tool remains a host-LLM tool; it returns a bounded, configuration-respecting source list to reduce unnecessary context.

**Constraints:** No new dependencies; no credentials, prompts, image data, or complete upstream URLs in logs; no retry changes for image/video POST; no new command; retain `/g2搜索` with `required=True`; preserve OneBot and QQ Official through existing `DeliveryAdapter` text/image/video paths.

## Completed Tasks

1. Replaced `send_video_progress` with `send_media_progress` in schema, immutable configuration, tests, and documentation. It emits one best-effort text notice only after a media session lock is acquired.
2. Added `operation_scope` and safe lifecycle logs for image generation, image editing, and video generation. Logs include only operation, model, media count, safe request ID, elapsed milliseconds, stable error code, and exception type.
3. Wired transport request-attempt logging through the existing safe logger in debug mode. It reports method, relative path, attempt, status, elapsed milliseconds, and retryability without headers, payloads, hostnames, or credentials.
4. Made Tool source output honor `show_search_sources` and `max_search_sources`; a zero source cap no longer renders an empty source heading.
5. Updated README, architecture/configuration/command docs, changelog, and regression coverage. Current verification: `282 passed`, JSON schema validation, Python compilation, `ruff check .`, `ruff format --check .`, and `git diff --check` all passed. No commit or push was made.
