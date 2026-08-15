# Grok2API Sub

<div align="center">

<a href="https://github.com/shitianyaa/astrbot_plugin_grok2api_sub/releases"><img alt="Version" src="https://img.shields.io/badge/version-0.2.0-22c55e?style=for-the-badge" /></a>
<a href="https://github.com/Soulter/AstrBot"><img alt="AstrBot" src="https://img.shields.io/badge/AstrBot-plugin-5865f2?style=for-the-badge" /></a>
<img alt="Python" src="https://img.shields.io/badge/Python-3.10+-3776ab?style=for-the-badge&logo=python&logoColor=white" />
<img alt="Platform" src="https://img.shields.io/badge/platform-OneBot%20%2F%20QQ%20Official-f97316?style=for-the-badge" />
<a href="https://github.com/shitianyaa/astrbot_plugin_grok2api_sub/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-3b82f6?style=for-the-badge" /></a>

<br />

<a href="https://github.com/shitianyaa/astrbot_plugin_grok2api_sub/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/shitianyaa/astrbot_plugin_grok2api_sub?style=for-the-badge&color=gold" /></a>
<a href="https://github.com/shitianyaa/astrbot_plugin_grok2api_sub"><img alt="Last Commit" src="https://img.shields.io/github/last-commit/shitianyaa/astrbot_plugin_grok2api_sub?style=for-the-badge" /></a>
<a href="https://qm.qq.com/q/cPQnFNtdN6"><img alt="QQ Group" src="https://img.shields.io/badge/QQ%E7%BE%A4-Bot%E6%B5%8B%E8%AF%95%E7%BE%A4-12B7F5?style=for-the-badge&logo=tencentqq&logoColor=white" /></a>

<br />

<img src="logo.png" alt="Grok2API Sub Logo" width="180" />

<br />

<img src="https://count.getloli.com/@astrbot-plugin-grok2api-sub?name=astrbot-plugin-grok2api-sub&theme=booru-jaypee&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto" alt="count" />

<p>
基于 grok2api 的全功能助手：支持 Web/X 联网搜索、AI 生图改图、视频生成及可视化管理面板与定时推送。
</p>

</div>

> [!NOTE]
> **社区非官方插件**：本项目与 xAI、Grok 无隶属或背书关系。
> 插件已在 [chenyme/grok2api](https://github.com/chenyme/grok2api) 项目上完成测试，理论兼容任何实现了相同接口的 grok2api 兼容服务。

---

## 目录

- [功能一览](#功能一览)
- [快速开始](#快速开始)
- [常用指令](#常用指令)
- [配置说明](#配置说明)
- [管理面板与定时推送](#管理面板与定时推送)
- [限制与安全规范](#限制与安全规范)
- [排错与常见问题](#排错与常见问题)
- [交流与支持](#交流与支持)
- [本地开发](#本地开发)
- [致谢与许可证](#致谢与许可证)

---

## 功能一览

| 场景 | 能力与行为 |
|---|---|
| 实时搜索 | 手动指令 `/g2搜索` 强制联网检索；大模型 Tool（`grok2api_web_search`）会话级自动调用；默认 Web/X 双引擎检索，由会话模型整理正文并追加引用来源 |
| 生图与改图 | 单/多张文生图；基于附图或回复链消息的局部改图；支持原文直传、参数提取与 LLM 提示词优化 |
| 视频生成 | 文生短视频与参考图引导生视频；自动将输入图片尺寸对齐到最近的合法比例（`16:9`、`9:16`、`4:3` 等） |
| 监控看板 | `/g2面板` 聚合账号池、媒体库与审计趋势；经 AstrBot T2I 渲染输出 1080p 磨砂玻璃卡片；支持会话定时订阅与 Cron 推送 |
| 平台适配 | 原生适配 **OneBot / aiocqhttp / NapCat** 与 **QQ Official** 双平台 |
| 访问与安全 | API Key 与敏感日志脱敏、用户/群聊黑白名单、模型错误退避重试、临时媒体文件生命周期清理 |


---

## 快速开始

### 1. 安装插件

在 AstrBot WebUI 插件市场搜索安装，或在 `plugins/` 目录手动克隆：

```bash
git clone https://github.com/shitianyaa/astrbot_plugin_grok2api_sub
cd astrbot_plugin_grok2api_sub
python -m pip install -r requirements.txt
```

### 2. 准备 grok2api 接入

- 准备一个兼容 grok2api 协议的服务（自部署 [chenyme/grok2api](https://github.com/chenyme/grok2api) 实例或第三方兼容站点）。
- 获取你的 **API Key** 以及服务 **API Base URL**（如 `https://grok.example.com`，不带 `/v1`）。
- 仅当需要使用 `/g2面板` 时，才需额外填写管理面账号密码（`admin_username` / `admin_password`）。

---

## 常用指令

| 指令 | 别名 | 权限 | 说明 |
|---|---|:---:|---|
| `/g2搜索 <问题>` | `/grok2搜索` | 访问规则 | 强制执行联网搜索并整理输出（附带来源引用） |
| `/g2生图 <提示词>` | `/grok2生图` | 访问规则 | 文本生成图片，每次 1 张（默认 1K 分辨率） |
| `/g2改图 <要求>` | `/grok2改图` | 访问规则 | 编辑当前消息或回复消息中的第一张图片 |
| `/g2视频 [参数] <提示词>` | `/grok2视频` | 访问规则 | 生成短视频；支持附图/回复图或 `--image-url=<URL>` 参考图 |
| `/g2面板` | `/grok2面板` | ADMIN | 渲染输出管理面板可视化卡片（需自部署管理端） |
| `/g2面板订阅` | `/grok2面板订阅` | ADMIN | 为当前会话订阅定时面板大盘推送 |
| `/g2面板退订` | `/grok2面板退订` | ADMIN | 退订当前会话的定时面板推送 |
| `/g2面板订阅列表` | `/grok2面板订阅列表` | ADMIN | 查看当前已订阅的会话状态与数量统计 |
| `/g2帮助` | `/grok2帮助` | 所有人 | 查看命令使用说明与当前可用能力 |

> [!TIP]
> 完整参数选项与调用示例请查阅 [命令详细说明文档](docs/commands.md)。

---

## 配置说明

在 AstrBot 管理面板的插件配置页中，按如下模块灵活调整：

### 1. 连接设置（`connection_settings`）

| 配置项 | 默认值 | 说明 |
|---|:---:|---|
| `enabled` | `true` | 插件全局总开关 |
| `api_base_url` | `""` | 远端 grok2api 根地址（如 `https://grok.example.com`，不要带 `/v1`） |
| `api_key` | `""` | 接入凭据 API Key |
| `admin_username` / `admin_password` | `""` | 管理面只读查询凭据（仅 `/g2面板` 需要，与 API Key 互相独立） |
| `verify_tls` | `true` | 是否校验 TLS 证书（生产环境建议开启） |
| `client_proxy_url` | `""` | 出站代理地址（如 `http://127.0.0.1:7890`，留空为直连） |

### 2. 能力与模型设置（`capability_settings`）

| 配置项 | 默认值 | 说明 |
|---|:---:|---|
| `search_models` | 多行列表 | 搜索候选模型列表（上方优先，首选 `grok-chat-fast` 等） |
| `enable_web_search` | `true` | 是否启用 Web 联网搜索 |
| `enable_x_search` | `true` | 是否启用 X/Twitter 平台搜索（chat 模型会自动降级为纯 Web） |
| `search_reasoning_effort` | `auto` | 搜索推理强度（`auto`/`none`/`low`/`medium`/`high`/`xhigh`） |
| `image_models` | 多行列表 | 文生图候选模型（首选 `grok-imagine-image-lite`） |
| `image_edit_models` | 多行列表 | 改图候选模型（首选 `grok-imagine-image`） |
| `video_models` | 多行列表 | 生视频候选模型（首选 `grok-imagine-video`） |
| `enable_llm_search_tool` | `true` | 是否将会话级联网 Tool（`grok2api_web_search`）注册给主模型 |

### 3. 提示词处理（`prompt_processing`）

- **`mode`**：`off`（原文直传）、`extract`（仅补全结构化参数）、`enhance`（调用大模型重写优化提示词）。
- **`extract_provider_id` / `enhance_provider_id`**：指定用于整理/优化的已配置 AstrBot 文本模型。
- **`disable_prompt_processing_with_reference_image`**：在有参考图时跳过 LLM 提示词改写，保持原样直传。

---

## 管理面板与定时推送

`/g2面板` 支持通过 AstrBot 配置的 T2I 服务（HTML-to-Image）渲染出 **1920x1080**（或 720p/1440p）高品质毛玻璃卡片：

- **智能多图源背景**：自动在 Wallhaven（动漫/SFW）、LoliAPI 横屏、t.alcy 等图源中轮询随机壁纸，校验横向比例并具备本地缓存回退能力。
- **自动化定时推送**：支持 Cron 表达式与对齐每日 00:00 的时间间隔（Interval），具备同分钟去重调度保护。

> [!NOTE]
> `/g2面板` 需要远端实例支持 `/api/admin/v1/...` 管理端点。若使用的是不带管理端的公共/第三方 API，仅面板功能不可用，搜索与媒体功能完全正常。

---

## 限制与安全规范

1. **凭据安全**：插件严禁在日志中记录 API Key、管理员密码、Base64 数据或原始响应正文；面向用户的错误提示均经过脱敏收敛。
2. **多模态自愈**：图生视频/附图请求会自动将用户图片的实际宽高比映射到最近的合法比例（`1:1`、`16:9`、`9:16`、`4:3`、`3:4`、`2:3`、`3:2`）。
3. **重试与回退**：仅在当前模型的重试次数耗尽后，若遇到 `model_not_found` 等稳定不可用错误，才会触发回退至下一候选模型。
4. **QQ Official 平台限制**：单次最多发送 4 张图片，超出将在调用前拦截提示。

---

## 排错与常见问题

- **面板显示“未获取”**：检查 `admin_username` 与 `admin_password` 是否正确配置，且 `api_base_url` 是否支持管理面 API。
- **401 / 403 鉴权错误**：确认配置项中的 `api_key` 是否有效且具有对应模型调用权限。
- **改图返回 404**：请确认改图模型列表中未包含不支持图生图的 `lite` 轻量模型（默认使用 `grok-imagine-image`）。
- **搜索无结果**：检查上游是否触发了 `search_not_performed`，插件会自动按配置重试或回退。

---

## 交流与支持

欢迎加入 QQ 交流群探讨插件使用、反馈问题或交流 AstrBot 玩法：

<p align="center">
  <a href="https://qm.qq.com/q/cPQnFNtdN6">
    <img src="https://img.shields.io/badge/QQ%E7%BE%A4-Bot%E6%B5%8B%E8%AF%95%E7%BE%A4-12B7F5?style=for-the-badge&logo=tencentqq&logoColor=white" alt="QQ 群" />
  </a>
</p>

---

## 本地开发

运行测试与代码门禁：

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
ruff check .
ruff format --check .
```

详见 [架构设计文档](docs/architecture.md) 与 [测试说明文档](docs/testing.md)。

---

## 致谢与许可证

- [chenyme/grok2api](https://github.com/chenyme/grok2api)：兼容 API 与管理面 API 的基础架构支持。
- [Xyanxhu](https://github.com/Xyanxhu)：管理面板卡片视觉设计参考。
- [PeeGayhub Telegram 表情包系列](https://t.me/addstickers/PeeGayhub)：插件 Logo 风格灵感来源。

本项目采用 [MIT 许可证](LICENSE)。
