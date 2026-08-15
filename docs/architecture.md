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

`/g2生图`、`/g2改图` 与 `/g2视频` 接收命令后的整段文本，不再将数字、时长或比例前缀当作命令参数。`PromptProcessor` 在服务层发起 grok2api 请求前解析模式：`off` 直接保留原提示词，`extract` 调用配置的 AstrBot 整理供应商且只接受媒体参数，`enhance` 调用独立的优化供应商并可替换提示词。改图没有可用媒体参数，因此全局 `extract` 保留其原提示词。

- 三套固定 system prompt 分别用于图片参数、视频参数和通用媒体优化；用户内容以 JSON 数据体传给 `Context.llm_generate()`，而不是插入 system prompt。带参考图的改图/视频仅额外传入 `reference_image_present` 布尔值，绝不传入图片、data URL、外链 URL 或签名 query。
- 返回内容必须是无多余字段的 JSON。比例、图片 `1k/2k`、视频 `6/10/15` 秒和 `480p/720p/1080p` 逐项白名单校验；模型异常、工具调用响应、超时或格式错误都会在 grok2api 生成请求前终止本次命令。
- `prompt_processing.disable_prompt_processing_with_reference_image=true` 时，检测到改图消息图片或视频消息图片/显式 URL 参考图会强制使用 `off`；因此不会调用文本模型，关闭时则完全遵循全局模式。
- 消息或回复中的视频参考图在 Pillow 校验和归一化时保留宽高；若处理器没有返回比例，服务层以固定白名单选择最近比例。显式 URL 保持不透明转发，不下载、不读取尺寸。
- `prompt_processor.py` 的内部处理过程均在 DEBUG 记录。用户启用 `extract` 或 `enhance` 且输出通过严格校验后，会额外写入一条本地 `prompt_processing_resolved`，包含实际发送的 `prompt` 与媒体参数 JSON，便于核对质量；自动填入的本地参考图比例会在该日志前合并。直传模式、原始输入、失败输出和 provider 标识不记录。该 JSON 会继续脱敏 API Key、Bearer/JWT、密码/secret 赋值、代理 userinfo 与 Base64。

## 管理面板安全域（`/g2面板`）

管理面与 API Key `/v1` 通道是两条互不重叠的通路：

```text
AstrBot WebUI 配置
  -> PluginConfig（admin_username / admin_password / panel_period / panel_sections）
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

## 多模型搜索回退矩阵

`capability_settings.search_models` 按多行、上方优先配置有序候选。每次搜索
都从配置第一项开始，**不**根据历史成功率/延迟/费用动态排序，**不**把成功模型
写回配置。

允许切换到下一候选的失败只有 3 类：

| 失败 | 错误码 | 行为 |
|---|---|---|
| 模型不在目录 | `not_visible` | 跳过，不发 POST |
| 搜索模型不存在 | `model_not_found` | 当前候选重试耗尽后切换到下一候选 |
| 无权使用该模型 | `model_not_allowed` | 当前候选重试耗尽后切换到下一候选 |
| 模型未执行联网搜索 | `search_not_performed` | 当前候选重试耗尽后切换到下一候选 |

远端 HTTP、网络、JSON、远端结构错误和 `search_not_performed` 都会先在当前候选上按
`model_retry_count` 重试。重试耗尽后，只有上表的三类候选级结果会继续下一候选；其他错误立即
向上抛出。`retry_excluded_errors` 只会跳过当前候选的重试，不会取消这三类候选回退。全部候选耗尽时
抛 `search_models_exhausted`。

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

- `send_media_progress` 默认开启。生图、改图、视频取得同会话任务锁后各发送一次进度提示；提示本身
  发送失败只记录安全日志，不取消已经接受的远端任务。
- 每个媒体任务使用多行块记录开始、完成或失败；开始块完整记录原始提示词与实际提示词，结束块记录最终模型、候选回退、远端重试、结果和耗时。日志不包含图片内容、参考图 URL、媒体 URL、请求 ID、上游响应正文或凭据。
- INFO 仅记录任务开始、汇总完成或失败；任务块包含原始/实际提示词、脱敏请求参数、最终模型、候选回退、远端重试和结果状态。通用命令包装、消息发送、模型选择、提示词处理过程、视频轮询、面板背景回退、面板渲染准备和每次 HTTP/管理面请求均仅在 DEBUG 记录。任务失败以 WARN 的最终块记录，包含稳定错误码与最终 HTTP 状态（有时）；不使用 `trace_id`。

## 远端重试边界

- `model_retry_count` 管理搜索、生图、改图、模型目录和图片下载；`video_retry_count` 管理视频创建、
  视频状态轮询和视频下载。两项均表示首次调用以外的额外次数，默认 `2`。
- `retry_excluded_errors` 为空时，远端 HTTP、网络、JSON 解析和远端响应结构错误均允许重试；
  可按 HTTP 状态码或稳定错误码关闭特定重试。`Retry-After` 仍优先于指数退避。
- 数字秒和 HTTP-date 两种 `Retry-After` 均受支持；HTTP-date 固定按 UTC 解释，最终退避上限为 30 秒。
- 生成 POST 也遵循此策略，因此可能产生重复生成或重复扣费。平台发送、访问控制、用户输入、媒体大小
  与路径安全错误均位于传输层之外，绝不自动重放。
- 每一次 HTTP 尝试只使用其操作自己的单次总超时。重试不裁剪单次超时，也不由视频轮询生命周期
  重新分配时间预算。
- 任务汇总在每个 HTTP 调用真正发出第 2 次及后续请求时累计一次重试；模型目录、生成、轮询和下载共享任务计数，独立的正常轮询请求均从首次尝试开始，不计为重试。

## 视频状态机

```text
create_video -> request_id -> wait_for_video (poll) -> done/failed
   -> download /v1/videos/{id}/content (.part, 原子改名) -> send_video
```

- 轮询支持 `pending/done/failed`，progress 限制到 0–100。
- 每次状态查询使用 `video_poll_timeout_seconds`；处于 `pending` 时按
  `video_poll_interval_seconds` 继续轮询，直到远端返回 `done` 或 `failed`。没有插件侧总等待上限。
- 完成后忽略响应里的绝对 `video.url` 主机，固定向配置的 base URL 请求 content。

## 清理生命周期

- 所有生成媒体落在 `StarTools.get_data_dir(plugin_name)/workspace`。
- `save_media=false` 时发送后删除（成功/失败都删）；`true` 时成功文件移到
  `workspace/archive/` 子目录保留，启动清理跳过 `archive/`。
- 启动时清理超过 `temp_retention_hours` 的 workspace 根目录临时文件和 `.part`。
- 路径通过 `Path.resolve().relative_to(root)` 校验，阻止越界删除。
