# astrbot_plugin_grok2api_sub

<div align="center">

<img src="https://count.getloli.com/@astrbot-plugin-grok2api-sub?name=astrbot-plugin-grok2api-sub&theme=booru-jaypee&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto" alt="count" />

![AstrBot](https://img.shields.io/badge/AstrBot-plugin-5865f2?style=flat-square)
![Version](https://img.shields.io/badge/version-0.1.6-22c55e?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab?style=flat-square)
![Platform](https://img.shields.io/badge/platform-OneBot%20%2F%20QQ%20Official-f97316?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-3b82f6?style=flat-square)

<br>

<img src="logo.png" alt="Grok2API Sub 助手 Logo" width="180">

</div>

> **社区非官方插件**：本项目与 xAI、Grok 无隶属或背书关系。

通过兼容 **grok2api 接口** 的站点（如自部署或第三方 API）提供联网搜索、文生图、改图和文生视频，并同时支持 **OneBot/NapCat** 与 **QQ Official** 双平台发送。

插件已在 [chenyme/grok2api](https://github.com/chenyme/grok2api) 项目上完成测试，理论上兼容任何实现了相同接口的 grok2api 服务。直接填入对方提供的 **Client Key** 和 **API 地址** 即可使用，无需自部署。

> `/g2面板` 管理命令除外：它需要 grok2api 管理面 API（`/api/admin/v1/...`），这部分目前仅 [chenyme/grok2api](https://github.com/chenyme/grok2api) 自部署实例支持。其他站点不提供管理面时，面板命令不可用，其余搜索/媒体功能不受影响。

## 功能

- 手动命令联网搜索（默认同时启用 Web 与 X 搜索，并由当前会话模型整理结果）
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

- 需要一个兼容 grok2api 接口的服务，并为其签发一个 **Client Key**。你可以选择：
  - **自部署** [chenyme/grok2api](https://github.com/chenyme/grok2api) 实例
  - **使用第三方站点** 提供的 grok2api 兼容 API
- 插件只保存 Client Key。**不要**把管理员 JWT、账号 SSO/OAuth、QQ AppID/AppSecret 填入本插件。
- 生产环境应使用 HTTPS，并签发最小权限的 Client Key。

> `/g2面板` 管理功能需要 grok2api 管理面 API（`/api/admin/v1/...`），这部分目前仅自部署的 [chenyme/grok2api](https://github.com/chenyme/grok2api) 实例支持。使用第三方站点时，面板功能不可用，其余搜索/媒体能力不受影响。

### 典型配置

在 AstrBot WebUI 插件配置页，配置按 4 个分组展示：

**连接设置（`connection_settings`）**

| 配置项 | 示例 |
|---|---|
| `enabled` | `true`（总开关） |
| `api_base_url` | `https://grok.example.com`（远端 grok2api 根地址，不带 `/v1`） |
| `client_api_key` | `g2a_...`（专用 Client Key，非管理员 JWT） |
| `admin_username` / `admin_password` | 管理面登录凭据；仅用于 `/g2面板` 只读查询，与 Client Key 相互独立 |
| `verify_tls` | `true`（生产保持开启） |
| `client_proxy_url` | `http://proxy.example:8080`（可选，AstrBot 到远端 API 的代理，留空不代理） |

**能力设置（`capability_settings`）**

| 配置项 | 示例 |
|---|---|
| `search_models` | `grok-chat-fast`、`grok-build-0.1`、`grok-4.3`、`grok-4.5`、`grok-4.6`、`grok-composer-2.5-fast`、`grok-4.20-0309-non-reasoning`、`grok-4.20-0309-reasoning`、`grok-4.20-multi-agent-0309`（多行文本，每行一个，**上方优先**，最多 12 个，留空禁用搜索） |
| `enable_web_search` | `true`（默认启用 Web 联网搜索） |
| `enable_x_search` | `true`（默认启用 X 搜索；`grok-chat-*` 自动降级为仅 Web 搜索） |
| `search_reasoning_effort` | `high`（`auto`、`none`、`low`、`medium`、`high`、`xhigh`） |
| `image_models` | `grok-imagine-image-lite`、`grok-imagine-image`、`grok-imagine-image-quality`（多行文本，每行一个，**上方优先**，留空禁用生图） |
| `image_edit_models` | `grok-imagine-image-lite`、`grok-imagine-image`、`grok-imagine-image-quality`（多行文本，每行一个，**上方优先**，留空禁用改图） |
| `video_models` | `grok-imagine-video`（多行文本，每行一个，留空禁用视频） |
| `prompt_processing.mode` | `off`（原提示词直接传上游）；可选 `extract`（仅补全参数）或 `enhance`（优化提示词和参数） |
| `prompt_processing.extract_provider_id` | AstrBot 已配置的整理文本模型；仅 `extract` 使用 |
| `prompt_processing.enhance_provider_id` | AstrBot 已配置的优化文本模型；仅 `enhance` 使用 |
| `prompt_processing.disable_prompt_processing_with_reference_image` | `false`；仅有改图消息图片、视频消息图片或视频 `--image-url` 时生效。关闭时遵循 `prompt_processing.mode`；开启后参考图请求原提示词直传且不调用提示词处理模型 |
| `enable_llm_search_tool` | `true`（主模型按 Tool 描述自动搜索） |

使用 `extract` 或 `enhance` 时，插件会在严格校验成功后将最终发送给 grok2api 的提示词与媒体参数 JSON 写入本地 `prompt_processing_resolved` 日志，方便管理员检查处理质量；不会回复给用户。直传模式和失败输出不记录，凭据、Bearer/JWT、密码/secret、代理 userinfo 与 Base64 始终脱敏。

**日志**：INFO 级别按多行块显示每个任务的开始和最终完成/失败。搜索、生图、改图、视频会完整记录原始提示词与实际请求提示词、实际请求参数（比例、时长、分辨率、数量、返回格式等）、候选模型、最终模型、回退次数、远端重试次数和耗时；远端重试次数汇总任务内模型目录、生成、轮询和下载实际发出的额外 HTTP 请求，正常的多次视频状态查询不算重试。面板只记录区块与推送汇总。HTTP、管理面子请求、模型尝试、轮询和提示词处理审计在 DEBUG 级别查看。日志不含 `trace_id`、参考图 URL、媒体 URL、请求 ID 或上游响应正文；凭据类片段仍强制脱敏。

**访问控制（`access_settings`）**：`user_whitelist` / `user_blacklist` / `group_whitelist` / `group_blacklist`（空列表不限制）。

**高级设置（`advanced_settings`）**：超时、并发、媒体大小、重试、`save_media` 等。

`model_retry_count` 默认 `2`，覆盖搜索、生图、改图、模型目录和图片下载；`video_retry_count`
默认 `2`，覆盖视频创建、状态轮询和视频下载。两个值都是**首次请求之外**的额外重试次数，设为
`0` 即只请求一次。`retry_excluded_errors` 默认留空，表示所有远端 HTTP、网络、JSON 和远端响应
结构错误都可重试；可用英文逗号填写 HTTP 状态码或稳定错误码排除，例如
`400,401,403,404,422,auth_error,model_not_found,invalid_json,invalid_model_catalog,network_error`。每次尝试仍使用各自的
单次超时；视频不再使用插件侧的总等待上限，会持续轮询至远端返回完成或失败状态。

> `search_models` 默认顺序从上到下为 `grok-chat-fast`、`grok-build-0.1`、`grok-4.3`、`grok-4.5`、`grok-4.6`、`grok-composer-2.5-fast`、`grok-4.20-0309-non-reasoning`、`grok-4.20-0309-reasoning`、`grok-4.20-multi-agent-0309`，可自行按需调整顺序。模型列表来自一次远端实例快照，实际可用性以你自己 Client Key 的 `GET /v1/models` 可见目录为准；目录请求或结构校验失败时按原配置顺序尝试，成功空目录表示没有可见候选并直接结束搜索。`grok-chat-*` 不支持 X 搜索，启用 X 搜索时会保留 Web 搜索；如果只启用 X 搜索且候选全为 chat 模型，搜索能力会明确不可用。`search_reasoning_effort=auto` 或候选不支持所选强度时，插件会省略 `reasoning` 参数继续搜索，不会因此跳过模型。

模型通常通过 `GET /v1/models` 可见，可用性以该目录为准。

## 面板与命令

| 命令 | 别名 | 权限 | 行为 |
|---|---|---|---|
| `/g2搜索 <问题>` | `/grok2搜索` | 访问规则 | 强制执行全局已启用的 Web/X 搜索；成功后用当前会话的 AstrBot 聊天模型整理正文，来源由插件本地追加，整理失败回退原始结果 |
| `/g2生图 <提示词>` | `/grok2生图` | 访问规则 | 整段提示词直传或按模式处理，每次生成 1 张；图片默认 `1k` |
| `/g2改图 <编辑要求>` | `/grok2改图` | 访问规则 | 编辑当前或回复消息中的第一张图片 |
| `/g2视频 [--image-url <HTTPS_URL>] <提示词>` | `/grok2视频` | 访问规则 | 整段提示词直传或按模式处理；默认 `6s`、`720p`，消息/回复参考图会自动匹配最近支持比例，也可显式传入 URL 参考图 |
| `/g2面板` | `/grok2面板` | AstrBot ADMIN | 按所选块发送账号/媒体/审计/模型聚合，不要求 Client Key |
| `/g2面板订阅` | `/grok2面板订阅` | AstrBot ADMIN | 订阅当前会话的定时面板推送 |
| `/g2面板退订` | `/grok2面板退订` | AstrBot ADMIN | 退订当前会话的定时面板推送 |
| `/g2面板订阅列表` | `/grok2面板订阅列表` | AstrBot ADMIN | 查看当前会话状态和订阅数量，不显示 UMO |
| `/g2帮助` | `/grok2帮助` | 所有人 | 输出命令说明和当前能力 |

详见 [docs/commands.md](docs/commands.md)。

`/g2搜索` 只使用 `enable_web_search`、`enable_x_search` 与
`search_reasoning_effort` 的全局设置；两个搜索开关都关闭时会在请求前拒绝。搜索成功后，插件
使用当前会话的 AstrBot 聊天模型进行一次独立整理：不携带会话历史或工具，正文仅基于检索材料，
来源仍由插件本地追加；整理失败时自动发送原始结果。`grok2api_web_search` 则是给 AstrBot 主模型
选择的 Tool：主模型调用 Tool 后，仍会根据结构化 Tool 结果组织最终回复，不触发这次整理。

`/g2面板` 默认经 AstrBot 已配置的 HTML-to-image 服务发送 1920x1080（16:9）图片，也可在插件配置中选择 720p 或 1440p；T2I 不可用时会自动
退回纯文本。背景图每次随机打乱 Wallhaven（动漫、SFW、16:9）、LoliAPI 横屏和 t.alcy 横屏的尝试顺序，各站点均随机取图，并使用插件的全局代理和 TLS
配置；所有来源都执行图片解码、体积和横向比例校验，失败时继续剩余图源，全部失败后复用最近有效缓存，未命中缓存时使用内置背景。来源不保证排除 AI 图片。定时推送的固定 UMO 目标与命令订阅目标合并
去重；Cron 和从每日 00:00 对齐的间隔任务可同时启用，同一目标同一分钟只发送一次。

> 注意：`/g2面板` 需要 grok2api 管理面 API（`/api/admin/v1/...`），目前仅自部署的 [chenyme/grok2api](https://github.com/chenyme/grok2api) 实例支持。使用第三方站点时，面板功能不可用，其余搜索/媒体能力不受影响。

## 限制与安全警告

- 搜索、生图、改图和视频请求默认会按重试配置重放；生成 POST 也可能被重放，可能造成重复生成或重复扣费。需要避免某类错误重试时，在 `retry_excluded_errors` 中显式排除对应状态码或错误码。
- 每个候选模型先用完所属重试次数再尝试下一候选。仅 `model_not_found`、`model_not_allowed` 会触发媒体候选回退；搜索还会在 `search_not_performed` 时回退。将这些错误码加入 `retry_excluded_errors` 会跳过当前候选的剩余重试，但仍会继续下一候选。
- 图片/视频会立即下载到本地再发送；原结果 URL 不作为永久存档。
- QQ Official 单次最多生成/发送 **4** 张图片；超出在调用 API 前拒绝。
- 改图只接受当前消息或回复链中的图片，不接受任意本地路径、用户输入 URL 或 `file_id`。
- 视频可用 `/g2视频 --image-url <HTTPS_URL> <提示词>` 或 `--image-url=<HTTPS_URL>` 显式传入参考图。仅接受外部 HTTPS 域名，不接受 userinfo、fragment、单标签主机、`localhost`、`.local` 或 IP 字面量；插件不下载、不记录 URL，也不将其交给提示词模型，因此不会自动读取外链图片比例。上游服务负责最终下载、重定向与网络访问安全。
- `prompt_processing.disable_prompt_processing_with_reference_image` 关闭时完全遵循全局模式；开启且检测到参考图时，改图/视频均使用原提示词且不调用提示词处理模型。消息或回复中的视频参考图会在未解析比例时自动填入最接近的支持比例；该行为不额外发送用户提示。
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
python -m pip install -e ".[dev]"
python -m pytest -q
ruff check .
```

详见 [docs/testing.md](docs/testing.md) 与 [docs/architecture.md](docs/architecture.md)。

协作与维护入口：[贡献指南](CONTRIBUTING.md) · [发布维护指南](docs/maintainers/release.md)。

## 致谢

- [chenyme/grok2api](https://github.com/chenyme/grok2api)：兼容 API 与管理面 API 的集成目标。
- [Xyanxhu](https://github.com/Xyanxhu)：管理面板主题设计参考。
- [PeeGayhub Telegram 表情包系列](https://t.me/addstickers/PeeGayhub)：插件图标借鉴了该系列表情包风格；图标素材由 GPT 生成。

## License

MIT，见 [LICENSE](LICENSE)。
