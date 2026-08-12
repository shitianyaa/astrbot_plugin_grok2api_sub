# Grok2API Sub Review Remediation Implementation Plan

> **For agentic workers:** Execute the tasks in order and keep each task independently reviewable. Do not commit or push unless the user explicitly authorizes it.

**Goal:** 修复当前审查确认的命令注册、媒体输入、HTTP、权限、清理和日志问题，使插件在 AstrBot 4.26.6 的 OneBot 与 QQ Official 平台上具备可发现、可诊断、可验证的行为。

**Architecture:** 保持 `main.py -> core/service.py -> client/transport/sender/media` 的现有边界。`main.py` 只负责 AstrBot 命令元数据、生命周期和错误出口；运行参数由 `PluginConfig` 注入客户端；日志通过一个安全的观测模块统一输出，不记录密钥、提示词、消息正文、用户/群 ID、完整 UMO、媒体 Base64 或带认证信息的代理 URL。

**Tech Stack:** Python 3.10+、AstrBot 4.26.6、aiohttp、Pillow、pytest、pytest-asyncio、ruff。

## Global Constraints

- 支持平台仍只声明 `aiocqhttp` 与 `qq_official`。
- 生成类 POST 在连接异常、读取超时、HTTP 5xx 或无效 2xx 响应时不得自动重放。
- GET/状态查询/媒体下载才允许按配置进行有限重试。
- QQ Official 单次图片发送上限保持 4 张；视频上限保持低于 200 MiB。
- 所有运行文件继续写入 `StarTools.get_data_dir(self.name)` 下的工作区。
- 不记录 Client Key、Authorization、Cookie、代理凭据、提示词、消息正文、用户/群 ID、完整 UMO、媒体 Base64 或上游原始响应体。
- 不增加新的生产依赖。
- 不修改版本号、提交、推送或发布，除非用户另行授权。

---

### Task 1: 修复插件导入方式与六个命令的 AstrBot 运行时契约

**Files:**
- Create: `__init__.py`
- Modify: `main.py:8-34,115-195`
- Replace: `tests/test_main_commands.py`
- Modify: `tests/test_main_contract.py`

**Interfaces:**
- 使用 `astrbot.core.star.filter.command.GreedyStr` 接收完整尾随文本。
- 搜索、生图、改图、视频命令分别接收一个 `GreedyStr`；状态和帮助命令不接收额外参数。
- 所有 handler 提供简短中文 docstring，作为 AstrBot 命令描述真源。

- [ ] **Step 1: 写真实命令注册失败测试**

  导入插件后从 `star_handlers_registry` 取六个 handler，断言参数模型如下，并通过 `validate_and_convert_params()` 验证多词参数不会拆散：

  ```python
  expected = {
      "main_g2_search": {"query": GreedyStr},
      "main_g2_generate_image": {"arguments": GreedyStr},
      "main_g2_edit_image": {"prompt": GreedyStr},
      "main_g2_generate_video": {"arguments": GreedyStr},
      "main_g2_status": {},
      "main_g2_help": {},
  }
  assert command_filter.handler_params == expected[handler.handler_full_name]
  assert command_filter.validate_and_convert_params(
      ["海边", "日落"], {"query": GreedyStr}
  ) == {"query": "海边 日落"}
  ```

- [ ] **Step 2: 运行测试并确认当前失败**

  Run: `python -m pytest tests/test_main_commands.py -q`

  Expected: 当前实现把 `runtime_args` 注册为参数，测试失败。

- [ ] **Step 3: 删除全局 `sys.path` 注入并改为包内相对导入**

  新增空的 `__init__.py`，删除 `_THIS_DIR` 与 `sys.path.insert()`，将导入统一为：

  ```python
  from astrbot.core.star.filter.command import GreedyStr

  from .core.client import Grok2APIClient
  from .core.command_parser import parse_image_command, parse_video_command, validate_search_query
  from .core.config import PluginConfig
  ```

  `_tool_allowed_for_event()` 内的延迟导入也改为 `.core.tools`。测试必须通过包路径导入插件，模拟 AstrBot 的 `data.plugins.<plugin>.main` 加载方式，不再直接 `import main`。

- [ ] **Step 4: 修正 handler 签名、参数来源和 docstring**

  使用以下签名，不再从 `event.get_message_str()` 重新读取包含命令名的完整文本：

  ```python
  async def g2_search(self, event: AstrMessageEvent, query: GreedyStr):
      """联网搜索：/g2搜索 <问题>，返回正文与来源。"""

  async def g2_generate_image(self, event: AstrMessageEvent, arguments: GreedyStr):
      """生成图片：/g2生图 [数量] <提示词>。"""

  async def g2_edit_image(self, event: AstrMessageEvent, prompt: GreedyStr):
      """编辑当前消息或回复中的首张图片：/g2改图 <编辑要求>。"""

  async def g2_generate_video(self, event: AstrMessageEvent, arguments: GreedyStr):
      """生成视频：/g2视频 [时长] [比例] <提示词>，可附带首帧图片。"""

  async def g2_status(self, event: AstrMessageEvent):
      """查看 Grok2API 配置与模型连通状态，仅 AstrBot 管理员可用。"""

  async def g2_help(self, event: AstrMessageEvent):
      """查看 Grok2API Sub 命令、参数、别名和当前能力状态。"""
  ```

  将 `_require_service()` 移入 handler 的 `try`，确保初始化失败也经过统一错误回复。

- [ ] **Step 5: 让 `/g2帮助` 展示真实能力状态**

  基于 `cfg.capability_enabled()` 为搜索、生图、改图、视频分别输出“可用/未配置”，同时保留参数、别名和管理员标识；不得输出模型权限、Key 或代理凭据。

- [ ] **Step 6: 运行命令契约与静态检查**

  Run:

  ```powershell
  python -m pytest tests/test_main_commands.py tests/test_main_contract.py -q
  ruff check main.py tests/test_main_commands.py tests/test_main_contract.py
  ```

  Expected: handler 参数、docstring、帮助动态能力和 `E402` 全部通过。

---

### Task 2: 建立安全、可配置的日志与请求关联机制

**Files:**
- Create: `core/observability.py`
- Create: `tests/test_observability.py`
- Modify: `main.py`
- Modify: `core/transport.py`
- Modify: `core/service.py`
- Modify: `core/sender.py`
- Modify: `core/media.py`
- Modify: `tests/test_transport.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- `operation_scope(operation: str, platform: str) -> ContextManager[str]` 创建 12 位随机 `trace_id`，通过 `ContextVar` 传播。
- `safe_log(level: int, event_name: str, **fields: object) -> None` 只接受允许字段并统一加 `[grok2api_sub]` 前缀。
- `sanitize_diagnostic(value: object) -> str` 清除 Client Key、Authorization、URL userinfo、Base64 和过长文本。

- [ ] **Step 1: 写脱敏与 debug 开关失败测试**

  覆盖以下断言：

  ```python
  secret = "g2a_prefix_supersecret"
  proxy = "http://alice:password@127.0.0.1:8080"
  payload = "data:image/png;base64," + "A" * 500
  output = sanitize_diagnostic(f"{secret} {proxy} {payload}")
  assert secret not in output
  assert "password" not in output
  assert "base64," not in output
  ```

  另测 `debug_mode=False` 时成功请求不输出请求明细；`debug_mode=True` 时输出 `operation/trace_id/method/path/status/elapsed_ms/attempt`，但不包含请求正文和认证头。

- [ ] **Step 2: 实现统一观测模块**

  允许字段固定为：

  ```python
  ALLOWED_FIELDS = {
      "operation", "trace_id", "platform", "method", "path", "attempt",
      "status", "elapsed_ms", "error_code", "retryable", "ambiguous",
      "request_id", "media_count", "bytes", "cleanup_count", "capability",
      "exception_type",
  }
  ```

  使用 `from astrbot.api import logger`，不要分别创建标准库 logger，也不要修改全局日志级别。未知字段直接拒绝或忽略，字段值先经 `sanitize_diagnostic()`。

- [ ] **Step 3: 定义日志等级和事件清单**

  默认模式只记录：

  - `INFO`: `plugin_initialized`、`plugin_terminated`、`search_tool_registered`、启动清理数量。
  - `WARNING`: 可恢复/预期失败，字段包含 `operation/error_code/status/retryable/ambiguous`。
  - `ERROR`: 初始化失败、关闭失败、无法发送错误提示。

  `debug_mode=True` 额外记录：

  - `command_started`、`command_completed`。
  - `http_request_completed`，包含 method、相对 path、attempt、status、elapsed_ms。
  - `video_created` 和轮询状态变化，包含安全的 request_id，不逐轮重复相同状态。
  - `media_downloaded`、`media_delivered`、`media_cleaned`，只记录数量和字节数，不记录本地绝对路径。

- [ ] **Step 4: 接入命令、Tool、HTTP 与媒体路径**

  每个命令和 LLM Tool 调用使用 `operation_scope()`；transport 从 ContextVar 读取 trace_id。HTTP 日志只记录已经验证的相对 `/v1/...` 路径，不记录完整 URL。`PluginError` 记录稳定 `code/status/retryable/ambiguous`；未知异常只记录异常类型，调试堆栈必须先整体脱敏，禁止直接 `logger.exception()` 输出外部异常字符串。

- [ ] **Step 5: 修复当前静默或不完整日志**

  - `initialize()` 未知异常记录脱敏后的异常类型与调试堆栈。
  - `HTTPTransport.close()` 不再 `except: pass`；关闭失败返回到生命周期层记录。
  - `PluginError` 也必须留下结构化失败记录，不能只给用户回消息。
  - sender 不再直接 `%s` 输出平台异常对象。
  - 工具注销 fallback 失败记录 `error_code=tool_unregister_failed`。

- [ ] **Step 6: 运行日志安全测试**

  Run:

  ```powershell
  python -m pytest tests/test_observability.py tests/test_transport.py tests/test_service.py -q
  ```

  Expected: 日志具备开关、耗时、状态码和关联 ID，敏感标记测试全部通过。

---

### Task 3: 将公开配置完整注入 HTTP、客户端和媒体工作区

**Files:**
- Modify: `main.py:51-66`
- Modify: `core/transport.py`
- Modify: `core/client.py`
- Modify: `core/media.py`
- Modify: `tests/test_config.py`
- Create: `tests/test_runtime_wiring.py`

**Interfaces:**
- `HTTPTransport(..., connect_timeout_seconds, debug_mode)`。
- `Grok2APIClient(..., search_timeout, image_timeout, video_create_timeout, video_poll_interval, video_max_wait, download_timeout, retry_policy)`。
- `MediaWorkspace(..., max_input_bytes, max_pixels)`。

- [ ] **Step 1: 写运行时接线失败测试**

  用非默认配置构造插件依赖，断言 transport/client/workspace 实际保存并使用 `17/181/301/121/7/901/302/4/1.25/13MiB`，避免测试只验证 `PluginConfig` 解析。

- [ ] **Step 2: 扩充构造参数并删除客户端硬编码**

  将 `core/client.py` 中 `180/300/120/30` 等固定值替换为构造参数；GET 重试统一使用：

  ```python
  RetryPolicy(
      operation=operation,
      attempts=config.get_retry_attempts,
      base_delay=config.retry_base_delay_seconds,
      allow_retry=True,
  )
  ```

  生成 POST 固定 `attempts=1, allow_retry=False`。

- [ ] **Step 3: 正确应用连接超时和 debug_mode**

  `aiohttp.ClientTimeout(total=operation_timeout, connect=connect_timeout_seconds)`；不得再用 `min(total, 10)`。将 debug_mode 传入 transport/observability，但不改变 AstrBot 全局 logger level。

- [ ] **Step 4: 运行配置与接线测试**

  Run: `python -m pytest tests/test_config.py tests/test_runtime_wiring.py tests/test_client_images.py tests/test_client_video.py -q`

---

### Task 4: 修复 AstrBot 图片输入、大小限制和图片输出验证

**Files:**
- Modify: `core/media.py:64-149`
- Modify: `core/service.py`
- Modify: `core/client.py`
- Modify: `tests/test_media.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- `image_component_to_data_url()` 直接 await AstrBot 的异步 `convert_to_base64()`。
- 在 Base64 解码前后执行 `max_input_bytes` 限制。
- 下载或解码出的结果图片必须经 Pillow 验证并按实际格式落盘。

- [ ] **Step 1: 使用真实 AstrBot `Image` 或 AsyncMock 写失败测试**

  ```python
  component.convert_to_base64 = AsyncMock(return_value=valid_data_url)
  result = await workspace.image_component_to_data_url(component)
  assert result.startswith(("data:image/png;base64,", "data:image/jpeg;base64,"))
  component.convert_to_base64.assert_awaited_once()
  ```

  另测 Base64 估算长度和解码后长度任一超过 `max_input_bytes` 都抛 `MediaLimitError(code="input_image_too_large")`。

- [ ] **Step 2: 修正异步调用与容量判断**

  `raw = await convert()`；仅把 Pillow 的 CPU 工作放入 `asyncio.to_thread()`。先根据 Base64 长度估算解码体积，再在解码后做精确检查。

- [ ] **Step 3: 避免修改 Pillow 进程级全局状态**

  不永久修改 `Image.MAX_IMAGE_PIXELS` 和 `ImageFile.LOAD_TRUNCATED_IMAGES`。若必须临时修改，保存旧值并在 `finally` 恢复；优先使用显式宽高乘积检查和 warning 捕获。

- [ ] **Step 4: 验证 URL 与 Base64 输出图片**

  下载先写 `.part`；Pillow 成功 `verify()` 后根据真实 MIME/format 原子改名为 `.png/.jpg/.webp`。HTML、空文件、损坏图片和扩展名伪装必须在平台发送前拒绝。

- [ ] **Step 5: 运行媒体测试**

  Run: `python -m pytest tests/test_media.py tests/test_service.py tests/test_client_images.py -q`

---

### Task 5: 修复 TLS 关闭和生成请求的状态不明语义

**Files:**
- Modify: `core/transport.py:109-119,141-197`
- Modify: `tests/test_transport.py`
- Modify: `tests/test_client_images.py`
- Modify: `tests/test_client_video.py`

**Interfaces:**
- `verify_tls=False` 使用 `aiohttp.TCPConnector(ssl=False)`。
- 非重试生成 POST 的网络异常、超时、5xx、无效 2xx JSON 都抛 `AmbiguousSubmissionError`。
- 明确 4xx 继续映射稳定 APIError，且调用次数始终为 1。

- [ ] **Step 1: 写 TLS 与模糊提交失败测试**

  修改现有错误预期：

  ```python
  with pytest.raises(AmbiguousSubmissionError):
      await generation_post_returning_503()
  with pytest.raises(AmbiguousSubmissionError):
      await generation_post_returning_invalid_json()
  assert len(session.calls) == 1
  ```

  另断言 `verify_tls=False` 创建 connector 不抛异常且 connector SSL 配置为关闭。

- [ ] **Step 2: 根据请求策略映射响应**

  当 `allow_retry=False`：

  - 401/403/404/429 等明确 4xx -> APIError。
  - 500-599 -> `AmbiguousSubmissionError(code="http_5xx_ambiguous")`。
  - 2xx 但 JSON 无效 -> `AmbiguousSubmissionError(code="invalid_2xx_ambiguous")`。
  - aiohttp/timeout -> `AmbiguousSubmissionError(code="network_ambiguous")`。

- [ ] **Step 3: 运行 transport/client 测试**

  Run: `python -m pytest tests/test_transport.py tests/test_client_images.py tests/test_client_video.py -q`

---

### Task 6: 修复视频成功清理与会话并发行为

**Files:**
- Modify: `core/service.py:64-68,178-212`
- Modify: `core/media.py:151-174`
- Modify: `tests/test_service.py`
- Modify: `tests/test_media.py`

**Interfaces:**
- 同一 UMO 已有媒体任务时立即抛 `PluginError(code="media_job_busy")`，不排队等待。
- `save_media=False` 时成功或失败都清理插件生成文件；`save_media=True` 时仅保留成功发送的文件。
- 空闲会话锁必须从字典回收。

- [ ] **Step 1: 写并发与清理失败测试**

  同时启动同 UMO 两个任务，第二个必须在 100ms 内返回 `media_job_busy`；不同 UMO 可并发。成功视频发送后断言文件不存在，`save_media=True` 时断言存在；失败发送后始终不存在。

- [ ] **Step 2: 用受控会话 guard 替换直接 `async with Lock`**

  guard 进入前检查占用并立即拒绝，在 `finally` 释放；仅在锁未占用且无等待者时删除字典条目。不要用 `wait_for(lock.acquire(), 0)` 这类竞态实现。

- [ ] **Step 3: 统一媒体 finalize 语义**

  将 `finalize_delivery(paths, success, save_media)` 作为唯一清理入口：

  ```python
  keep = success and save_media
  if not keep:
      unlink_generated_paths()
  ```

  图片、改图、视频都在 `finally` 调用，禁止只在异常路径清理。

- [ ] **Step 4: 运行并发与清理测试**

  Run: `python -m pytest tests/test_service.py tests/test_media.py -q`

---

### Task 7: 修复群聊用户白名单绕过并补全访问矩阵

**Files:**
- Modify: `core/access.py:45-64`
- Modify: `tests/test_access.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- 用户黑名单和用户白名单对私聊、群聊都生效。
- 群黑/白名单仅群聊生效。
- 黑名单优先于白名单；用户规则与群规则均需通过。

- [ ] **Step 1: 写完整访问矩阵测试**

  至少覆盖：群内用户不在 user whitelist、群在白名单但用户不在、用户在白名单但群不在、同时通过、任一黑名单命中、空白名单不限制。

- [ ] **Step 2: 调整判断顺序**

  固定顺序：user blacklist -> user whitelist -> group blacklist -> group whitelist -> allow。日志只记录 reason_code，不记录用户或群 ID。

- [ ] **Step 3: 运行访问控制测试**

  Run: `python -m pytest tests/test_access.py tests/test_service.py tests/test_tools.py -q`

---

### Task 8: 补强状态、搜索结果和运维错误可见性

**Files:**
- Modify: `core/service.py:214-235`
- Modify: `core/parsers.py:68-169`
- Modify: `main.py:160-181`
- Modify: `tests/test_parsers.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- `/g2状态` 区分“模型列表成功为空”和“请求失败”，不能把异常静默显示成 0 个模型。
- 搜索来源合并 annotations、全部 completed web_search_call sources 和顶层 citations，按 URL 保序去重。

- [ ] **Step 1: 写状态失败和来源合并测试**

  模型请求 401/网络失败时，状态报告包含安全的 `error_code`，管理员输出明确显示“连接失败”，而不是“可见模型数 0”。搜索测试包含多个 completed call 与顶层 citations，并断言全部合并去重。

- [ ] **Step 2: 实现状态错误字段与来源合并**

  扩充 `StatusReport` 的可选 `error_code`，不得放原始异常。解析器遍历所有 completed call，不在第一个 call 提前 return。

- [ ] **Step 3: 运行状态与解析测试**

  Run: `python -m pytest tests/test_parsers.py tests/test_service.py tests/test_main_commands.py -q`

---

### Task 9: 同步用户文档、测试文档和变更记录

**Files:**
- Modify: `README.md`
- Modify: `docs/commands.md`
- Modify: `docs/configuration.md`
- Modify: `docs/testing.md`
- Modify: `docs/architecture.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- README、运行时 docstring、`/g2帮助` 和 `docs/commands.md` 的六个命令说明必须一致。
- `debug_mode` 文档必须与实际日志字段、等级和脱敏规则一致。

- [ ] **Step 1: 更新命令文档**

  每条命令写明：语法、别名、权限、参数范围、是否需要附图/回复、平台限制、成功回复数量、失败语义。删除 `/g2帮助`“当前能力”与实际实现不一致的描述，或在 Task 1 实现后保留并写清状态来源。

- [ ] **Step 2: 更新日志文档**

  列出默认日志与 debug 日志事件；明确绝不记录的敏感字段；说明 `trace_id` 用于关联同一命令，不是上游凭据；说明生成状态不明时如何根据 trace_id/request_id 排查且不要盲目重试。

- [ ] **Step 3: 更新测试与 CHANGELOG**

  `docs/testing.md` 增加真实 AstrBot registry、异步 Image、日志脱敏、TLS、模糊提交、清理和访问矩阵测试。CHANGELOG 在 `Unreleased` 下按 Fixed/Security/Changed 记录，不提前修改版本号。

- [ ] **Step 4: 检查文档一致性**

  Run:

  ```powershell
  rg -n "g2搜索|g2生图|g2改图|g2视频|g2状态|g2帮助" README.md docs main.py
  rg -n "debug_mode|trace_id|request_id|Client Key|Base64" README.md docs _conf_schema.json core
  ```

---

### Task 10: 全量回归与双平台验收门禁

**Files:**
- Modify only if a preceding test exposes a confirmed defect.

- [ ] **Step 1: 运行全量自动检查**

  ```powershell
  python -m json.tool _conf_schema.json > $null
  python -m compileall -q main.py core tests
  python -m pytest -q
  ruff check .
  ruff format --check .
  git diff --check
  ```

  Expected: 全部退出码为 0，不允许用 noqa、跳过测试或降低校验来掩盖失败。

- [ ] **Step 2: 检查敏感信息与死配置**

  ```powershell
  rg -n "Authorization|client_api_key|g2a_|base64,|logger\.(debug|info|warning|error|exception)" main.py core tests
  rg -n --glob '!core/config.py' --glob '!tests/**' "connect_timeout_seconds|search_timeout_seconds|image_timeout_seconds|video_create_timeout_seconds|video_poll_interval_seconds|video_max_wait_seconds|download_timeout_seconds|max_input_image_mb|get_retry_attempts|retry_base_delay_seconds|debug_mode" .
  ```

  Expected: 没有日志输出密钥/正文/Base64；每项公开运行配置至少有一个生产代码消费点和一个行为测试。

- [ ] **Step 3: OneBot 手工验收**

  在 `aiocqhttp` 私聊与群聊各验证六个命令，重点检查多词提示词、附图/回复改图、单链多图、视频、本地文件清理、黑白名单和同会话并发拒绝。记录 AstrBot/NapCat 版本和结果，不记录消息正文或账号 ID。

- [ ] **Step 4: QQ Official 手工验收**

  在 `qq_official` 私聊与群聊验证单图逐条发送、最多 4 图、视频、一次进度消息、发送失败不重复投递、权限规则和媒体大小边界。记录平台返回码和 trace_id，不记录 AppID/AppSecret、用户 ID 或完整 UMO。

- [ ] **Step 5: 最终 diff 审核**

  检查未引入调试打印、缓存、媒体、真实日志、凭据、无关格式化或版本变更；确认 `Progress/` 未被加入插件提交范围，并将剩余未验证的真实平台风险写入进度记录。

## Required Review Gates

1. Task 1 完成后先复审命令 registry 与包加载，未通过不得继续。
2. Task 2 完成后用含假密钥、假代理凭据和假 Base64 的异常做日志泄漏审查。
3. Task 3-8 每项必须先看到对应测试在旧实现上失败，再实现并通过。
4. Task 10 自动检查全部通过后，才进入 OneBot 与 QQ Official 实机验收。
5. 未完成双平台实机验收时，不得把 `support_platforms` 的声明当作已验证结论。
