# 配置

`_conf_schema.json` 是唯一的 WebUI 真源，`core/config.py` 在启动时解析为不可变
`PluginConfig`。运行时不得散落 `config.get()`。

Schema 顶层只有 4 个 `object` 分组：`connection_settings`、`capability_settings`、
`access_settings`、`advanced_settings`。业务层只读取 `PluginConfig` 的扁平属性，
不直接读嵌套字典。

## 连接设置（connection_settings）

| 配置键 | 类型 | 默认值 | 校验/说明 |
|---|---|---:|---|
| `enabled` | bool | `true` | 总开关 |
| `api_base_url` | string | `""` | 远端 grok2api 根地址；只允许 http/https，禁止 userinfo/query/fragment，移除末尾 `/`；留空则所有能力禁用（`未配置远端 API 地址`） |
| `api_key` | string | `""` | grok2api API Key；运行配置保存，禁止写日志 |
| `verify_tls` | bool | `true` | 生产保持开启 |
| `client_proxy_url` | string | `""` | AstrBot 到 grok2api 的代理；只允许 http/https；允许认证但日志只显示协议/主机/端口 |
| `admin_username` | string | `""` | 管理面登录用户名；与搜索 API Key 相互独立，仅 `/g2面板` 使用；不写日志 |
| `admin_password` | string | `""` | 管理面登录密码；**泄露即有权读取上游账号与聚合数据（仅 bot 主人接线）**；不写日志 |

## 能力设置（capability_settings）

| 配置键 | 类型 | 默认值 | 校验/说明 |
|---|---|---:|---|
| `search_models` | text | `grok-chat-fast`、`grok-build-0.1`、`grok-4.3`、`grok-4.5`、`grok-4.6`、`grok-composer-2.5-fast`、`grok-4.20-0309-non-reasoning`、`grok-4.20-0309-reasoning`、`grok-4.20-multi-agent-0309` | 多行文本，每行一个，**上方优先**，最多 12 个，每项 ≤255 字符，按首次出现保序去重；英文或中文逗号均直接报配置错误；留空禁用搜索 |
| `enable_web_search` | bool | `true` | 是否将 `web_search` 工具传给远端 Responses；与 X 搜索不能同时关闭 |
| `enable_x_search` | bool | `true` | 是否将 `x_search` 工具传给远端 Responses；`grok-chat-*` 不支持该工具，会自动保留已启用的 Web 搜索；与 Web 搜索不能同时关闭 |
| `search_reasoning_effort` | string | `high` | `auto`、`none`、`low`、`medium`、`high`、`xhigh`；`auto` 不发送 `reasoning` 字段，由远端选择；已知模型不支持所选值或自定义模型时也省略该字段，保留该候选的搜索机会 |
| `image_models` | text | `grok-imagine-image-lite`、`grok-imagine-image`、`grok-imagine-image-quality` | 多行文本，每行一个，**上方优先**，最多 12 个；英文或中文逗号均直接报配置错误；留空禁用生图 |
| `image_edit_models` | text | `grok-imagine-image`、`grok-imagine-image-quality` | 多行文本，每行一个，**上方优先**，最多 12 个；英文或中文逗号均直接报配置错误；留空禁用改图 |
| `video_models` | text | `grok-imagine-video` | 多行文本，每行一个，最多 12 个；英文或中文逗号均直接报配置错误；留空禁用视频 |
| `prompt_processing.mode` | string | `off` | `off` 原文直传；`extract` 调用整理模型，仅补全参数；`enhance` 调用优化模型，改写提示词并补全参数 |
| `prompt_processing.extract_provider_id` | string | `""` | AstrBot 原生供应商选择器；仅整理模式使用，必须选择已配置文本模型 |
| `prompt_processing.enhance_provider_id` | string | `""` | AstrBot 原生供应商选择器；仅优化模式使用，可与整理模型不同 |
| `prompt_processing.disable_prompt_processing_with_reference_image` | bool | `false` | 仅检测到改图消息图片、视频消息图片或视频显式 `--image-url` 时生效；`false` 时遵循全局模式，`true` 时本次请求强制 `off`、原提示词直传且不调用提示词处理模型 |

| `enable_llm_search_tool` | bool | `true` | 会话级暴露搜索 Tool；是否调用仍由 AstrBot 主模型决定 |
| `show_search_sources` | bool | `true` | 手动命令输出与 Tool 返回内容是否包含结构化来源 |
| `max_search_sources` | int | `5` | 0–10；`0` 不输出来源段，也不向 Tool 返回来源 |
| `max_search_output_chars` | int | `6000` | 500–20000，Unicode 字符截断并标记 |
| `image_response_format` | string | `b64_json` | `b64_json`、`url`；无论哪种都落盘后发送 |
| `send_media_progress` | bool | `true` | 生图、改图、视频在任务锁取得后各发一次尽力而为的进度提示；提示发送失败不取消任务 |

启用 `extract` 或 `enhance` 后，处理成功且字段校验完成的最终请求 JSON 会写入 DEBUG 级别的本地 `prompt_processing_resolved` 日志，供管理员核对提示词与参数质量。该记录不会发送给聊天用户；`off` 模式、失败输出和未经校验的模型原文不会记录。API Key、Bearer/JWT、密码/secret、代理 userinfo 与 Base64 仍会脱敏。参考图不会把消息图片、data URL 或显式 URL 传给文本模型；模型只接收“是否存在参考图”的布尔上下文。开启 `disable_prompt_processing_with_reference_image` 后，有参考图的请求直接使用 `off` 模式；消息或回复中的视频参考图会在处理器没有给出比例时自动匹配最近支持比例，显式 URL 不下载、不识别尺寸。

## 访问控制（access_settings）

| 配置键 | 类型 | 默认值 | 校验/说明 |
|---|---|---:|---|
| `user_whitelist` | list[string] | `[]` | 空表示不限制；私聊/群聊都生效 |
| `user_blacklist` | list[string] | `[]` | 黑名单优先；私聊/群聊都生效 |
| `group_whitelist` | list[string] | `[]` | 只对群聊生效 |
| `group_blacklist` | list[string] | `[]` | 黑名单优先；只对群聊生效 |

## 高级设置（advanced_settings）

| 配置键 | 类型 | 默认值 | 校验/说明 |
|---|---|---:|---|
| `connect_timeout_seconds` | int | `10` | 1–60 |
| `search_timeout_seconds` | int | `180` | 10–600 |
| `image_timeout_seconds` | int | `300` | 30–900 |
| `video_create_timeout_seconds` | int | `120` | 10–600 |
| `video_poll_timeout_seconds` | int | `30` | 1–600；每次视频状态查询的整体超时 |
| `video_poll_interval_seconds` | int | `3` | 1–30 |
| `download_timeout_seconds` | int | `300` | 30–1800 |
| `prompt_processing_timeout_seconds` | int | `15` | 1–60；提示词整理/优化或搜索结果整理模型超时、调用失败或输出非 JSON 时，媒体生成终止；`/g2搜索` 则回退发送原始结果 |
| `max_input_image_mb` | int | `12` | 1–24，为 32 MiB JSON 请求体留 Base64 膨胀空间 |
| `max_image_download_mb` | int | `25` | 1–100 |
| `max_video_download_mb` | int | `190` | 1–200，低于 QQ Official 200 MiB 硬上限 |
| `panel_period` | string | `7d` | 请求审计汇总与本地按模型统计的共用区间，仅 `24h`/`7d`/`30d`/`90d` |
| `panel_sections` | list[string] | 全选 | `/g2面板` 获取并发送的数据块多选，中文选项顺序即输出顺序；选项：`账号池`、`图片库`、`视频库`、`请求审计汇总`、`按模型统计`；置空则面板不发任何请求 |
| `panel_t2i_enabled` | bool | `true` | 面板优先使用 AstrBot 全局已配置的 HTML-to-image 服务；关闭时固定发送纯文本 |
| `panel_resolution` | string | `1080p` | 面板图片分辨率：`720p`（1280x720）、`1080p`（1920x1080）或 `1440p`（2560x1440） |
| `panel_push_targets` | template_list | `[]` | 固定推送 UMO；每项填写 `platform:message_type:session_id` 与启用状态，完整 UMO 不写日志或 `redacted_summary()` |
| `panel_cron_enabled` | bool | `false` | 启用五段 Cron 定时推送 |
| `panel_cron_expression` | string | `0 9 * * *` | 分、时、日、月、周五段 Cron 表达式 |
| `panel_interval_enabled` | bool | `false` | 启用从本地每日 `00:00` 对齐的间隔推送 |
| `panel_interval_minutes` | int | `30` | 1--1440 分钟；例如 30 分钟在每个整点和半点触发 |
| `max_concurrent_searches` | int | `4` | 1–16 |
| `max_concurrent_media_jobs` | int | `2` | 1–8 |
| `model_retry_count` | int | `2` | 0–5；搜索、生图、改图、模型目录和图片下载的额外重试次数，不含首次请求 |
| `video_retry_count` | int | `2` | 0–5；视频创建、状态轮询和视频下载的额外重试次数，不含首次请求 |
| `retry_base_delay_seconds` | float | `0.5` | 0.1–5.0 |
| `retry_excluded_errors` | string | `""` | 英文逗号分隔的 HTTP 状态码或稳定错误码；留空表示不排除远端错误，例如 `400,401,403,404,422,auth_error,model_not_found,invalid_json,invalid_model_catalog,network_error` |
| `save_media` | bool | `false` | false 发送后删除；true 成功文件移到 `archive/` 保留 |
| `temp_retention_hours` | int | `24` | 1–168 |

## 自愈与拒绝

- 安全自愈：URL 末尾 `/` 去除、ID 转字符串、列表去重、search_models 去空白/忽略空项/去重。
- 拒绝：非法协议、userinfo/query/fragment、越界值、非法 options、模型列表中的英文或中文逗号、超 12 个模型、超 255 字符模型名，抛配置错误。
- `enable_web_search` 与 `enable_x_search` 同时关闭不属于配置错误，但会明确禁用搜索能力，避免发出没有工具的 Responses 请求。
- 面板背景每次随机打乱 Wallhaven（动漫、SFW、16:9）、LoliAPI 横屏和 t.alcy 横屏的请求顺序，各站点均随机取图；API 请求与图片下载均显式使用 `client_proxy_url`、`verify_tls`，不读取环境代理。所有来源都执行解码、体积和横向比例校验，但来源不保证排除 AI 图片。单个图源失败后继续剩余图源，全部失败时复用 `panel_background.jpg` 缓存；无缓存时由卡片 CSS 使用默认背景。
- Cron 与间隔任务可同时启用。固定 UMO 和 `/g2面板订阅` 创建的 UMO 会合并去重；同一 UMO 在同一自然分钟最多有一次发送尝试。
- 仅开启 X 搜索而候选全为 `grok-chat-*` 时同样明确禁用搜索能力；chat 模型不会收到没有可用工具的请求。
- 每次尝试只使用该操作自己的单次超时。视频等待不再设置插件侧总时长，而是以 `video_poll_timeout_seconds`、`video_poll_interval_seconds` 和 `video_retry_count` 持续轮询远端终态。
- 远端 HTTP、网络、JSON 解析和远端响应结构错误默认都可重试，包含生成 POST；这可能重复生成或重复扣费。需要避免某类错误重试时，加入 `retry_excluded_errors`。本地输入校验、媒体大小限制、路径校验和平台消息发送不会被自动重放。
- INFO 日志只保留多行任务开始和完成/失败块。搜索、图片和视频任务完整记录原始提示词及实际请求提示词，并记录脱敏后的实际请求参数（搜索开关、推理强度、比例、时长、分辨率、数量、返回格式等）；任务结束记录实际模型、候选回退、任务内实际发出的额外远端请求、结果状态和耗时。模型目录、生成、状态查询和下载的重试都会汇总，正常的多次视频轮询不算重试。内部 HTTP、管理面请求、轮询、模型尝试、命令包装和媒体发送细节写入 DEBUG。`trace_id` 不再使用；参考图 URL、媒体 URL、请求 ID、上游响应正文和凭据不会写入任务日志。
- 单个候选先完成 `retry_count + 1` 次请求才进入下一候选。媒体仅在 `model_not_found` 或 `model_not_allowed` 时回退；搜索额外在 `search_not_performed` 时回退。排除这些错误码只会缩短当前候选的重试，不会阻止回退。
- 模型目录必须包含数组类型的 `data`。结构异常按 `invalid_model_catalog` 进入模型重试组，耗尽后按目录请求失败回退原配置；成功空目录直接返回无可见候选，不发送搜索 POST。

## 安全约束

- 管理员 JWT、账号 SSO/OAuth、QQ AppID/AppSecret 均不应填入插件。
- 代理 URL 允许认证，但日志只显示协议、主机、端口。
- `redacted_summary()` 只返回 `api_key_configured` 与 `admin_configured`，绝不返回 Key、管理密码或明文凭据本体。
