# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/) 规范。

## [Unreleased]

## v0.3.0 (2026-08-19)

### Added

- **通用视觉事实与角色资料联网检索**：新增 `character_research_mode`（`off`/`auto`/`always`）与 `character_research_timeout_seconds`。生图与视频前自动通过联网搜索检索具名角色、IP、现实物品型号、建筑、载具等实体的视觉外观事实（发型、配色、服饰、标志性配饰），并将资料清洗后注入增强模型；无特定实体时返回 `NO_SPECIFIC_ENTITY` 自动平滑跳过。
- **显式搜索指令开关 (`-s`/`--search`)**：`/g2生图` 与 `/g2视频` 支持在命令任意位置附带 `-s` 或 `--search` 标记，显式触发针对提示词主体的联网视觉事实检索。改图命令默认跳过搜索，但在启用提示词增强时亦支持通过 `-s` 显式检索并注入外观资料。
- **任务级搜索请求预算控制 (`max_search_requests_per_task`)**：新增单次任务实际上游搜索请求次数限制（默认 3 次），按 `/v1/responses` 统一扣减，覆盖 `/g2搜索`、LLM 会话 Tool（`grok2api_web_search`）与提示词视觉资料搜索，支持 Agent 会话多轮工具调用继承已用预算，防止模型切换与重试过度消耗配额。
- **配置面板 8 大职责分组与平滑迁移**：将 AstrBot WebUI 插件配置按职责拆分为连接设置、图片与视频、提示词处理、联网搜索、访问控制、性能与可靠性、文件与缓存、管理面板 8 个分组；通过内置 `config_layout_version` 布局版本号实现旧版 4 分组配置无缝自动迁移，避免 AstrBot WebUI 回写丢失自定义项。

### Changed

- **提示词增强英文优化**：提示词 `enhance` 模式默认指导大模型输出更适配 Grok/Flux/SD 底模的地道英文 Prompt，同时强制保留用户指定的引号印刷文字字面量（如 `'不要忘记我'`）与所有否定排除词。
- **阶段超时优化与直观配置**：提示词处理默认超时提升至 60 秒（上限 300 秒），视觉资料检索默认超时提升至 120 秒（上限 600 秒），普通搜索提升至 300 秒（上限 900 秒）；任务总超时作为核心配置直观呈现，阶段超时折叠收敛。
- **全链路结构化可读任务日志**：插件加载、面板定时任务调度、生图/改图/视频/搜索与资料检索全面升级为格式化任务块日志，直观呈现有效处理模式、提示词状态（已增强/原文直传/回退原文）、搜索预算消耗（如 `1/3`）与任务耗时。

### Fixed

- **移除死板保真度拦截门禁**：彻底移除 `fidelity_check` 字符串严格字面量匹配拦截与 `prompt_processing_fidelity_failed` 报错打回，消除大模型英文扩写与正常意译被误判拦截的问题。
- **管理面板时间窗口测试修复**：修复管理面板单测中硬编码时间戳超出 7 天审计窗口导致的自然时间漂移测试偶发失败问题。

## v0.2.1 (2026-08-17)

### Added

- **媒体提示词处理失败自愈回退**：新增配置项 `capability_settings.prompt_processing.fallback_to_original_on_error`（默认开启）。在提示词整理或优化模式下，若改写模型发生网络超时、接口异常或格式错误（`prompt_processing_*`），自动按原始提示词与默认参数直发完成生图、改图和生视频任务。

### Changed

- **搜索路径直发搜索结果**：`/g2搜索` 移除调用文本模型二次改写的步骤，直接呈现 Grok 搜索 API 返回的原始正文与引用来源，降低响应延迟并节省大模型调用开销。
- **架构与冗余清理**：移除内部未使用的搜索改写逻辑与相关正则/Prompt，提示词处理体系更专注服务于媒体生成参数解析与优化。

## v0.2.0 (2026-08-15)

### Added

- **Release 提取工具**：新增 `scripts/extract_changelog.py`，支持在 GitHub Actions 触发 Release 时精准抽取版本发布说明。

### Changed

- **架构模块化分层**：将 `core/` 拆分为 `common/`（基础设施）、`search/`（搜索领域）、`media/`（媒体领域）、`panel/`（管理面板）、`handlers/`（指令混入层）清晰子包，规范 `GrokService` 门面与 `main.py` 混入 MRO 继承链。
- **配置与术语统一**：全局统一使用标准 `api_key` / `API Key`，精简 Schema 提示文案，默认改图模型对齐上游实际支持。
- **文档与 README 视觉升级**：重构 README 目录导航、QQ 交流群入口与纯净功能矩阵，全面汉化项目规范。
- **Release 工作流升级**：支持推送至 `main` 分支自动检测版本更新并触发 GitHub Release，自动注入对应版本的完整 Release Notes。

### Fixed

- **循环导入修复**：修复 `core/common/config.py` 引用兼容 shim 导致的 `core.panel_models` 独立导入失败与测试依赖执行顺序的问题。
- **清理冗余死代码**：移除 `core/media.py` 影子 shim，彻底清理未使用的 `SearchPipeline` 与 `MediaPipeline` 重复实现。
- **日志与测试规范化**：消除指令 Handler 中的日志魔数，精简非功能性测试与过度白盒接线测试，全套测试套件运行纯粹化。

### Removed

- **废弃脚本与过渡测试清理**：移除历史遗留的离线渲染脚本与冗余测试，收敛发布构建资产。

## v0.1.5 (2026-08-15)

### Changed

- 面板背景不再支持标签筛选；每次发送随机打乱 Wallhaven、LoliAPI 和 t.alcy 的尝试顺序，各站点均随机取图，单个图源失败后继续剩余图源。
- `panel_background_ready` 与各图源失败原因均在 DEBUG 记录，包含最终背景状态、具体图源和安全图片名，且不输出完整媒体 URL 或查询参数。
- 默认代理地址 `client_proxy_url` 从 `http://127.0.0.1:3067` 改为空字符串（用户不再需代理时手动清空）。
- `/g2帮助` 改为动态输出能力状态（可用/未配置，不泄露密钥或凭据）。
- 配置注入：`connect_timeout_seconds`、`search_timeout_seconds`、`image_timeout_seconds`、`video_create_timeout_seconds`、`video_poll_timeout_seconds`、`video_poll_interval_seconds`、`download_timeout_seconds`、`max_input_image_mb`、`model_retry_count`、`video_retry_count`、`retry_base_delay_seconds`、`model_switch_errors` 从 `PluginConfig` 完整注入 transport/client/media，不再硬编码。
- **远端重试分组**：搜索、生图、改图、模型目录和图片下载共用 `model_retry_count`；视频创建、状态轮询和视频下载使用 `video_retry_count`。两项均为不含首次请求的额外次数，默认 `2`。
- **默认重试范围**：远端 HTTP、网络、JSON 与远端响应结构错误默认都会重试，包含生成 POST；命中 `model_switch_errors` 的 HTTP 状态码或稳定错误码时，跳过当前模型的剩余重试并直接切换下一模型。生成请求可能重复生成或扣费。
- **视频轮询超时**：新增 `video_poll_timeout_seconds` 作为每次状态查询的单次超时，移除 `video_max_wait_seconds` 的插件侧总等待上限；重试不再裁剪任何单次超时。
- **配置重构为 4 分组**：`_conf_schema.json` 顶层只有 `connection_settings`/`capability_settings`/`access_settings`/`advanced_settings` 四个 `object`；`api_base_url`/`client_proxy_url` 默认空字符串，未配置远端地址时插件可初始化但能力禁用。
- **多搜索模型**：`search_model` 单值改为 `search_models` 有序候选（英文逗号分隔、左侧优先、最多 12 个、保序去重、中文逗号拒绝）；默认 `grok-4.5,grok-4.3,grok-4.20-0309-reasoning,grok-4.20-0309-non-reasoning,grok-4.20-multi-agent-0309,grok-build-0.1,grok-chat-fast`。
- **搜索工具与思考强度**：默认同时开启 `web_search` 与 `x_search`；`grok-chat-*` 自动禁用 X 搜索并保留 Web 搜索。`search_reasoning_effort` 新增 `auto`（省略该字段、由远端选择），当前模型不支持所选强度时同样省略，不影响候选回退。
- **搜索路径边界**：`/g2搜索` 始终直接请求 grok2api 并强制当前全局启用的 Web/X 搜索，不经 AstrBot 主模型改写；`grok2api_web_search` 保持为由主模型决定调用的 Tool。
- **媒体进度**：`send_video_progress` 更名为 `send_media_progress`，覆盖生图、改图和视频；进度提示发送失败不取消已接受的任务。
- **可观测性**：媒体、搜索、HTTP 请求和消息交付默认记录安全的开始/完成/失败日志；JSON HTTP 每次尝试记录相对路径、状态、耗时和重试性，网络失败使用状态 `0`。
- **模型目录缓存**：`GET /v1/models` 结果缓存 300 秒，并发刷新只发一个 GET，失败不返回过期目录。
- **严格搜索回退**：每个远端结果先按当前候选的重试策略处理；仅 `model_not_found`/`model_not_allowed`/`search_not_performed` 在重试耗尽后切换下一候选，其他错误立即抛出；耗尽抛 `search_models_exhausted`。
- **安全错误码**：transport 从错误体有界读取（64 KiB）只保留 `model_not_found`/`model_not_allowed`，其余用稳定映射，杜绝错误体泄漏。
- 未知命令异常不再 `logger.warning("命令异常: %s", exc)` 打印完整异常字符串，改为 `safe_log` 只记录 `exception_type`。
- 图片输入归一化并防解压炸弹；输出媒体先落盘再由 AstrBot 发送。

### Fixed

- 修复 GitHub Actions 中打包测试错误继承 runner `GITHUB_SHA`，导致 manifest 提交归因错误和 CI 失败的问题。
- 修复 Release workflow 对 `metadata.yaml` 中 `v` 前缀版本的比较，确保 `vX.Y.Z` tag 可以通过发布前校验。
- 修复成功空模型目录仍发送搜索请求、异常目录结构绕过重试，以及 HTTP-date `Retry-After` 按本地时区解释的问题。
- 任务日志的远端重试次数改为汇总模型目录、生成、轮询和下载实际发出的额外请求，正常视频轮询不再混入重试计数。

### Maintenance

- 简化普通 CI 为单一 Python 3.12 质量检查 job，并精简 Release workflow 为单 job 的验证、打包和发布流程。
- 移除 Dependabot 配置、重复构建和跨 job artifact 传递，Release Notes 改由 GitHub 自动生成。

## v0.1.4 (2026-08-14)

### Added

- **多源面板背景**：背景图按 Wallhaven（动漫、SFW、16:9）、LoliAPI 横屏和 t.alcy 横屏顺序获取；每个来源统一执行下载体积、图片解码和横向比例校验，全部失败时复用缓存或 CSS 默认背景。
- **任务日志参数**：任务块补充实际请求参数、搜索状态和结果摘要，内部 HTTP、面板子请求、轮询与模型尝试继续仅在 DEBUG 输出。

### Changed

- `panel_background_tags` 改为 Wallhaven 搜索关键词，每行一个；来源不保证排除 AI 图片。
- 统一任务日志移除 `trace_id`，开始和完成/失败块记录完整提示词、脱敏参数、实际模型和回退/重试统计。

## v0.1.3 (2026-08-14)

### Removed

- 移除仓库中遗留的 `admin_dashboard/` 临时管理面板源码、配置入口和生成物；插件内 `/g2面板` 功能不受影响。

### Changed

- 发布工作流不再保留对已删除 `admin_dashboard/` 目录的打包排除规则。

## v0.1.2 (2026-08-14)

### Added

- **参考图媒体工作流**：`/g2视频` 支持显式 `--image-url <HTTPS_URL>` 参考图，并优先于消息或回复图片；改图与视频均能感知消息参考图。视频使用本地消息/回复图片时，会在未指定比例时自动匹配最接近的受支持比例。
- **媒体模型候选回退**：生图、改图和视频模型均支持按配置顺序逐个尝试；模型候选配置统一为每行一个名称，不迁移旧逗号格式。

### Changed

- **参考图提示词处理**：新增 `prompt_processing.disable_prompt_processing_with_reference_image`。关闭时遵循全局模式；开启且存在参考图时，改图和视频原提示词直传，不调用提示词处理模型。显式 URL 不下载、不解析尺寸，也不传入提示词模型或日志。
- **运行日志**：普通 INFO 日志聚焦任务开始与最终结果；内部 HTTP、管理面请求、模型选择和轮询成功明细降为 DEBUG，失败与重试仍保留可观测性。
- **项目资料**：新增项目图标、面板主题与上游项目致谢，并同步 README 对参考图、配置和命令的说明。

## v0.1.1 (2026-08-14)

### Added

- **媒体提示词处理**：`/g2生图` 与 `/g2视频` 改为完整提示词直传，不再解析数量、时长或比例前缀；新增关闭、参数整理、提示词优化三种模式，以及独立的 AstrBot 整理/优化供应商选择器。图片支持 `1k`/`2k` 与七种比例；视频支持 `6s`/`10s`/`15s`、`480p`/`720p`/`1080p` 与七种比例。整理/优化模型输出严格校验，失败时终止请求而不静默降级。
- **`/g2面板` 管理面板（ADMIN）**：新增 `core/admin_client.py` 只读管理客户端（独立 aiohttp 会话、`asyncio.Lock` 保护的 token 轮换、401→refresh→单次重放、`connect_timeout_seconds` + 固定 30s 管理读超时），只允许账号摘要、图片/视频统计、审计摘要与审计列表五个 GET；管理请求按 `api_base_url` 的 scheme+authority 同源拼接，忽略 `/v1` 后缀。
- **面板配置**：`connection_settings` 增加 `admin_username`/`admin_password`（与 API Key 相互独立，仅面板使用，不入日志与 `redacted_summary`）；`advanced_settings` 增加 `panel_period`（`24h`/`7d`/`30d`/`90d`，默认 `7d`）与 `panel_sections`（五块中文多选，默认全选，可置空）。顶层仍为 4 个分组。
- **面板聚合与渲染**：`core/panel_models.py` 提供防御式 DTO（缺字段→0、未知 key 忽略）、`Decimal` 成本换算（`1e8 ticks = $1`）、本地按 `createdAt` 切窗的 `aggregate_models`；`core/panel_renderer.py` 输出纯文本，最多显示 20 个模型并标注省略/截断。`GrokService.build_panel` 走独立 `_panel_preflight()`（不复用 `_preflight`/`missing_capability`，因此**不要求 API Key**），完整报告缓存 60 秒，逐块顺序抓取、单块失败不阻断其余块。
- 审计逐条只保留 `createdAt`/`statusCode`/`errorCode`/`durationMs`/`totalTokens`/`modelPublicId`/`modelUpstreamModel`，游标分页上限 5000 行并显式标记截断；账号邮箱、API Key 名、请求 ID 与原始审计行不进入 `PanelReport`。
- **面板图片与定时推送**：`/g2面板` 现通过 AstrBot HTML-to-image 渲染 720p/1080p/1440p（默认 1080p）的 16:9 卡片，T2I 失败退回文本；新增 Lolicon 非 R18/排除 AI 横向背景、缓存回退、固定 UMO 模板列表、会话订阅命令、Cron 与从午夜对齐的间隔推送。同一 UMO 在同一分钟只发送一次，定时路径不调用主 LLM。

### Removed

- **破坏性变更：`/g2状态` 与别名 `/grok2状态` 已移除**，由 `/g2面板`（别名 `/grok2面板`）替代，不保留兼容别名。同时删除 `GrokService.status()` 与 `StatusReport`。

### Fixed

- 命令注册：handler 参数从 `*runtime_args: Any` 改为 `GreedyStr`，消除 `Any cannot be instantiated` 导入报错，确保多词参数正确合并。
- 包导入：删除 `sys.path.insert` 注入，改为 `__init__.py` + 包内相对导入，消除 13 个 `E402` lint 错误，支持 `data.plugins.xxx` 加载。
- 图片输入：`convert_to_base64` 改为 `await` 异步调用（AstrBot 4.26.6 的 `Image` 组件已是异步）。
- 图片大小限制：解码前先按 Base64 长度估算，解码后精确检查 `max_input_bytes`，超限在调用 API 前拒绝。
- 解压炸弹：不再永久修改 `Image.MAX_IMAGE_PIXELS` 进程级全局状态，改为 `try/finally` 恢复。
- TLS 关闭：`verify_tls=False` 不再使用 `ssl.create_default_context(False)`，改为 `aiohttp.TCPConnector(ssl=False)`。
- 视频清理：成功发送后也调用 `finalize_delivery`（`save_media=false` 时清理临时文件），不再仅异常路径清理。
- 媒体归档：`save_media=true` 时成功文件移入 `archive/` 子目录保留，`cleanup_expired` 跳过 `archive/`，保留文件不再被启动清理误删（此前文档承诺 archive/ 但代码未实现）。
- 会话并发 guard：即时检查同 UMO 锁占用，立即返回 `media_job_busy` 而非排队等待；空闲锁从字典回收。
- 访问控制：群聊中用户黑/白名单现在也生效（之前仅群聊忽略用户白名单）。
- 搜索来源合并：遍历所有完成态 Web/X 搜索输出，累计来源，不再第一个 call 提前 return。
- 搜索来源展示：`max_search_sources=0` 时不再输出空的"来源"标题；FunctionTool 也遵守来源显示和数量配置。
- `HTTPTransport.close()` 不再 `except: pass`，关闭失败记录 `transport_close_failed` 日志。
- sender 日志不再直接用 `%s` 输出平台异常对象，改用 `safe_log` 只记录异常类型。

### Security

- 统一日志脱敏：新增 `core/observability.py`，`safe_log` 只接受白名单字段，所有值经 `sanitize_diagnostic` 清除 API Key、代理 userinfo、Base64 和超长文本。
- 请求关联：每个命令/操作通过 `operation_scope` 建立 12 位随机 `trace_id`，通过 `ContextVar` 传播到 HTTP 日志。
- 日志中只记录已验证的相对路径，不记录完整 URL 或 Authorization 头。
