# astrbot_plugin_grok2api_sub

> **社区非官方插件**：本项目与 xAI、Grok 和 grok2api 项目无隶属或背书关系。

通过 **grok2api Client Key** 提供联网搜索、文生图、改图和文生视频，并同时支持 **OneBot/NapCat** 与 **QQ Official** 双平台发送。

## 功能

- 手动命令联网搜索（默认同时启用 Web 与 X 搜索）
- AstrBot 主模型按 Tool 描述自动决定是否联网（`grok2api_web_search`）
- 文生图（1 到配置上限张）
- 单图改图（当前消息或回复消息中的第一张图片）
- 文生视频，可选单图引导
- 双平台（OneBot / QQ Official）私聊、群聊发送
- 配置校验、访问控制、并发限制、大小限制、临时文件清理、可诊断错误

## 安装

克隆或下载本仓库到 AstrBot `plugins/` 目录，或在 AstrBot 插件市场安装。

```bash
git clone https://github.com/shitianyaa/astrbot_plugin_grok2api_sub
cd astrbot_plugin_grok2api_sub
python -m pip install -r requirements.txt
```

## grok2api 前置

- 需要运行一个 grok2api 服务，并为其签发一个 **Client Key**。
- 插件只保存 Client Key。**不要**把管理员 JWT、账号 SSO/OAuth、QQ AppID/AppSecret 填入本插件。
- 生产环境必须使用 HTTPS，并签发最小权限的 Client Key。

### 典型配置

在 AstrBot WebUI 插件配置页，配置按 4 个分组展示：

**连接设置（`connection_settings`）**

| 配置项 | 示例 |
|---|---|
| `enabled` | `true`（总开关） |
| `api_base_url` | `https://grok.example.com`（远端 grok2api 根地址，不带 `/v1`） |
| `client_api_key` | `g2a_...`（专用 Client Key，非管理员 JWT） |
| `verify_tls` | `true`（生产保持开启） |
| `client_proxy_url` | `http://proxy.example:8080`（可选，AstrBot 到远端 API 的代理，留空不代理） |

**能力设置（`capability_settings`）**

| 配置项 | 示例 |
|---|---|
| `search_models` | `grok-4.5,grok-4.3,grok-4.20-0309-reasoning,grok-4.20-0309-non-reasoning,grok-4.20-multi-agent-0309,grok-build-0.1,grok-chat-fast`（英文逗号分隔，**左侧优先**，最多 12 个，留空禁用搜索） |
| `enable_web_search` | `true`（默认启用 Web 联网搜索） |
| `enable_x_search` | `true`（默认启用 X 搜索；`grok-chat-*` 自动降级为仅 Web 搜索） |
| `search_reasoning_effort` | `high`（`auto`、`none`、`low`、`medium`、`high`、`xhigh`） |
| `image_model` | `grok-imagine-image` |
| `image_edit_model` | `grok-imagine-image` |
| `video_model` | `grok-imagine-video` |
| `prompt_processing.mode` | `off`（原提示词直接传上游）；可选 `extract`（仅补全参数）或 `enhance`（优化提示词和参数） |
| `prompt_processing.extract_provider_id` | AstrBot 已配置的整理文本模型；仅 `extract` 使用 |
| `prompt_processing.enhance_provider_id` | AstrBot 已配置的优化文本模型；仅 `enhance` 使用 |
| `enable_llm_search_tool` | `true`（主模型按 Tool 描述自动搜索） |

使用 `extract` 或 `enhance` 时，插件会在严格校验成功后将最终发送给 grok2api 的提示词与媒体参数 JSON 写入本地 `prompt_processing_resolved` 日志，方便管理员检查处理质量；不会回复给用户。直传模式和失败输出不记录，凭据、Bearer/JWT、密码/secret、代理 userinfo 与 Base64 始终脱敏。

**访问控制（`access_settings`）**：`user_whitelist` / `user_blacklist` / `group_whitelist` / `group_blacklist`（空列表不限制）。

**高级设置（`advanced_settings`）**：超时、并发、媒体大小、重试、`save_media` 等。

`model_retry_count` 默认 `2`，覆盖搜索、生图、改图、模型目录和图片下载；`video_retry_count`
默认 `2`，覆盖视频创建、状态轮询和视频下载。两个值都是**首次请求之外**的额外重试次数，设为
`0` 即只请求一次。`retry_excluded_errors` 默认留空，表示所有远端 HTTP、网络、JSON 和远端响应
结构错误都可重试；可用英文逗号填写 HTTP 状态码或稳定错误码排除，例如
`400,401,403,404,422,auth_error,model_not_found,invalid_json,network_error`。每次尝试仍使用各自的
单次超时；视频不再使用插件侧的总等待上限，会持续轮询至远端返回完成或失败状态。

> `search_models` 默认顺序为 `grok-4.5,grok-4.3,grok-4.20-0309-reasoning,grok-4.20-0309-non-reasoning,grok-4.20-multi-agent-0309,grok-build-0.1,grok-chat-fast`，可自行按需调整。模型列表来自一次远端实例快照，实际可用性以你自己 Client Key 的 `GET /v1/models` 可见目录为准。`grok-chat-*` 不支持 X 搜索，启用 X 搜索时会保留 Web 搜索；如果只启用 X 搜索且候选全为 chat 模型，搜索能力会明确不可用。`search_reasoning_effort=auto` 或候选不支持所选强度时，插件会省略 `reasoning` 参数继续搜索，不会因此跳过模型。

模型通常通过 `GET /v1/models` 可见，可用性以该目录为准。

## 面板与命令

| 命令 | 别名 | 权限 | 行为 |
|---|---|---|---|
| `/g2搜索 <问题>` | `/grok2搜索` | 访问规则 | 强制执行全局已启用的 Web/X 搜索，直接返回远端正文和来源，不调用本地 LLM 改写 |
| `/g2生图 <提示词>` | `/grok2生图` | 访问规则 | 整段提示词直传或按模式处理，每次生成 1 张；图片默认 `1k` |
| `/g2改图 <编辑要求>` | `/grok2改图` | 访问规则 | 编辑当前或回复消息中的第一张图片 |
| `/g2视频 <提示词>` | `/grok2视频` | 访问规则 | 整段提示词直传或按模式处理；默认 `6s`、`720p`，可附带首帧 |
| `/g2面板` | `/grok2面板` | AstrBot ADMIN | 按所选块发送账号/媒体/审计/模型聚合，不要求 Client Key |
| `/g2面板订阅` | `/grok2面板订阅` | AstrBot ADMIN | 订阅当前会话的定时面板推送 |
| `/g2面板退订` | `/grok2面板退订` | AstrBot ADMIN | 退订当前会话的定时面板推送 |
| `/g2面板订阅列表` | `/grok2面板订阅列表` | AstrBot ADMIN | 查看当前会话状态和订阅数量，不显示 UMO |
| `/g2帮助` | `/grok2帮助` | 所有人 | 输出命令说明和当前能力 |

详见 [docs/commands.md](docs/commands.md)。

`/g2搜索` 只使用 `enable_web_search`、`enable_x_search` 与
`search_reasoning_effort` 的全局设置；两个搜索开关都关闭时会在请求前拒绝。它直接请求
grok2api 并发送远端结果。`grok2api_web_search` 则是给 AstrBot 主模型选择的 Tool：主模型
调用 Tool 后，仍会根据 Tool 结果组织最终回复。

`/g2面板` 默认经 AstrBot 已配置的 HTML-to-image 服务发送 1920x1080（16:9）图片，也可在插件配置中选择 720p 或 1440p；T2I 不可用时会自动
退回纯文本。背景图每次发送都向 Lolicon 请求非 R18、排除 AI 的横向图片，并使用插件的全局代理和 TLS
配置；失败时复用最近有效缓存，未命中缓存时使用内置背景。定时推送的固定 UMO 目标与命令订阅目标合并
去重；Cron 和从每日 00:00 对齐的间隔任务可同时启用，同一目标同一分钟只发送一次。

## 限制与安全警告

- 搜索、生图、改图和视频请求默认会按重试配置重放；生成 POST 也可能被重放，可能造成重复生成或重复扣费。需要避免某类错误重试时，在 `retry_excluded_errors` 中显式排除对应状态码或错误码。
- 图片/视频会立即下载到本地再发送；原结果 URL 不作为永久存档。
- QQ Official 单次最多生成/发送 **4** 张图片；超出在调用 API 前拒绝。
- 改图只接受当前消息或回复链中的图片，不接受任意本地路径、用户输入 URL 或 `file_id`。
- 视频时长 1–15 秒；比例仅 `1:1`、`16:9`、`9:16`、`4:3`、`3:4`、`3:2`、`2:3`。
- `send_media_progress` 默认开启；生图、改图、视频在开始远端任务前各提示一次，提示发送失败不会取消任务。
- 发送异常时交付状态可能不确定，插件不自动重发。

## 排错

- `/g2面板` 显示“未获取”：检查 `connection_settings` 的 `admin_username`/`admin_password` 与 `api_base_url`。
- 401/403：Client Key 无效或权限不足。
- 404：`api_base_url` 或 endpoint 错误。
- 搜索无完成态 `web_search_call` 或 `x_search_call`：上游未执行联网搜索，先按 `model_retry_count` 重试当前模型，耗尽后按候选顺序回退。

## 开发

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
ruff check .
```

详见 [docs/testing.md](docs/testing.md) 与 [docs/architecture.md](docs/architecture.md)。

## License

MIT，见 [LICENSE](LICENSE)。
