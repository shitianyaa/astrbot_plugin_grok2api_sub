# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/) 规范。

## [Unreleased]

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
- 状态可见性：`StatusReport` 增加 `error_code` 字段，模型请求失败时显示"模型列表: 连接失败"而非 0 个模型。
- 搜索来源合并：遍历所有完成态 Web/X 搜索输出，累计来源，不再第一个 call 提前 return。
- 搜索来源展示：`max_search_sources=0` 时不再输出空的“来源”标题；FunctionTool 也遵守来源显示和数量配置。
- `HTTPTransport.close()` 不再 `except: pass`，关闭失败记录 `transport_close_failed` 日志。
- sender 日志不再直接用 `%s` 输出平台异常对象，改用 `safe_log` 只记录异常类型。

### Security

- 统一日志脱敏：新增 `core/observability.py`，`safe_log` 只接受白名单字段，所有值经 `sanitize_diagnostic` 清除 Client Key、代理 userinfo、Base64 和超长文本。
- 请求关联：每个命令/操作通过 `operation_scope` 建立 12 位随机 `trace_id`，通过 `ContextVar` 传播到 HTTP 日志。
- 日志中只记录已验证的相对路径，不记录完整 URL 或 Authorization 头。

### Changed

- 默认代理地址 `client_proxy_url` 从 `http://127.0.0.1:3067` 改为空字符串（用户不再需代理时手动清空）。
- `/g2帮助` 改为动态输出能力状态（可用/未配置，不泄露密钥或凭据）。
- 配置注入：`connect_timeout_seconds`、`search_timeout_seconds`、`image_timeout_seconds`、`video_create_timeout_seconds`、`video_poll_timeout_seconds`、`video_poll_interval_seconds`、`download_timeout_seconds`、`max_input_image_mb`、`model_retry_count`、`video_retry_count`、`retry_base_delay_seconds`、`retry_excluded_errors`、`debug_mode` 从 `PluginConfig` 完整注入 transport/client/media，不再硬编码。
- **远端重试分组**：搜索、生图、改图、模型目录和图片下载共用 `model_retry_count`；视频创建、状态轮询和视频下载使用 `video_retry_count`。两项均为不含首次请求的额外次数，默认 `2`。
- **默认重试范围**：空的 `retry_excluded_errors` 会重试远端 HTTP、网络、JSON 与远端响应结构错误，包含生成 POST；可用英文逗号排除 HTTP 状态码或稳定错误码。生成请求可能重复生成或扣费。
- **视频轮询超时**：新增 `video_poll_timeout_seconds` 作为每次状态查询的单次超时，移除 `video_max_wait_seconds` 的插件侧总等待上限；重试不再裁剪任何单次超时。
- **配置重构为 4 分组**：`_conf_schema.json` 顶层只有 `connection_settings`/`capability_settings`/`access_settings`/`advanced_settings` 四个 `object`；`api_base_url`/`client_proxy_url` 默认空字符串，未配置远端地址时插件可初始化但能力禁用。
- **多搜索模型**：`search_model` 单值改为 `search_models` 有序候选（英文逗号分隔、左侧优先、最多 12 个、保序去重、中文逗号拒绝）；默认 `grok-4.5,grok-4.3,grok-4.20-0309-reasoning,grok-4.20-0309-non-reasoning,grok-4.20-multi-agent-0309,grok-build-0.1,grok-chat-fast`。
- **搜索工具与思考强度**：默认同时开启 `web_search` 与 `x_search`；`grok-chat-*` 自动禁用 X 搜索并保留 Web 搜索。`search_reasoning_effort` 新增 `auto`（省略该字段、由远端选择），当前模型不支持所选强度时同样省略，不影响候选回退。
- **搜索路径边界**：`/g2搜索` 始终直接请求 grok2api 并强制当前全局启用的 Web/X 搜索，不经 AstrBot 主模型改写；`grok2api_web_search` 保持为由主模型决定调用的 Tool。
- **媒体进度**：`send_video_progress` 更名为 `send_media_progress`，覆盖生图、改图和视频；进度提示发送失败不取消已接受的任务。
- **可观测性**：媒体任务增加安全的开始/完成/失败日志；`debug_mode` 下 JSON HTTP 每次尝试记录相对路径、状态、耗时和重试性，网络失败使用状态 `0`。
- **模型目录缓存**：`GET /v1/models` 结果缓存 300 秒，并发刷新只发一个 GET，失败不返回过期目录。
- **严格搜索回退**：每个远端结果先按当前候选的重试策略处理；仅 `model_not_found`/`model_not_allowed`/`search_not_performed` 在重试耗尽后切换下一候选，其他错误立即抛出；耗尽抛 `search_models_exhausted`。
- **状态命令升级**：`/g2状态` 输出搜索候选顺序、当前可见/不可见候选、模型目录状态（只做目录 GET，不执行搜索探针）。
- **安全错误码**：transport 从错误体有界读取（64 KiB）只保留 `model_not_found`/`model_not_allowed`，其余用稳定映射，杜绝错误体泄漏。
- 未知命令异常不再 `logger.warning("命令异常: %s", exc)` 打印完整异常字符串，改为 `safe_log` 只记录 `exception_type`。

## [v0.1.0] - 2026-08-11

### Added

- 通过 grok2api Client Key 提供联网搜索、生图、改图、生视频。
- 双平台发送：OneBot/NapCat（aiocqhttp）与 QQ Official。
- 六个命令：`/g2搜索`、`/g2生图`、`/g2改图`、`/g2视频`、`/g2状态`、`/g2帮助`。
- 会话级注册的 `grok2api_web_search` FunctionTool，由 AstrBot 主模型决定是否调用。
- 配置集中校验、访问控制（黑白名单）、并发/大小限制、临时文件清理。
- HTTP 传输：同源相对路径、受控重试矩阵、`.part` 原子下载、代理支持。

### Security

- 只保存 Client Key；日志、状态页、错误消息均不泄露密钥或完整 Base64。
- 拒绝把上游提供的绝对 URL 作为鉴权请求目标，避免 Key 外泄。
- 图片输入归一化并防解压炸弹；输出媒体先落盘再由 AstrBot 发送。
