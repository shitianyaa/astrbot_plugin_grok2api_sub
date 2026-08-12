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
- 模糊提交语义：生成 POST 的 5xx、无效 2xx JSON 不再映射为 `APIError` 或 `ProtocolError`，统一抛 `AmbiguousSubmissionError`，明确禁止自动重放。
- 视频清理：成功发送后也调用 `finalize_delivery`（`save_media=false` 时清理临时文件），不再仅异常路径清理。
- 媒体归档：`save_media=true` 时成功文件移入 `archive/` 子目录保留，`cleanup_expired` 跳过 `archive/`，保留文件不再被启动清理误删（此前文档承诺 archive/ 但代码未实现）。
- 会话并发 guard：即时检查同 UMO 锁占用，立即返回 `media_job_busy` 而非排队等待；空闲锁从字典回收。
- 访问控制：群聊中用户黑/白名单现在也生效（之前仅群聊忽略用户白名单）。
- 状态可见性：`StatusReport` 增加 `error_code` 字段，模型请求失败时显示"模型列表: 连接失败"而非 0 个模型。
- 搜索来源合并：遍历所有完成态 Web/X 搜索输出，累计来源，不再第一个 call 提前 return。
- `HTTPTransport.close()` 不再 `except: pass`，关闭失败记录 `transport_close_failed` 日志。
- sender 日志不再直接用 `%s` 输出平台异常对象，改用 `safe_log` 只记录异常类型。

### Security

- 统一日志脱敏：新增 `core/observability.py`，`safe_log` 只接受白名单字段，所有值经 `sanitize_diagnostic` 清除 Client Key、代理 userinfo、Base64 和超长文本。
- 请求关联：每个命令/操作通过 `operation_scope` 建立 12 位随机 `trace_id`，通过 `ContextVar` 传播到 HTTP 日志。
- 日志中只记录已验证的相对路径，不记录完整 URL 或 Authorization 头。

### Changed

- 默认代理地址 `client_proxy_url` 从 `http://127.0.0.1:3067` 改为空字符串（用户不再需代理时手动清空）。
- `/g2帮助` 改为动态输出能力状态（可用/未配置，不泄露密钥或凭据）。
- 配置注入：`connect_timeout_seconds`、`search_timeout_seconds`、`image_timeout_seconds`、`video_create_timeout_seconds`、`video_poll_interval_seconds`、`video_max_wait_seconds`、`download_timeout_seconds`、`max_input_image_mb`、`get_retry_attempts`、`retry_base_delay_seconds`、`debug_mode` 从 `PluginConfig` 完整注入 transport/client/media，不再硬编码。
- **配置重构为 4 分组**：`_conf_schema.json` 顶层只有 `connection_settings`/`capability_settings`/`access_settings`/`advanced_settings` 四个 `object`；`api_base_url`/`client_proxy_url` 默认空字符串，未配置远端地址时插件可初始化但能力禁用。
- **多搜索模型**：`search_model` 单值改为 `search_models` 有序候选（英文逗号分隔、左侧优先、最多 12 个、保序去重、中文逗号拒绝）；默认 `grok-4.5,grok-4.3,grok-4.20-0309-reasoning,grok-4.20-0309-non-reasoning,grok-4.20-multi-agent-0309,grok-build-0.1,grok-chat-fast`。
- **搜索工具与思考强度**：默认同时开启 `web_search` 与 `x_search`，新增可配置 `search_reasoning_effort`（默认 `high`）；当前模型不支持所选强度时省略该字段，不影响候选回退。
- **模型目录缓存**：`GET /v1/models` 结果缓存 300 秒，并发刷新只发一个 GET，失败不返回过期目录。
- **严格搜索回退**：仅 `model_not_found`/`model_not_allowed`/`search_not_performed` 切换下一候选；401/429/5xx/超时/模糊提交等立即抛出不切换；耗尽抛 `search_models_exhausted`。
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
- 生成类 POST 状态不确定时不自动重试（`AmbiguousSubmissionError`）。

### Security

- 只保存 Client Key；日志、状态页、错误消息均不泄露密钥或完整 Base64。
- 拒绝把上游提供的绝对 URL 作为鉴权请求目标，避免 Key 外泄。
- 图片输入归一化并防解压炸弹；输出媒体先落盘再由 AstrBot 发送。
