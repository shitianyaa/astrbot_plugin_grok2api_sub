"""Backward-compatibility bridge: re-exports from core.common.prompt_processor."""

import sys

from .common import prompt_processor as _m

# Re-export all public and private attributes so monkeypatching core.prompt_processor works
globals().update({k: v for k, v in _m.__dict__.items() if not k.startswith("__")})
sys.modules[__name__] = _m
