"""Media command handler fallback contract tests."""

from __future__ import annotations

from core.config import PluginConfig
from core.errors import PluginError
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


def _fallback_cfg(*, fallback_enabled: bool, mode: str) -> PluginConfig:
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
                    "enhance_provider_id": "session-model",
                    "fallback_to_original_on_error": fallback_enabled,
                },
            },
        }
    )


async def test_image_processing_failure_retries_with_skip():
    calls: list[bool] = []

    class Svc:
        async def deliver_generated_images(self, event, prompt, *, skip_prompt_processing=False):
            calls.append(skip_prompt_processing)
            if not skip_prompt_processing:
                raise PluginError("处理失败", code="prompt_processing_provider_failed")

    mixin = _mixin(_fallback_cfg(fallback_enabled=True, mode="enhance"))
    mixin._service = Svc()

    await mixin._handle_generate_image(StubEvent(), "猫")

    assert calls == [False, True]
    assert mixin.sent == []


async def test_image_processing_failure_reports_when_switch_off():
    class Svc:
        async def deliver_generated_images(self, event, prompt, *, skip_prompt_processing=False):
            raise PluginError("处理失败", code="prompt_processing_provider_failed")

    mixin = _mixin(_fallback_cfg(fallback_enabled=False, mode="enhance"))
    mixin._service = Svc()

    await mixin._handle_generate_image(StubEvent(), "猫")

    # base.py 的 _ERROR_HINTS 映射（本计划不改动）：
    assert mixin.sent == ["智能改写提示词失败，请检查提示词改写模型的配置"]


async def test_image_processing_failure_not_fallback_when_mode_off():
    calls: list[bool] = []

    class Svc:
        async def deliver_generated_images(self, event, prompt, *, skip_prompt_processing=False):
            calls.append(skip_prompt_processing)
            raise PluginError("处理失败", code="prompt_processing_provider_failed")

    mixin = _mixin(_fallback_cfg(fallback_enabled=True, mode="off"))
    mixin._service = Svc()

    await mixin._handle_generate_image(StubEvent(), "猫")

    assert calls == [False]  # 只调用一次，且无 skip 短路
    assert mixin.sent == ["智能改写提示词失败，请检查提示词改写模型的配置"]


async def test_edit_image_processing_failure_retries_with_skip():
    calls: list[bool] = []

    class Svc:
        async def deliver_edited_image(self, event, prompt, *, skip_prompt_processing=False):
            calls.append(skip_prompt_processing)
            if not skip_prompt_processing:
                raise PluginError("处理失败", code="prompt_processing_timeout")

    mixin = _mixin(_fallback_cfg(fallback_enabled=True, mode="enhance"))
    mixin._service = Svc()

    await mixin._handle_edit_image(StubEvent(), "变红")

    assert calls == [False, True]
    assert mixin.sent == []


async def test_video_processing_failure_retries_with_skip_and_reference_image():
    calls: list[bool] = []

    class Svc:
        async def deliver_video(
            self, event, prompt, *, reference_image_url="", skip_prompt_processing=False
        ):
            calls.append(skip_prompt_processing)
            if not skip_prompt_processing:
                raise PluginError("处理失败", code="prompt_processing_invalid")

    mixin = _mixin(_fallback_cfg(fallback_enabled=True, mode="enhance"))
    mixin._service = Svc()

    await mixin._handle_generate_video(StubEvent(), "纸飞机 --image-url=https://e.com/i.png")

    assert calls == [False, True]
    assert mixin.sent == []


async def test_video_fallback_failure_path_reports_error():
    calls: list[bool] = []

    class Svc:
        async def deliver_video(
            self, event, prompt, *, reference_image_url="", skip_prompt_processing=False
        ):
            calls.append(skip_prompt_processing)
            if not skip_prompt_processing:
                raise PluginError("改写出错", code="prompt_processing_timeout")
            raise PluginError("生成失败", code="video_failed")

    mixin = _mixin(_fallback_cfg(fallback_enabled=True, mode="enhance"))
    mixin._service = Svc()

    await mixin._handle_generate_video(StubEvent(), "纸飞机")

    assert calls == [False, True]  # 重试确实发生
    assert "生成失败" in mixin.sent[0]  # 回退失败错误路径
