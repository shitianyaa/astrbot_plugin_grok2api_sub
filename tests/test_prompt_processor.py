"""Prompt processing tests: direct, extraction and enhancement modes."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from core.config import PluginConfig
from core.errors import PluginError
from core.prompt_processor import PromptProcessor


def _config(
    *, mode: str = "off", extract_provider: str = "extract", enhance_provider: str = "enhance"
):
    return PluginConfig.from_astrbot(
        {
            "capability_settings": {
                "prompt_processing": {
                    "mode": mode,
                    "extract_provider_id": extract_provider,
                    "enhance_provider_id": enhance_provider,
                }
            },
            "advanced_settings": {"prompt_processing_timeout_seconds": 15},
        }
    )


class Context:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def _assistant(data: dict):
    return SimpleNamespace(role="assistant", completion_text=json.dumps(data), tools_call_name=None)


async def test_off_mode_uses_direct_defaults_without_calling_provider():
    context = Context()
    processor = PromptProcessor(context, _config())

    image = await processor.resolve_image("A red chair in a bright studio")
    video = await processor.resolve_video("A paper airplane over a city")

    assert image.prompt == "A red chair in a bright studio"
    assert image.aspect_ratio == ""
    assert image.resolution == "1k"
    assert video.prompt == "A paper airplane over a city"
    assert video.duration == 6
    assert video.aspect_ratio == ""
    assert video.resolution == "720p"
    assert context.calls == []


async def test_extract_image_preserves_prompt_and_reads_only_allowed_parameters():
    context = Context(_assistant({"aspect_ratio": "16:9", "resolution": "2k"}))
    processor = PromptProcessor(context, _config(mode="extract"))

    result = await processor.resolve_image("保留  空格 和 原文")

    assert result.prompt == "保留  空格 和 原文"
    assert result.aspect_ratio == "16:9"
    assert result.resolution == "2k"
    assert context.calls[0]["chat_provider_id"] == "extract"
    assert json.loads(context.calls[0]["prompt"]) == {
        "media_type": "image",
        "source_prompt": "保留  空格 和 原文",
    }


async def test_enhance_video_uses_selected_provider_and_returns_complete_request():
    context = Context(
        _assistant(
            {
                "prompt": "cinematic paper airplane gliding above a luminous city",
                "duration": 10,
                "aspect_ratio": "16:9",
                "resolution": "1080p",
            }
        )
    )
    processor = PromptProcessor(context, _config(mode="enhance", enhance_provider="fast-model"))

    result = await processor.resolve_video("纸飞机飞过城市")

    assert result.prompt.startswith("cinematic paper airplane")
    assert result.duration == 10
    assert result.aspect_ratio == "16:9"
    assert result.resolution == "1080p"
    assert context.calls[0]["chat_provider_id"] == "fast-model"


@pytest.mark.parametrize(
    "response",
    [
        _assistant({"aspect_ratio": "21:9", "resolution": "1k"}),
        _assistant({"aspect_ratio": None, "resolution": "1k", "extra": True}),
        SimpleNamespace(role="tool", completion_text="{}", tools_call_name=None),
        SimpleNamespace(role="assistant", completion_text="{}", tools_call_name="search"),
    ],
)
async def test_extract_rejects_invalid_model_outputs(response):
    processor = PromptProcessor(Context(response), _config(mode="extract"))

    with pytest.raises(PluginError) as caught:
        await processor.resolve_image("cat")

    assert caught.value.code == "prompt_processing_invalid"


async def test_provider_failure_or_timeout_does_not_fall_back_to_direct_generation():
    processor = PromptProcessor(Context(error=asyncio.TimeoutError()), _config(mode="extract"))

    with pytest.raises(PluginError) as caught:
        await processor.resolve_video("cat")

    assert caught.value.code == "prompt_processing_timeout"


async def test_extract_mode_requires_its_own_configured_provider():
    processor = PromptProcessor(Context(), _config(mode="extract", extract_provider=""))

    with pytest.raises(PluginError) as caught:
        await processor.resolve_image("cat")

    assert caught.value.code == "prompt_processing_provider_missing"


async def test_extract_rejects_unsupported_video_duration():
    context = Context(_assistant({"duration": 8, "aspect_ratio": "16:9", "resolution": "720p"}))
    processor = PromptProcessor(context, _config(mode="extract"))

    with pytest.raises(PluginError) as caught:
        await processor.resolve_video("8 seconds, wide shot")

    assert caught.value.code == "prompt_processing_invalid"
