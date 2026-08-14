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
    *,
    mode: str = "off",
    extract_provider: str = "extract",
    enhance_provider: str = "enhance",
    force_reference_enhance: bool = False,
):
    return PluginConfig.from_astrbot(
        {
            "capability_settings": {
                "prompt_processing": {
                    "mode": mode,
                    "extract_provider_id": extract_provider,
                    "enhance_provider_id": enhance_provider,
                    "force_enhance_with_reference_image": force_reference_enhance,
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
        "reference_image_present": False,
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


async def test_reference_image_override_forces_video_enhance_and_hides_reference_content():
    context = Context(
        _assistant(
            {
                "prompt": "cinematic dancer moving with clear full-body motion",
                "duration": 6,
                "aspect_ratio": "9:16",
                "resolution": "720p",
            }
        )
    )
    processor = PromptProcessor(
        context,
        _config(mode="off", enhance_provider="reference-model", force_reference_enhance=True),
    )

    result = await processor.resolve_video("让他跳舞", has_reference_image=True)

    assert result.prompt == "cinematic dancer moving with clear full-body motion"
    assert context.calls[0]["chat_provider_id"] == "reference-model"
    assert json.loads(context.calls[0]["prompt"]) == {
        "media_type": "video",
        "source_prompt": "让他跳舞",
        "reference_image_present": True,
    }
    assert "https://" not in context.calls[0]["prompt"]
    assert "cannot see the reference image" in context.calls[0]["system_prompt"]


async def test_reference_image_override_keeps_global_mode_without_a_reference_image():
    context = Context()
    processor = PromptProcessor(context, _config(mode="off", force_reference_enhance=True))

    result = await processor.resolve_video("让他跳舞", has_reference_image=False)

    assert result.prompt == "让他跳舞"
    assert context.calls == []


async def test_reference_image_without_override_uses_global_extract_mode():
    context = Context(_assistant({"duration": 10, "aspect_ratio": "16:9", "resolution": "1080p"}))
    processor = PromptProcessor(context, _config(mode="extract"))

    result = await processor.resolve_video("让他跳舞", has_reference_image=True)

    assert result.prompt == "让他跳舞"
    assert result.duration == 10
    assert context.calls[0]["chat_provider_id"] == "extract"
    assert json.loads(context.calls[0]["prompt"])["reference_image_present"] is True
    assert "cannot see it" in context.calls[0]["system_prompt"]


async def test_image_edit_uses_dedicated_enhancement_schema_and_audit_operation(monkeypatch):
    events = []
    monkeypatch.setattr(
        "core.prompt_processor.safe_log",
        lambda _level, name, **fields: events.append((name, fields)),
    )
    context = Context(_assistant({"prompt": "apply a vivid red color treatment"}))
    processor = PromptProcessor(
        context,
        _config(mode="off", force_reference_enhance=True),
    )

    result = await processor.resolve_image_edit("变红", has_reference_image=True)

    assert result == "apply a vivid red color treatment"
    assert json.loads(context.calls[0]["prompt"]) == {
        "media_type": "image_edit",
        "source_prompt": "变红",
        "reference_image_present": True,
    }
    resolved = [fields for name, fields in events if name == "prompt_processing_resolved"]
    assert resolved == [
        {
            "operation": "image_edit",
            "prompt_mode": "enhance",
            "prompt_json": {"prompt": "apply a vivid red color treatment"},
        }
    ]


async def test_image_edit_extract_mode_keeps_original_prompt_without_provider_call():
    context = Context()
    processor = PromptProcessor(context, _config(mode="extract"))

    result = await processor.resolve_image_edit("变红", has_reference_image=True)

    assert result == "变红"
    assert context.calls == []


async def test_reference_image_override_requires_enhance_provider():
    processor = PromptProcessor(
        Context(),
        _config(mode="off", enhance_provider="", force_reference_enhance=True),
    )

    with pytest.raises(PluginError) as caught:
        await processor.resolve_video("让他跳舞", has_reference_image=True)

    assert caught.value.code == "prompt_processing_provider_missing"


async def test_successful_processing_logs_only_the_final_validated_request(monkeypatch):
    events = []
    monkeypatch.setattr(
        "core.prompt_processor.safe_log",
        lambda _level, name, **fields: events.append((name, fields)),
    )
    context = Context(
        _assistant(
            {
                "prompt": "bright cinematic city, wide composition",
                "duration": 10,
                "aspect_ratio": "16:9",
                "resolution": "1080p",
            }
        )
    )
    await PromptProcessor(context, _config(mode="enhance")).resolve_video("city")

    resolved = [fields for name, fields in events if name == "prompt_processing_resolved"]
    assert resolved == [
        {
            "operation": "video_generate",
            "prompt_mode": "enhance",
            "prompt_json": {
                "prompt": "bright cinematic city, wide composition",
                "duration": 10,
                "aspect_ratio": "16:9",
                "resolution": "1080p",
            },
        }
    ]


async def test_invalid_processing_output_never_logs_a_resolved_request(monkeypatch):
    events = []
    monkeypatch.setattr(
        "core.prompt_processor.safe_log",
        lambda _level, name, **fields: events.append((name, fields)),
    )
    processor = PromptProcessor(
        Context(_assistant({"aspect_ratio": "21:9", "resolution": "1k"})),
        _config(mode="extract"),
    )

    with pytest.raises(PluginError):
        await processor.resolve_image("cat")

    assert "prompt_processing_resolved" not in [name for name, _fields in events]


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
