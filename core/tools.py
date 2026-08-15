"""Backward-compatibility bridge: re-exports from core.search.tools."""

import sys

from .search import tools as _m

globals().update({k: v for k, v in _m.__dict__.items() if not k.startswith("__")})
sys.modules[__name__] = _m
