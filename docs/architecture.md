# 架构设计

## 模块分层与依赖架构

```text
main.py (Star 入口: 生命周期 / 命令装饰器 / LLM Tool 请求钩子)
  ├── core/handlers/ (指令混入层: HelpMixin, SearchMixin, MediaMixin, PanelMixin, BaseHandler)
  └── core/service.py (服务门面 GrokService)
        ├── core/search/ (搜索领域: SearchToolPolicy, Grok2APISearchTool, parsers, models)
        ├── core/media/ (媒体生成领域: MediaWorkspace, parser)
        ├── core/panel/ (管理面板领域: AdminClient, PanelReport, PanelSubscriptionStore, BackgroundProvider, card/renderer)
        └── core/common/ (公共基础设施: config, errors, access, transport, observability, platform, sender, prompt_processor, models)

assets/ (静态资源)
  └── fonts/ (文楷字体与开源授权)
```

- `main.py` 作为纯净的生命周期和指令调度入口，继承各业务 Mixin 与 `Star`。
- `core/handlers/` 拆分为独立领域模块 (`help.py`, `search.py`, `media.py`, `panel.py`)，指令直接路由到对应处理函数。
- `core/service.py` 统筹搜索、媒体生成、会话并发锁与管理面板采集。
- 业务层（`core/`）不直接调用 OneBot action，也不直接调用 QQ OpenAPI；所有业务 HTTP 经由 `transport.py`，所有发送经由 `sender.py`。例外仅 `admin_client.py`：它是管理面专用的独立只读客户端，见下节。

## 媒体提示词处理

提示词处理与视觉事实检索**严格仅服务于 `/g2生图`**。`/g2改图` 与 `/g2视频` 彻底绕过 `PromptProcessor`，将用户提示词与编辑要求原文直传至上游端点，若检测到提示词控制标记（`-off`、`-ex`、`-st`、`-eh`、`-ys`、`-s`、`--search`）会在远端调用前直接拦截拒绝。

- **文生图四档模式与自定义预设**：`PromptProcessor` 在发起生图请求前解析最终有效模式：
  - `off`：直接保留原提示词，不调用提示词模型与资料搜索。
  - `extract`：调用 `extract_provider_id`，仅提取图片比例（`1:1`、`16:9`、`9:16`、`4:3`、`3:4`、`3:2`、`2:3`）与分辨率（`1k`/`2k`），保留原始提示词。
  - `standard`、`enhance`：共用 `enhance_provider_id`，按模式对应的 System Prompt 执行忠实整理（20~45 词）或受控增强（45~80 词），输出适配底模的地道英文 Prompt。
  - `preset:<名称>`：调用在 WebUI 配置（`prompt_settings.presets`）中定义的专属 System Prompt 指令（通过 `-ys<名称>` 触发）。
- **三段式 System Prompt 架构**：提示词由「公共保真底座 `SHARED_LOSSLESS_RULES`（顶部） + 当前模式/预设专属指令（中间） + JSON 输出规范与标准示例 `_JSON_OUTPUT_SCHEMA`（底部）」组成；用户输入以结构化 JSON 数据体（`{"media_type":"image","source_prompt":...}`）传给 `Context.llm_generate()`，绝不与 System Prompt 混淆。有视觉资料时追加 `REFERENCE_RULES` 与 `character_reference` 字段。
- **严格 JSON 响应与白名单校验**：模型输出必须是无多余字段的合法 JSON，比例与分辨率经枚举白名单校验。
- **显式控制硬报错与默认模式自愈**：
  - 显式指定模式参数（如 `-eh`、`-ys二次元`）或显式搜索（`-s`）时，若改写模型超时/异常或搜索无资料，直接报错中止本次命令，绝不静默回退原文。
  - 仅在未指定命令标记、使用 WebUI 默认配置模式时，若改写模型异常且 `fallback_to_original_on_error=true`，才降级为原提示词直传继续生图。
- **改图与视频原文直传**：
  - `/g2改图`：直接将消息/回复图片与原始编辑文本发往 `/v1/images/edits`。
  - `/g2视频`：提示词原文直传，默认 `6s`、`720p`。消息/回复参考图经本地 Pillow 校验宽高比并对齐到最近合法比例；显式 `--image-url` 作为透明参数直传上游。
- **可观测性与脱敏**：`prompt_processor.py` 内部处理细节仅在 DEBUG 记录。INFO 日志中生图任务块明确呈现配置默认模式、请求覆盖模式、最终有效模式、提示词状态（已增强/原文直传/回退原文）与搜索状态；改图与视频日志明确呈现“原文直传”。日志严禁输出凭据、Base64 或上游原始正文。

## 管理面板安全域（`/g2面板`）

管理面与 API Key `/v1` 通道是两条互不重叠的通路：

```text
AstrBot WebUI 配置
  -> PluginConfig（panel_settings.admin_username / panel_settings.admin_password / panel_settings.panel_period / panel_settings.panel_sections）
  -> AdminClient（login -> 缓存 Bearer GET -> 401 refresh -> 单次重放）
  -> GrokService.build_panel()
  -> PanelReport（汇总字段 + 脱敏审计行为聚合）
  -> PanelBackgroundProvider（Wallhaven / LoliAPI / t.alcy / 缓存 / CSS 默认背景）
  -> panel/card.py HTML 模板
  -> Star.html_render()（AstrBot T2I）
  -> 受控 workspace 图片 -> MessageChain

T2I 失败：PanelReport -> format_panel_text() -> MessageChain 文本回复
```

- `HTTPTransport` 仍只允许 `/v1/...`，绝不承载管理路径；`AdminClient` 自建 aiohttp 会话，
  只允许账号摘要、图片/视频统计、审计摘要与审计列表五个 GET，管理路径按
  `api_base_url` 的 scheme + authority 同源拼接，忽略 `/v1` 后缀。
- Access token 只存进程内存；服务端提供 refresh token 时也仅存进程内存，由一把 `asyncio.Lock` 保护。
  401 优先触发一次 refresh；未提供或拒绝 refresh token 时重新登录一次，并只重放一次，再次 401 即命令级失败，不做循环。
- 超时用 `connect_timeout_seconds` + 固定 30s 管理读超时，与 `search_timeout_seconds` 无关。
- 审计汇总的请求数、Token、费用、计费状态、统计区间和计价元数据来自 summary 接口；审计行只保留时间、状态、模型、operation、provider、usageSource、流式、重试、工具和媒体等非身份字段，用于行为、UTC 调用趋势与模型聚合。趋势按 `24h=1h`、`7d=6h`、`30d=1d`、`90d=1w` 分桶，列表覆盖不足时保留 `X/Y` 覆盖提示，不伪造完整明细。
- 面板预检是独立的 `_panel_preflight()`，**不复用** `_preflight`/`missing_capability`
  （二者强制要求 API Key），因此只配管理凭据、不配 API Key 也能用 `/g2面板`。
- `PanelReport` 只保留聚合值；账号邮箱、API Key 名、请求 ID 与原始审计行在解析时即丢弃。
- **T2I 边界**：`panel/card.py` 只消费 `PanelReport`，不改动取数、鉴权与脱敏规则。图片
  调用经 `Star.html_render()` 使用 AstrBot 全局的 T2I 配置，插件不持有 T2I 主机或凭据，也不使用 Playwright。`panel_resolution` 将固定 1280x720 逻辑布局原生栅格化为 720p、1080p 或 1440p；渲染结果在发送前会验证为真实图片，错误 JSON 或 HTML 不会被当作图片发送。
- **定时投递**：`main.py` 使用 AstrBot `CronJobManager.add_basic_job()` 注册非持久化处理器；插件重载时重建处理器并清理同名前缀的遗留任务。`core/panel/scheduler.py` 的命令订阅与 Schema 固定目标合并后去重，Cron 与间隔触发在自然分钟维度再去重。

## 联网搜索的双层决策

1. AstrBot 主模型根据 `grok2api_web_search` 的 Tool 描述决定是否调用。
2. Tool 一旦被调用，默认同时传入 `web_search` 与 `x_search`；任一开关可单独关闭。
3. 至少存在一个搜索工具时，内部请求固定 `tool_choice: "required"`。
4. grok2api 响应中若没有完成态 `web_search_call` 或 `x_search_call`，返回 `SearchNotPerformedError`，
   不把普通模型回答伪装成联网结果。

`required` 的跨 Provider 行为并不完全相同：Build 可强制 hosted tool，Console 在
仅有 web search 时可能降级为 auto。因此模型字段允许填写 `Build/<model>` 锁定
Provider；无论选哪个 Provider，插件都以完成态 `web_search_call` 或 `x_search_call` 作为联网成功的
最终证据。

## 手动命令与 Tool 的边界

- `/g2搜索` 不经过 AstrBot 主模型。它直接向 grok2api 发送查询，固定要求远端执行至少一个
  当前全局启用的 Web/X 搜索工具，并将远端正文按来源展示配置发送给用户。
- `enable_web_search`、`enable_x_search`、`search_reasoning_effort` 是两条路径共享的全局设置；
  两个搜索开关都关闭时，命令和 Tool 都在发请求前拒绝。
- `grok2api_web_search` 是给 AstrBot 主模型选择的 FunctionTool。主模型决定是否调用；调用后 Tool
  同样强制远端搜索，但只返回受 `show_search_sources` 与 `max_search_sources` 限制的结果，最终用户回复仍由主模型组织。

## 多模型搜索与媒体回退矩阵

`search_settings.search_models`、`media_settings.image_models`、`media_settings.image_edit_models`、`media_settings.video_models` 按多行、上方优先配置有序候选。每次任务
都从配置第一项开始，**不**根据历史成功率/延迟/费用动态排序，**不**把成功模型
写回配置。

切换到下一候选的契约：

| 失败类型 | 典型错误码 / 场景 | 行为 |
|---|---|---|
| 模型不在目录 | `not_visible` | 跳过，不发 POST |
| 远端稳定错误 | `model_not_found`、`model_not_allowed`、`unsupported_model`、`search_not_performed` | 单次尝试失败立即切换至下一候选模型；遍历完候选进入下一轮 |
| 远端暂时/业务失败 | HTTP 4xx/5xx、网络中断、请求超时、JSON 无效、业务 error 块 | 单次尝试失败立即切换至下一候选模型；遍历完候选进入下一轮 |
| 视频任务终态失败 | `status=failed` | 按 `video_retry_count` 轮次重新创建新任务重试，单次失败切换下一候选 |
| 本地不可重试错误 | 输入校验失败、媒体大小超限、SSRF / 越界路径、未初始化、权限拒绝 | 立即终止本次任务，不切换下一候选 |
| 用户任务超时 | `task_timeout`（超过 `task_timeout_seconds`） | 立即终止重试与候选切换，清理工作区并安全退出 |

模型重试遵循 `model_retry_strategy` 配置：
- **`round_robin`（轮询重试，默认）**：单次请求失败时立即切换至下一个候选模型，遍历完所有候选后进入下一轮，总轮次数对齐 `1 + model_retry_count`（视频对齐 `1 + video_retry_count`）。
- **`sequential`（依次重试）**：在当前候选模型上重试自愈，耗尽 `1 + model_retry_count` 次后才切换至下一个候选模型。

本地不可重试错误与任务超时立即向上抛出。所有候选在全部轮次均失败时抛 `*_models_exhausted`。

注意事项：

- `/v1/models` 目录只证明**可见性**，不证明搜索能力；完成态 `web_search_call`
  或 `x_search_call` 才证明本次执行了联网搜索。
- 目录 GET 或 `data` 结构校验失败时回退到“原配置顺序”（不跳过 not_visible）；结构错误使用可重试的 `invalid_model_catalog`。
- 成功返回空目录时，所有候选均视为 not_visible，直接抛 `search_models_exhausted`，不发送搜索 POST。
- 实际搜索 POST 始终发送用户配置的原字符串（`Build/grok-4.5` 不会被重写为
  `grok-4.5`），Provider 前缀只用于目录可见性匹配。
- `grok-chat-*` 不支持 X 搜索：插件会在每个候选模型发起请求前移除 `x_search`，
  保留已启用的 Web 搜索；若因此没有任何可用工具则跳过该候选，不发送 Responses 请求。
- `search_reasoning_effort` 默认 `auto`，也可设为 `high`/`medium`/`low` 等。`auto`、不支持或未知的模型
  都省略 `reasoning.effort`，由远端选择或使用默认值，不以此触发模型回退。

## 平台发送边界

- OneBot/NapCat：图片可放入一个多图 `MessageChain`；视频用 `Video.fromFileSystem`。
- QQ Official：图片逐张独立发送，单次最多 4 张；不构造 `Node/Nodes`、合并转发、
  不调用 `/v2/groups/.../files` 或 `/v2/users/.../files`，不保存 QQ 官方凭据。
- 发送抛错后的交付状态可能不确定，插件不自动重发同一媒体。

## 媒体进度与日志

- `send_media_progress` 默认开启。生图、改图、视频取得单用户任务锁后各发送一次进度提示；提示本身
  发送失败只记录安全日志，不取消已经接受的远端任务。不同群友并发任务互不阻塞，全局受 `max_concurrent_media_jobs` 限制。
- 每个媒体任务使用多行块记录开始、完成或失败；开始块完整记录原始提示词与实际提示词，结束块记录最终模型、候选回退、远端重试、结果和耗时。日志不包含图片内容、参考图 URL、媒体 URL、请求 ID、上游响应正文或凭据。
- INFO 仅记录任务开始、汇总完成或失败；任务块包含原始/实际提示词、脱敏请求参数、最终模型、候选回退、远端重试和结果状态。通用命令包装、消息发送、模型选择、提示词处理过程、视频轮询、面板背景回退、面板渲染准备和每次 HTTP/管理面请求均仅在 DEBUG 记录。任务失败以 WARN 的最终块记录，包含稳定错误码与最终 HTTP 状态（有时）；不使用 `trace_id`。

## 远端重试与任务超时边界

- `task_timeout_seconds` 覆盖搜索、生图、改图和视频整个用户任务（默认 1800 秒）。服务入口使用 `asyncio.wait_for` 主动取消超时协程，并通过 ContextVar-backed `task_deadline_scope` 向传输层传递剩余预算；每次 HTTP 尝试与等待间隔都会按剩余时间裁剪。超时到达后统一抛出 `task_timeout` 终止。
- `model_retry_count` 与 `video_retry_count` 分别管理普通请求和视频请求的额外尝试次数；候选模型切换顺序由 `model_retry_strategy` 决定：`round_robin` 在候选间轮询，`sequential` 在当前候选耗尽后再切换。模型目录、图片/视频下载和视频状态轮询仍使用各自的重试组。默认值均为 `2`。
- 未命中 `model_switch_errors` 时，远端 HTTP、网络、JSON 解析和远端响应结构错误均允许进入下一候选/下一轮重试；
  命中配置的 HTTP 状态码或稳定错误码时跳过重试。`Retry-After` 仍优先于指数退避。
- 数字秒和 HTTP-date 两种 `Retry-After` 均受支持；HTTP-date 固定按 UTC 解释，最终退避上限为 30 秒。
- 生成 POST 也遵循此策略，因此可能产生重复生成或重复扣费。平台发送、访问控制、用户输入、媒体大小
  与路径安全错误均位于传输层之外，绝不自动重放。
- 任务汇总在每个 HTTP 调用真正发出第 2 次及后续请求时累计一次重试；模型目录、生成、轮询和下载共享任务计数，独立的正常轮询请求均从首次尝试开始，不计为重试。

## 视频状态机与任务重建

```text
[创建] create_video -> request_id -> wait_for_video (poll) ->
  ├── done: download /v1/videos/{id}/content (.part, 原子改名) -> send_video
  └── failed: 检查 model_switch_errors
        ├── 命中或重试耗尽 -> 切换下一候选视频模型
        └── 未超限 -> 重新发起 create_video 创建新任务并轮询新 request_id
```

- 轮询支持 `pending/done/failed`，progress 限制到 0–100。
- 每次状态查询使用 `video_poll_timeout_seconds` 并受全局 task deadline 裁剪；处于 `pending` 时按
  `video_poll_interval_seconds` 继续轮询，直到远端返回 `done` 或 `failed`。
- 收到 `status=failed` 时，不再重复轮询已失败的 `request_id`，而是重新创建新的视频任务。
- 完成后忽略响应里的绝对 `video.url` 主机，固定向配置的 base URL 请求 content。

## 清理生命周期

- 所有生成媒体落在 `StarTools.get_data_dir(plugin_name)/workspace`。
- `save_media=false` 时发送后删除（成功/失败都删）；`true` 时成功文件移到
  `workspace/archive/` 子目录保留，启动清理跳过 `archive/`。
- 启动时清理超过 `temp_retention_hours` 的 workspace 根目录临时文件和 `.part`。
- 路径通过 `Path.resolve().relative_to(root)` 校验，阻止越界删除。
