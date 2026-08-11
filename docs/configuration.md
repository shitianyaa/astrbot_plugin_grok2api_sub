# 配置

`_conf_schema.json` 是唯一的 WebUI 真源，`core/config.py` 在启动时解析为不可变
`PluginConfig`。运行时不得散落 `config.get()`。

| 配置键 | 类型 | 默认值 | 校验/说明 |
|---|---|---:|---|
| `enabled` | bool | `true` | 总开关 |
| `api_base_url` | string | `http://127.0.0.1:8000` | 只允许 http/https，禁止 userinfo/query/fragment，移除末尾 `/` |
| `client_api_key` | string | 空 | 运行配置保存，禁止写日志 |
| `verify_tls` | bool | `true` | 生产保持开启 |
| `client_proxy_url` | string | `http://127.0.0.1:3067` | AstrBot 到 grok2api 的代理；只允许 http/https |
| `search_model` | string | 空 | 必填后才暴露搜索能力 |
| `image_model` | string | 空 | 必填后才启用生图 |
| `image_edit_model` | string | 空 | 必填后才启用改图 |
| `video_model` | string | 空 | 必填后才启用视频 |
| `enable_llm_search_tool` | bool | `true` | 会话级暴露搜索 Tool |
| `show_search_sources` | bool | `true` | 是否展示结构化来源 |
| `max_search_sources` | int | `5` | 0–10 |
| `max_search_output_chars` | int | `6000` | 500–20000，Unicode 字符截断并标记 |
| `connect_timeout_seconds` | int | `10` | 1–60 |
| `search_timeout_seconds` | int | `180` | 10–600 |
| `image_timeout_seconds` | int | `300` | 30–900 |
| `video_create_timeout_seconds` | int | `120` | 10–600 |
| `video_poll_interval_seconds` | int | `3` | 1–30 |
| `video_max_wait_seconds` | int | `1800` | 30–7200 |
| `download_timeout_seconds` | int | `300` | 30–1800 |
| `max_input_image_mb` | int | `12` | 1–24，为 32 MiB JSON 请求体留 Base64 膨胀空间 |
| `max_image_download_mb` | int | `25` | 1–100 |
| `max_video_download_mb` | int | `190` | 1–200，低于 QQ Official 200 MiB 硬上限 |
| `max_images_per_request` | int | `4` | 1–10；QQ Official 运行时仍固定上限 4 |
| `max_concurrent_searches` | int | `4` | 1–16 |
| `max_concurrent_media_jobs` | int | `2` | 1–8 |
| `video_resolution` | string | `""` | `""`、`480p`、`720p` |
| `image_response_format` | string | `b64_json` | `b64_json`、`url`；无论哪种都落盘后发送 |
| `get_retry_attempts` | int | `3` | 1–5，仅用于允许重试的操作 |
| `retry_base_delay_seconds` | float | `0.5` | 0.1–5.0 |
| `save_media` | bool | `false` | false 发送后删除；true 保留到工作区 |
| `temp_retention_hours` | int | `24` | 1–168 |
| `send_video_progress` | bool | `true` | 视频生成前发一次进度 |
| `user_whitelist` | list[string] | `[]` | 空表示不限制 |
| `user_blacklist` | list[string] | `[]` | 黑名单优先 |
| `group_whitelist` | list[string] | `[]` | 只对群聊生效 |
| `group_blacklist` | list[string] | `[]` | 黑名单优先 |
| `debug_mode` | bool | `false` | 只增加状态码/耗时/request id 日志 |

## 自愈与拒绝

- 安全自愈：URL 末尾 `/` 去除、ID 转字符串、列表去重。
- 拒绝：非法协议、userinfo/query/fragment、越界值、非法 options，抛配置错误。

## 安全约束

- 管理员 JWT、账号 SSO/OAuth、QQ AppID/AppSecret 均不应填入插件。
- 代理 URL 允许认证，但日志与 `/g2状态` 只显示协议、主机、端口。
- `redacted_summary()` 只返回 `client_api_key_configured`，绝不返回 Key 本体。