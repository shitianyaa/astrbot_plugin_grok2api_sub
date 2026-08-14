"""Prompt processing tests: direct, extraction and enhancement modes."""

from __future__ import annotations

import asyncio
import json
import logging
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
    disable_reference_processing: bool = False,
):
    return PluginConfig.from_astrbot(
        {
            "capability_settings": {
                "prompt_processing": {
                    "mode": mode,
                    "extract_provider_id": extract_provider,
                    "enhance_provider_id": enhance_provider,
                    "disable_prompt_processing_with_reference_image": disable_reference_processing,
                }
            },
            "advanced_settings": {"prompt_processing_timeout_seconds": 15},
        }
    )


class Context:
    def __init__(
        self,
        response=None,
        error: Exception | None = None,
        provider_id: str | None = "session-model",
        provider_error: Exception | None = None,
    ):
        self.response = response
        self.error = error
        self.provider_id = provider_id
        self.provider_error = provider_error
        self.calls: list[dict] = []
        self.provider_umos: list[str] = []

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response

    async def get_current_chat_provider_id(self, umo: str) -> str:
        self.provider_umos.append(umo)
        if self.provider_error is not None:
            raise self.provider_error
        assert self.provider_id is not None
        return self.provider_id


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


async def test_reference_image_disable_skips_video_prompt_processing():
    context = Context()
    processor = PromptProcessor(
        context,
        _config(mode="enhance", disable_reference_processing=True),
    )

    result = await processor.resolve_video(
        "让他跳舞", has_reference_image=True, reference_aspect_ratio="9:16"
    )

    assert result.prompt == "让他跳舞"
    assert result.duration == 6
    assert result.aspect_ratio == "9:16"
    assert result.resolution == "720p"
    assert context.calls == []


async def test_reference_image_disable_keeps_global_mode_without_a_reference_image():
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
    processor = PromptProcessor(context, _config(mode="enhance", disable_reference_processing=True))

    result = await processor.resolve_video("让他跳舞", has_reference_image=False)

    assert result.prompt == "cinematic dancer moving with clear full-body motion"
    assert context.calls[0]["chat_provider_id"] == "enhance"


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
        _config(mode="enhance"),
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


async def test_reference_image_disable_skips_image_edit_prompt_processing():
    context = Context()
    processor = PromptProcessor(
        context,
        _config(mode="enhance", disable_reference_processing=True),
    )

    result = await processor.resolve_image_edit("变红", has_reference_image=True)

    assert result == "变红"
    assert context.calls == []


async def test_reference_image_disable_does_not_require_enhance_provider():
    processor = PromptProcessor(
        Context(),
        _config(mode="enhance", enhance_provider="", disable_reference_processing=True),
    )

    result = await processor.resolve_video("让他跳舞", has_reference_image=True)

    assert result.prompt == "让他跳舞"


async def test_successful_processing_logs_request_audit_at_debug(monkeypatch):
    events = []
    monkeypatch.setattr(
        "core.prompt_processor.safe_log",
        lambda level, name, **fields: events.append((level, name, fields)),
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

    resolved = [fields for _level, name, fields in events if name == "prompt_processing_resolved"]
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
    levels = {name: level for level, name, _fields in events}
    assert levels["prompt_processing_started"] == logging.DEBUG
    assert levels["prompt_processing_completed"] == logging.DEBUG
    assert levels["prompt_processing_resolved"] == logging.DEBUG


async def test_reference_aspect_ratio_fills_processed_video_before_audit(monkeypatch):
    events = []
    monkeypatch.setattr(
        "core.prompt_processor.safe_log",
        lambda _level, name, **fields: events.append((name, fields)),
    )
    processor = PromptProcessor(
        Context(_assistant({"duration": 6, "aspect_ratio": None, "resolution": "720p"})),
        _config(mode="extract"),
    )

    request = await processor.resolve_video(
        "让他跳舞",
        has_reference_image=True,
        reference_aspect_ratio="9:16",
    )

    assert request.aspect_ratio == "9:16"
    resolved = [fields for name, fields in events if name == "prompt_processing_resolved"]
    assert resolved[0]["prompt_json"]["aspect_ratio"] == "9:16"


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


# ---------------------------------------------------------------------------
# Search result rewrite
# ---------------------------------------------------------------------------


def _search_answer(answer: str):
    return _assistant({"answer": answer})


async def test_rewrite_search_uses_session_provider_single_call_without_tools_or_history():
    context = Context(
        _search_answer("GitHub 组织所有者对组织仓库有管理员权限。"),
        provider_id="session-model",
    )
    processor = PromptProcessor(context, _config(mode="off"))

    answer = await processor.rewrite_search(
        "platform:group:123", question="组织所有者有什么权限？", search_text="检索正文"
    )

    assert answer == "GitHub 组织所有者对组织仓库有管理员权限。"
    assert context.provider_umos == ["platform:group:123"]
    assert context.calls[0]["chat_provider_id"] == "session-model"
    assert context.calls[0].get("tools") is None
    assert context.calls[0].get("contexts") is None
    assert json.loads(context.calls[0]["prompt"]) == {
        "question": "组织所有者有什么权限？",
        "search_text": "检索正文",
    }


async def test_rewrite_search_logs_model_and_lengths_without_raw_input(monkeypatch):
    events = []
    monkeypatch.setattr(
        "core.prompt_processor.safe_log",
        lambda _level, name, **fields: events.append((name, fields)),
    )
    processor = PromptProcessor(Context(_search_answer("简洁回答")), _config(mode="off"))

    await processor.rewrite_search("umo", question="私密问题", search_text="私密检索正文")

    expected_payload = json.dumps(
        {"question": "私密问题", "search_text": "私密检索正文"},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert events[0] == (
        "search_rewrite_started",
        {"operation": "search", "model": "session-model", "text_chars": len(expected_payload)},
    )
    assert events[1][0] == "search_rewrite_completed"
    assert events[1][1]["operation"] == "search"
    assert events[1][1]["model"] == "session-model"
    assert events[1][1]["text_chars"] == len("简洁回答")
    assert isinstance(events[1][1]["elapsed_ms"], int)
    assert "私密问题" not in str(events)
    assert "私密检索正文" not in str(events)


async def test_rewrite_search_strips_source_urls_from_outbound_payload():
    context = Context(_search_answer("简洁回答"))
    processor = PromptProcessor(context, _config(mode="off"))

    await processor.rewrite_search(
        "umo", question="q", search_text="结论 https://example.com/private-source 剩余正文"
    )

    payload = json.loads(context.calls[0]["prompt"])
    assert payload["question"] == "q"
    assert "example.com" not in payload["search_text"]
    assert "结论" in payload["search_text"]
    assert "剩余正文" in payload["search_text"]


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        _assistant({"answer": None}),
        _assistant({"answer": "组织所有者对仓库有管理员权限。", "extra": True}),
        _assistant({"answer": "   "}),
        _assistant({"answer": "含来源 https://example.com/private-source 的回答"}),
        SimpleNamespace(role="tool", completion_text="{}", tools_call_name=None),
        SimpleNamespace(role="assistant", completion_text="{}", tools_call_name="search"),
        SimpleNamespace(role="assistant", completion_text=""),  # empty output
    ],
)
async def test_rewrite_search_rejects_invalid_model_outputs(response):
    processor = PromptProcessor(Context(response), _config(mode="off"))

    with pytest.raises(PluginError) as caught:
        await processor.rewrite_search("umo", question="q", search_text="text")

    assert caught.value.code == "search_rewrite_invalid"


async def test_rewrite_search_timeout_maps_to_search_rewrite_timeout():
    processor = PromptProcessor(Context(error=asyncio.TimeoutError()), _config(mode="off"))

    with pytest.raises(PluginError) as caught:
        await processor.rewrite_search("umo", question="q", search_text="text")

    assert caught.value.code == "search_rewrite_timeout"


async def test_rewrite_search_provider_lookup_failure_maps_to_search_rewrite_provider_failed():
    processor = PromptProcessor(
        Context(provider_error=KeyError("no provider")),
        _config(mode="off"),
    )

    with pytest.raises(PluginError) as caught:
        await processor.rewrite_search("umo", question="q", search_text="text")

    assert caught.value.code == "search_rewrite_provider_failed"


async def test_rewrite_search_generate_error_maps_to_search_rewrite_provider_failed():
    processor = PromptProcessor(Context(error=RuntimeError("boom")), _config(mode="off"))

    with pytest.raises(PluginError) as caught:
        await processor.rewrite_search("umo", question="q", search_text="text")

    assert caught.value.code == "search_rewrite_provider_failed"


async def test_rewrite_search_cancellation_propagates():
    processor = PromptProcessor(Context(error=asyncio.CancelledError()), _config(mode="off"))

    with pytest.raises(asyncio.CancelledError):
        await processor.rewrite_search("umo", question="q", search_text="text")
