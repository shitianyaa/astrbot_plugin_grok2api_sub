"""Media command handler fallback and validation contract tests."""

from __future__ import annotations

import pytest

from core.config import PluginConfig
from core.errors import PluginError
from core.handlers.help import HelpMixin
from core.handlers.media import MediaMixin


def _mixin(cfg: PluginConfig) -> MediaMixin:
    class _M(MediaMixin):
        pass

    mixin = _M()
    mixin._plugin_config = cfg
    mixin._service = None  # set per test
    mixin.sent: list[str] = []

    async def _send(_event, text: str) -> None:
        mixin.sent.append(text)

    mixin._send = _send
    return mixin


class StubEvent:
    def __init__(self) -> None:
        self.stopped = False

    def stop_event(self) -> None:
        self.stopped = True


def _fallback_cfg(
    *,
    fallback_enabled: bool = True,
    mode: str = "standard",
    character_research_mode: str = "auto",
) -> PluginConfig:
    return PluginConfig.from_astrbot(
        {
            "connection_settings": {"api_base_url": "https://h.com", "api_key": "k"},
            "capability_settings": {
                "search_models": "grok-4.5",
                "image_models": "grok-imagine-image",
                "image_edit_models": "grok-imagine-image",
                "video_models": "grok-imagine-video",
                "send_media_progress": False,
                "prompt_processing": {
                    "mode": mode,
                    "character_research_mode": character_research_mode,
                    "enhance_provider_id": "session-model",
                    "fallback_to_original_on_error": fallback_enabled,
                },
            },
        }
    )


async def test_image_processing_failure_retries_with_skip():
    calls: list[bool] = []

    class Svc:
        async def deliver_generated_images(
            self, event, prompt, *, skip_prompt_processing=False, **_kwargs
        ):
            calls.append(skip_prompt_processing)
            if not skip_prompt_processing:
                raise PluginError("处理失败", code="prompt_processing_provider_failed")

    mixin = _mixin(_fallback_cfg(fallback_enabled=True, mode="standard"))
    mixin._service = Svc()

    await mixin._handle_generate_image(StubEvent(), "猫")

    assert calls == [False, True]
    assert mixin.sent == []


async def test_image_processing_failure_reports_when_switch_off():
    class Svc:
        async def deliver_generated_images(
            self, event, prompt, *, skip_prompt_processing=False, **_kwargs
        ):
            raise PluginError("处理失败", code="prompt_processing_provider_failed")

    mixin = _mixin(_fallback_cfg(fallback_enabled=False, mode="standard"))
    mixin._service = Svc()

    await mixin._handle_generate_image(StubEvent(), "猫")

    # base.py 的 _ERROR_HINTS 映射（本计划不改动）：
    assert mixin.sent == ["智能改写提示词失败，请检查提示词改写模型的配置"]


async def test_image_processing_failure_not_fallback_when_mode_off():
    calls: list[bool] = []

    class Svc:
        async def deliver_generated_images(
            self, event, prompt, *, skip_prompt_processing=False, **_kwargs
        ):
            calls.append(skip_prompt_processing)
            raise PluginError("处理失败", code="prompt_processing_provider_failed")

    mixin = _mixin(_fallback_cfg(fallback_enabled=True, mode="off"))
    mixin._service = Svc()

    await mixin._handle_generate_image(StubEvent(), "猫")

    assert calls == [False]  # 只调用一次，且无 skip 短路
    assert mixin.sent == ["智能改写提示词失败，请检查提示词改写模型的配置"]


async def test_explicit_prompt_mode_failure_does_not_fallback():
    calls: list[bool] = []

    class Svc:
        async def deliver_generated_images(
            self, event, prompt, *, skip_prompt_processing=False, **_kwargs
        ):
            calls.append(skip_prompt_processing)
            raise PluginError("处理失败", code="prompt_processing_provider_failed")

    mixin = _mixin(_fallback_cfg(fallback_enabled=True, mode="standard"))
    mixin._service = Svc()

    await mixin._handle_generate_image(StubEvent(), "-eh 猫")

    assert calls == [False]  # 显式模式失败直接报错，不触发 skip 重试
    assert mixin.sent == ["智能改写提示词失败，请检查提示词改写模型的配置"]


async def test_generate_image_explicit_search_fails_without_fallback_when_prompt_model_fails():
    calls: list[bool] = []

    class Svc:
        async def deliver_generated_images(
            self, event, prompt, *, skip_prompt_processing=False, **_kwargs
        ):
            calls.append(skip_prompt_processing)
            raise PluginError("提示词处理模型响应超时", code="prompt_processing_timeout")

    mixin = _mixin(_fallback_cfg(fallback_enabled=True, mode="standard"))
    mixin._service = Svc()

    await mixin._handle_generate_image(StubEvent(), "-s a cute cat")

    assert calls == [False]
    assert mixin.sent == ["智能改写提示词超时，请重试"]


async def test_generate_image_rejects_search_flag_with_off_or_extract_mode():
    class Svc:
        async def deliver_generated_images(self, *args, **kwargs):
            pass

    # 1. explicit -s with -off
    mixin = _mixin(_fallback_cfg(mode="standard"))
    mixin._service = Svc()
    await mixin._handle_generate_image(StubEvent(), "-s -off 猫")
    assert any("-s/--search 只能与 -st、-eh 或 -ys 预设配合使用" in msg for msg in mixin.sent)

    # 2. explicit -s with -ex
    mixin.sent.clear()
    await mixin._handle_generate_image(StubEvent(), "-s -ex 猫")
    assert any("-s/--search 只能与 -st、-eh 或 -ys 预设配合使用" in msg for msg in mixin.sent)

    # 3. explicit -s with config mode "off" and no override
    mixin = _mixin(_fallback_cfg(mode="off"))
    mixin._service = Svc()
    mixin.sent.clear()
    await mixin._handle_generate_image(StubEvent(), "-s 猫")
    assert any("-s/--search 只能与 -st、-eh 或 -ys 预设配合使用" in msg for msg in mixin.sent)


async def test_generate_image_accepts_preset_flag_and_passes_to_service():
    calls: list[dict] = []

    class Svc:
        async def deliver_generated_images(self, event, prompt, **kwargs):
            calls.append({"prompt": prompt, **kwargs})

    mixin = _mixin(_fallback_cfg(mode="standard"))
    mixin._service = Svc()
    await mixin._handle_generate_image(StubEvent(), "画一只猫 -ys二次元 -s")

    assert len(calls) == 1
    assert calls[0]["prompt"] == "画一只猫"
    assert calls[0]["preset_name"] == "二次元"
    assert calls[0]["explicit_search"] is True


async def test_generate_image_rejects_search_flag_when_search_disabled_in_config():
    class Svc:
        async def deliver_generated_images(self, *args, **kwargs):
            pass

    mixin = _mixin(_fallback_cfg(mode="standard", character_research_mode="off"))
    mixin._service = Svc()
    await mixin._handle_generate_image(StubEvent(), "-s 猫")
    assert any("资料搜索已在插件配置中关闭，无法使用 -s/--search" in msg for msg in mixin.sent)


async def test_edit_image_disallows_prompt_processing_flags():
    class Svc:
        async def deliver_edited_image(self, *args, **kwargs):
            pass

    mixin = _mixin(_fallback_cfg())
    mixin._service = Svc()
    await mixin._handle_edit_image(StubEvent(), "-eh 变红")
    assert any("提示词处理和资料搜索参数仅支持 /g2生图" in msg for msg in mixin.sent)


async def test_edit_image_calls_service_with_verbatim_prompt():
    calls: list[str] = []

    class Svc:
        async def deliver_edited_image(self, event, prompt):
            calls.append(prompt)

    mixin = _mixin(_fallback_cfg())
    mixin._service = Svc()
    await mixin._handle_edit_image(StubEvent(), "变红 保持细节")
    assert calls == ["变红 保持细节"]


async def test_generate_video_disallows_prompt_processing_flags():
    class Svc:
        async def deliver_video(self, *args, **kwargs):
            pass

    mixin = _mixin(_fallback_cfg())
    mixin._service = Svc()
    await mixin._handle_generate_video(StubEvent(), "-s 纸飞机")
    assert any("提示词处理和资料搜索参数仅支持 /g2生图" in msg for msg in mixin.sent)


async def test_generate_video_calls_service_with_verbatim_prompt_and_image_url():
    calls: list[tuple[str, str]] = []

    class Svc:
        async def deliver_video(self, event, prompt, *, reference_image_url=""):
            calls.append((prompt, reference_image_url))

    mixin = _mixin(_fallback_cfg())
    mixin._service = Svc()
    await mixin._handle_generate_video(StubEvent(), "纸飞机 --image-url=https://e.com/i.png")
    assert calls == [("纸飞机", "https://e.com/i.png")]


@pytest.mark.parametrize(
    "arguments",
    ["-x 猫", "-xx 猫", "--ar 16:9 猫", "-en 猫", "-enp 猫", "-eh -zz 猫", "猫 -zz"],
)
async def test_generate_image_rejects_unrecognized_flags_without_calling_service(arguments):
    calls: list[str] = []

    class Svc:
        async def deliver_generated_images(self, event, prompt, **_kwargs):
            calls.append(prompt)

    mixin = _mixin(_fallback_cfg(mode="standard"))
    mixin._service = Svc()
    await mixin._handle_generate_image(StubEvent(), arguments)

    assert calls == []  # 未识别参数在任何远端请求前拦截
    assert len(mixin.sent) == 1
    assert "未识别的参数" in mixin.sent[0]
    assert "/g2生图 可用参数：-off、-ex、-st、-eh、-ys[预设名]、-s" in mixin.sent[0]


async def test_edit_image_rejects_unrecognized_flags_without_calling_service():
    calls: list[str] = []

    class Svc:
        async def deliver_edited_image(self, event, prompt):
            calls.append(prompt)

    mixin = _mixin(_fallback_cfg())
    mixin._service = Svc()
    await mixin._handle_edit_image(StubEvent(), "-zz 变红")

    assert calls == []
    assert any("/g2改图 不支持任何参数" in msg for msg in mixin.sent)


async def test_generate_video_rejects_unrecognized_flags_without_calling_service():
    calls: list[str] = []

    class Svc:
        async def deliver_video(self, event, prompt, *, reference_image_url=""):
            calls.append(prompt)

    mixin = _mixin(_fallback_cfg())
    mixin._service = Svc()
    await mixin._handle_generate_video(StubEvent(), "--ar 16:9 纸飞机")

    assert calls == []
    assert any("/g2视频 可用参数：--image-url" in msg for msg in mixin.sent)


async def test_generate_image_keeps_hyphenated_prompt_text():
    calls: list[str] = []

    class Svc:
        async def deliver_generated_images(self, event, prompt, **_kwargs):
            calls.append(prompt)

    mixin = _mixin(_fallback_cfg(mode="standard"))
    mixin._service = Svc()
    await mixin._handle_generate_image(StubEvent(), "画一只穿 T-shirt 的猫 -5 度雪景")

    assert calls == ["画一只穿 T-shirt 的猫 -5 度雪景"]
    assert mixin.sent == []


async def test_help_command_returns_updated_syntax_and_modes():
    class HelpTarget(HelpMixin):
        pass

    target = HelpTarget()
    target._plugin_config = _fallback_cfg()
    target.sent = []

    async def _send(_event, text: str) -> None:
        target.sent.append(text)

    target._send = _send

    event = StubEvent()
    await target._handle_help(event)

    assert event.stopped is True
    assert len(target.sent) == 1
    help_text = target.sent[0]

    assert "/g2生图 [-off|-ex|-st|-eh] [-ys<名称>] [-s] <提示词>" in help_text
    assert "-ehp" not in help_text
    assert "深度增强" not in help_text
    assert "风格预设(-ys<名称>)" in help_text


async def test_prompt_mode_conflict_preserves_dynamic_tokens():
    from core.errors import ConfigurationError

    mixin = _mixin(_fallback_cfg())

    exc = ConfigurationError(
        "提示词处理模式只能指定一个，检测到：-st -eh",
        code="prompt_mode_conflict",
    )
    await mixin._send_error(StubEvent(), exc, operation="image_generate")

    # 不再有 _ERROR_HINTS 条目，_send_error 回退到异常携带的动态消息（含冲突 token）。
    assert mixin.sent == ["提示词处理模式只能指定一个，检测到：-st -eh"]
