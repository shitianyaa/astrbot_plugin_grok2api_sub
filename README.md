# astrbot_plugin_grok2api_sub

> **社区非官方插件**：本项目与 xAI、Grok 和 grok2api 项目无隶属或背书关系。

通过 **grok2api Client Key** 提供联网搜索、文生图、改图和文生视频，并同时支持 **OneBot/NapCat** 与 **QQ Official** 双平台发送。

## 功能

- 手动命令联网搜索（强制 hosted web search）
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

在 AstrBot WebUI 插件配置页填写：

| 配置项 | 示例 |
|---|---|
| `api_base_url` | `https://grok.example.com` |
| `client_api_key` | `g2a_...`（仅 Client Key） |
| `client_proxy_url` | `http://127.0.0.1:3067`（可选代理） |
| `search_model` | `grok-build-0.1` 或 `grok-4.5` |
| `image_model` | `grok-imagine-image` |
| `image_edit_model` | `grok-imagine-image` |
| `video_model` | `grok-imagine-video` |

模型通常通过 `GET /v1/models` 可见；`/g2状态` 可查看该 Client Key 可见的模型列表。

## 六个命令

| 命令 | 别名 | 权限 | 行为 |
|---|---|---|---|
| `/g2搜索 <问题>` | `/grok2搜索` | 访问规则 | 强制执行联网搜索，返回正文和来源 |
| `/g2生图 [数量] <提示词>` | `/grok2生图` | 访问规则 | 生成 1 到配置上限张图片 |
| `/g2改图 <编辑要求>` | `/grok2改图` | 访问规则 | 编辑当前或回复消息中的第一张图片 |
| `/g2视频 [时长] [比例] <提示词>` | `/grok2视频` | 访问规则 | 创建、轮询、鉴权下载并发送视频 |
| `/g2状态` | `/grok2状态` | AstrBot ADMIN | 检查配置和 `/v1/models`，不泄露 Key |
| `/g2帮助` | `/grok2帮助` | 所有人 | 输出命令说明和当前能力 |

详见 [docs/commands.md](docs/commands.md)。

## 限制与安全警告

- 生成类 POST 遇到结果不确定的网络失败时 **不会自动重试**，避免重复生成/扣费。
- 图片/视频会立即下载到本地再发送；原结果 URL 不作为永久存档。
- QQ Official 单次最多生成/发送 **4** 张图片；超出在调用 API 前拒绝。
- 改图只接受当前消息或回复链中的图片，不接受任意本地路径、用户输入 URL 或 `file_id`。
- 视频时长 1–15 秒；比例仅 `1:1`、`16:9`、`9:16`、`4:3`、`3:4`、`3:2`、`2:3`。
- 发送异常时交付状态可能不确定，插件不自动重发。

## 排错

- `/g2状态` 无模型：检查 Client Key 权限与 `api_base_url`。
- 401/403：Client Key 无效或权限不足。
- 404：`api_base_url` 或 endpoint 错误。
- 搜索无 `web_search_call`：上游未执行联网搜索，插件明确提示。

## 开发

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
ruff check .
```

详见 [docs/testing.md](docs/testing.md) 与 [docs/architecture.md](docs/architecture.md)。

## License

MIT，见 [LICENSE](LICENSE)。