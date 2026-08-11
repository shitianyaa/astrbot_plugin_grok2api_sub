"""Grok2API web search, image generation/editing and video tool for AstrBot.

The two most important rules enforced across this codebase:

1. **Never log or leak credentials.** The grok2api Client Key must never appear
   in logs, error messages, test output or the ``redacted_summary()`` report.
   Only a boolean ``client_key_configured`` flag is exposed.
2. **Never auto-replay ambiguous generation.** Any image/video ``POST`` that
   fails in an indeterminate way (read timeout, connection reset, 5xx, or an
   invalid 2xx body) must raise ``AmbiguousSubmissionError`` and must NOT be
   retried, to avoid duplicate generation and duplicate billing.

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