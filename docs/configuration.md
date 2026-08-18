# 配置

`_conf_schema.json` 是 WebUI 真源，`core/common/config.py` 在启动时把配置解析为不可变
`PluginConfig`。运行时代码不应散落 `config.get()`。

当前面板按职责拆成 8 个可见分组：`connection_settings`、`media_settings`、
`prompt_settings`、`search_settings`、`access_settings`、`performance_settings`、
`storage_settings`、`panel_settings`。旧的 `capability_settings` 与 `advanced_settings`
仍保留在 Schema 中但默认隐藏，只用于兼容已有配置。插件首次读取旧布局时会把自定义值
迁移到新分组，并写入隐藏的 `connection_settings.config_layout_version`；以后以新分组为准。

## 基础连接（`connection_settings`）

| 配置键 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `enabled` | bool | `true` | 插件总开关；关闭后命令和搜索 Tool 均不可用 |
| `api_base_url` | string | `""` | grok2api 根地址，只允许 http/https，不要附加 `/v1`；留空时媒体与搜索能力不可用 |
| `api_key` | string | `""` | grok2api API Key，禁止写入日志 |
| `verify_tls` | bool | `true` | 是否校验证书，生产环境保持开启 |
| `client_proxy_url` | string | `""` | 出站 HTTP(S) 代理，留空为直连；允许认证但日志只显示协议/主机/端口 |

管理面凭据已移动到 `panel_settings.admin_username` 与 `panel_settings.admin_password`，旧的
连接分组字段仅作为隐藏迁移源，不应再填写。

## 图片与视频（`media_settings`）

| 配置键 | 默认值 | 说明 |
|---|---:|---|
| `image_models` | 多行列表 | 文生图候选模型，上方优先；留空禁用生图 |
| `image_edit_models` | 多行列表 | 改图候选模型，上方优先；默认不含 `lite` |
| `video_models` | 多行列表 | 生视频候选模型，上方优先 |
| `image_response_format` | `b64_json` | `b64_json` 或 `url`，两者都会落盘校验后发送 |
| `send_media_progress` | `true` | 是否在媒体任务开始时发送一次进度提示 |

模型列表每行一个，最多 12 个，按首次出现顺序去重；不要使用英文或中文逗号分隔。

## 提示词处理（`prompt_settings`）

| 配置键 | 默认值 | 说明 |
|---|---:|---|
| `mode` | `off` | `off` 原文直传；`extract` 只补全媒体参数；`enhance` 执行严格无损优化 |
| `extract_provider_id` | `""` | 整理模型，使用 AstrBot 原生供应商选择器 |
| `enhance_provider_id` | `""` | 优化模型，使用 AstrBot 原生供应商选择器 |
| `character_research_mode` | `off` | `off` 不搜索；`auto` 识别具名角色后搜索；`always` 每次生图/视频都尝试搜索 |
| `disable_prompt_processing_with_reference_image` | `false` | 有参考图时强制原提示词直传，不调用提示词模型 |
| `fallback_to_original_on_error` | `true` | 处理失败时使用原提示词继续媒体任务；关闭则中止 |

角色资料搜索只在 `enhance` 模式生效；改图或带参考图视频会跳过角色资料检索。搜索结果
只是事实参考，用户输入保持最高优先级；角色搜索失败或超时会软回退普通优化。处理成功且
通过字段校验后才会在 DEBUG 记录脱敏的最终请求 JSON。

## 联网搜索（`search_settings`）

| 配置键 | 默认值 | 说明 |
|---|---:|---|
| `search_models` | 多行列表 | 搜索候选模型，上方优先；留空禁用搜索 |
| `enable_web_search` | `true` | 启用 Web 搜索工具 |
| `enable_x_search` | `true` | 启用 X 搜索工具；chat 模型不支持时自动降级 |
| `search_reasoning_effort` | `auto` | `auto`、`none`、`low`、`medium`、`high`、`xhigh`；默认 `auto` |
| `enable_llm_search_tool` | `true` | 向 AstrBot 主模型注册会话级搜索 Tool |
| `show_search_sources` | `true` | 手动命令和 Tool 是否返回结构化来源 |
| `max_search_sources` | `5` | 来源数量上限，`0` 表示不显示来源 |
| `max_search_output_chars` | `6000` | 搜索正文 Unicode 字符上限，超出后截断 |

搜索开关和来源限制属于输出策略；搜索请求是否能完成还受下面的阶段超时和任务总超时影响。

## 性能与可靠性（`performance_settings`）

普通情况下只需要调整 `timeouts.task_timeout_seconds`。其他阶段超时和重试位于同一分组的
专家设置中，并在 WebUI 中折叠展示。

### 超时（`performance_settings.timeouts`）

| 配置键 | 默认值 | 作用 |
|---|---:|---|
| `task_timeout_seconds` | `1800` | 单次任务总预算，包含排队、提示词处理、重试、候选回退、轮询和下载 |
| `connect_timeout_seconds` | `10` | 建立 TCP/TLS 连接的上限 |
| `search_timeout_seconds` | `180` | `/g2搜索` 和会话级搜索请求上限 |
| `image_timeout_seconds` | `300` | 生图/改图请求上限 |
| `video_create_timeout_seconds` | `120` | 视频创建请求上限 |
| `video_poll_timeout_seconds` | `30` | 单次视频状态查询上限，不是整个视频任务上限 |
| `video_poll_interval_seconds` | `3` | 视频状态轮询间隔 |
| `download_timeout_seconds` | `300` | 媒体下载上限 |
| `prompt_processing_timeout_seconds` | `15` | 提示词整理/优化和搜索结果整理模型上限 |
| `character_research_timeout_seconds` | `20` | 单次角色视觉资料搜索上限，超时后继续普通优化 |

每次网络尝试都会同时受阶段上限和当前任务剩余预算裁剪；任务总预算到期后停止重试、轮询和
候选模型切换。角色资料搜索的实际预算还会取 `character_research_timeout_seconds`、
`search_timeout_seconds` 与任务剩余时间的最小值。

### 并发与重试（`performance_settings.reliability`）

| 配置键 | 默认值 | 说明 |
|---|---:|---|
| `max_concurrent_searches` | `4` | 同时进行的搜索数，范围 1--16 |
| `max_concurrent_media_jobs` | `2` | 同时进行的媒体任务数，范围 1--8 |
| `model_retry_count` | `2` | 搜索、生图、改图、模型目录和图片下载的额外重试次数 |
| `video_retry_count` | `2` | 视频创建、轮询和视频下载的额外重试次数 |
| `retry_base_delay_seconds` | `0.5` | 指数退避的基础等待，范围 0.1--5.0 |
| `model_switch_errors` | `401,403,404,429,...` | 命中后跳过当前模型剩余重试，切换下一个候选 |

单个候选模型会先完成 `retry_count + 1` 次请求，只有重试耗尽或命中稳定切换错误才会进入
下一候选；生成 POST 也遵循该契约，因此网络错误可能造成重复生成或重复扣费。

## 文件与缓存（`storage_settings`）

| 配置键 | 默认值 | 说明 |
|---|---:|---|
| `max_input_image_mb` | `12` | 改图输入图片上限，为 32 MiB JSON 体积预留 Base64 膨胀空间 |
| `max_image_download_mb` | `25` | 图片下载上限 |
| `max_video_download_mb` | `190` | 视频下载上限，低于 QQ Official 200 MiB 硬上限 |
| `save_media` | `false` | 开启后将成功媒体移动到 `archive/`，否则发送后删除 |
| `temp_retention_hours` | `24` | 启动时清理超过该时长的临时媒体 |

## 访问控制（`access_settings`）

`user_whitelist`、`user_blacklist`、`group_whitelist`、`group_blacklist` 均为 ID 列表。空列表
表示不限制；黑名单优先，群列表只对群聊生效。

## 管理面板（`panel_settings`）

| 配置键 | 默认值 | 说明 |
|---|---:|---|
| `admin_username` / `admin_password` | `""` | `/g2面板` 管理端凭据，与 API Key 独立；不写日志 |
| `panel_period` | `7d` | `24h`、`7d`、`30d` 或 `90d`，用于审计汇总和按模型统计 |
| `panel_sections` | 全选 | 账号池、图片库、视频库、请求审计汇总、按模型统计；取消的块不会发起请求 |
| `panel_t2i_enabled` | `true` | 使用 AstrBot 全局 HTML-to-image 服务；关闭后发送纯文本 |
| `panel_resolution` | `1080p` | `720p`、`1080p` 或 `1440p` |
| `panel_push_targets` | `[]` | 固定 UMO 目标列表 |
| `panel_cron_enabled` / `panel_cron_expression` | `false` / `0 9 * * *` | 五段 Cron 定时推送 |
| `panel_interval_enabled` / `panel_interval_minutes` | `false` / `30` | 从每日 00:00 对齐的间隔推送 |

## 自愈、拒绝与安全

- 安全自愈：移除 URL 末尾 `/`、ID 转字符串、列表去重、模型列表去空白。
- 拒绝：非法协议、userinfo/query/fragment、越界值、未知选项、模型列表中的逗号、超 12 个模型或超 255 字符模型名。
- `enable_web_search` 与 `enable_x_search` 同时关闭时明确禁用搜索，不发送没有工具的请求。
- HTTP、网络、JSON 和远端结构错误默认可按重试契约重试；输入校验、媒体大小、路径安全和平台发送错误不会自动重放。
- 日志不包含 API Key、Bearer/JWT、密码、代理认证、媒体 URL、请求 ID、Base64 或上游原始响应正文。
