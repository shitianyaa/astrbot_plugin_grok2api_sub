"""Grok2API web search, image generation/editing and video tool for AstrBot.

The two most important rules enforced across this codebase:

1. **Never log or leak credentials.** The grok2api Client Key must never appear
   in logs, error messages, test output or the ``redacted_summary()`` report.
   Only a boolean ``client_key_configured`` flag is exposed.
2. **Auditable resolved media prompts.** When prompt processing is enabled and
   succeeds in ``extract`` or ``enhance`` mode, the final validated media
   request JSON is intentionally written to the local log for owner review.
   It must pass through ``sanitize_prompt_json`` and redact Client Keys,
   Bearer/JWT tokens, password/secret assignments, proxy userinfo and Base64.
   Direct ``off`` mode prompts and failed or unvalidated model output are not
   logged.
3. **Remote-call retries are explicit and configurable.** Search, image and
   image-edit work use the model retry group; video creation, polling and
   download use the video retry group. Generation requests may be replayed
   when the configured policy permits it, so do not add an implicit bypass
   for generation requests.

Validation commands (run from this repo root):

.. code-block:: powershell

    python -m json.tool _conf_schema.json
    python -m compileall main.py core tests
    python -m pytest -q
    ruff check .
    ruff format --check .

The parent ``D:\\Python\\QQBOT\\AGENTS.md`` still applies on top of this file.
No real credentials or full Base64 may be written to logs or tests. The
validated prompt JSON described above is the explicit local-audit exception;
tests must continue to use synthetic, non-private values.
"""
