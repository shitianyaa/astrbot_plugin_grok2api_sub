"""Grok2API web search, image generation/editing and video tool for AstrBot.

The two most important rules enforced across this codebase:

1. **Never log or leak credentials.** The grok2api Client Key must never appear
   in logs, error messages, test output or the ``redacted_summary()`` report.
   Only a boolean ``client_key_configured`` flag is exposed.
2. **Remote-call retries are explicit and configurable.** Search, image and
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
No real credentials, full Base64 or user private content may be written to
logs or tests.
"""
