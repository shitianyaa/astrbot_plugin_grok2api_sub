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
| `enable_llm_search_tool` | `true`（主模型按 Tool 描述自动搜索） |
| `max_images_per_request` | `4`（QQ Official 运行时固定上限 4） |

**访问控制（`access_settings`）**：`user_whitelist` / `user_blacklist` / `group_whitelist` / `group_blacklist`（空列表不限制）。

**高级设置（`advanced_settings`）**：超时、并发、媒体大小、重试、`save_media`、`debug_mode` 等。

`model_retry_count` 默认 `2`，覆盖搜索、生图、改图、模型目录和图片下载；`video_retry_count`
默认 `2`，覆盖视频创建、状态轮询和视频下载。两个值都是**首次请求之外**的额外重试次数，设为
`0` 即只请求一次。`retry_excluded_errors` 默认留空，表示所有远端 HTTP、网络、JSON 和远端响应
结构错误都可重试；可用英文逗号填写 HTTP 状态码或稳定错误码排除，例如
`400,401,403,404,422,auth_error,model_not_found,invalid_json,network_error`。每次尝试仍使用各自的
单次超时；视频不再使用插件侧的总等待上限，会持续轮询至远端返回完成或失败状态。

> `search_models` 默认顺序为 `grok-4.5,grok-4.3,grok-4.20-0309-reasoning,grok-4.20-0309-non-reasoning,grok-4.20-multi-agent-0309,grok-build-0.1,grok-chat-fast`，可自行按需调整。模型列表来自一次远端实例快照，实际可用性以你自己 Client Key 的 `GET /v1/models` 可见目录为准。`grok-chat-*` 不支持 X 搜索，启用 X 搜索时会保留 Web 搜索；如果只启用 X 搜索且候选全为 chat 模型，搜索能力会明确不可用。`search_reasoning_effort=auto` 或候选不支持所选强度时，插件会省略 `reasoning` 参数继续搜索，不会因此跳过模型。

模型通常通过 `GET /v1/models` 可见；`/g2状态` 可查看该 Client Key 可见的模型列表与搜索候选分区。

## 六个命令

| 命令 | 别名 | 权限 | 行为 |
|---|---|---|---|
| `/g2搜索 <问题>` | `/grok2搜索` | 访问规则 | 强制执行全局已启用的 Web/X 搜索，直接返回远端正文和来源，不调用本地 LLM 改写 |
| `/g2生图 [数量] <提示词>` | `/grok2生图` | 访问规则 | 生成 1 到配置上限张图片 |
| `/g2改图 <编辑要求>` | `/grok2改图` | 访问规则 | 编辑当前或回复消息中的第一张图片 |
| `/g2视频 [时长] [比例] <提示词>` | `/grok2视频` | 访问规则 | 创建、轮询、鉴权下载并发送视频 |
| `/g2状态` | `/grok2状态` | AstrBot ADMIN | 检查配置和 `/v1/models`，不泄露 Key |
| `/g2帮助` | `/grok2帮助` | 所有人 | 输出命令说明和当前能力 |

详见 [docs/commands.md](docs/commands.md)。

`/g2搜索` 只使用 `enable_web_search`、`enable_x_search` 与
`search_reasoning_effort` 的全局设置；两个搜索开关都关闭时会在请求前拒绝。它直接请求
grok2api 并发送远端结果。`grok2api_web_search` 则是给 AstrBot 主模型选择的 Tool：主模型
调用 Tool 后，仍会根据 Tool 结果组织最终回复。

## 限制与安全警告

- 搜索、生图、改图和视频请求默认会按重试配置重放；生成 POST 也可能被重放，可能造成重复生成或重复扣费。需要避免某类错误重试时，在 `retry_excluded_errors` 中显式排除对应状态码或错误码。
- 图片/视频会立即下载到本地再发送；原结果 URL 不作为永久存档。
- QQ Official 单次最多生成/发送 **4** 张图片；超出在调用 API 前拒绝。
- 改图只接受当前消息或回复链中的图片，不接受任意本地路径、用户输入 URL 或 `file_id`。
- 视频时长 1–15 秒；比例仅 `1:1`、`16:9`、`9:16`、`4:3`、`3:4`、`3:2`、`2:3`。
- `send_media_progress` 默认开启；生图、改图、视频在开始远端任务前各提示一次，提示发送失败不会取消任务。
- 发送异常时交付状态可能不确定，插件不自动重发。

## 排错

- `/g2状态` 无模型：检查 Client Key 权限与 `api_base_url`。
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
