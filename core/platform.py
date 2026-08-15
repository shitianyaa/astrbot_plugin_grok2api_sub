"""Backward-compatibility bridge: re-exports from core.common.platform."""

import sys

from .common import platform as _m

globals().update({k: v for k, v in _m.__dict__.items() if not k.startswith("__")})
sys.modules[__name__] = _m
