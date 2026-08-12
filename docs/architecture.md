# 架构

## 模块依赖

```text
main.py -> config / access / service / tools / sender / platform / observability
service.py -> client / media / models / errors / access / parser / observability
client.py -> transport / parsers / models / errors
sender.py -> platform / models / observability + AstrBot message components
models.py / errors.py / parsers.py / observability.py -> 不依赖 AstrBot
```

- `main.py` 只保留生命周期、命令装饰器和 LLM Tool 暴露策略。
- 业务层（`service.py`、`sender.py`）不直接调用 OneBot action，也不直接调用
  QQ OpenAPI；所有 HTTP 都经由 `transport.py`，所有发送都经由 `sender.py`。

## 联网搜索的双层决策

1. AstrBot 主模型根据 `grok2api_web_search` 的 Tool 描述决定是否调用。
2. Tool 一旦被调用，默认同时传入 `web_search` 与 `x_search`；任一开关可单独关闭。
3. 至少存在一个搜索工具时，内部请求固定 `tool_choice: "required"`。
4. grok2api 响应中若没有完成态 `web_search_call` 或 `x_search_call`，返回 `SearchNotPerformedError`，
   不把普通模型回答伪装成联网结果。

`required` 的跨 Provider 行为并不完全相同：Build 可强制 hosted tool，Console 在
仅有 web search 时可能降级为 auto。因此模型字段允许填写 `Build/<model>` 锁定
Provider；无论选哪个 Provider，插件都以完成态 `web_search_call` 或 `x_search_call` 作为联网成功的
最终证据。

## 多模型搜索回退矩阵

`capability_settings.search_models` 按英文逗号、左侧优先配置有序候选。每次搜索
都从配置第一项开始，**不**根据历史成功率/延迟/费用动态排序，**不**把成功模型
写回配置。

允许切换到下一候选的失败只有 3 类：

| 失败 | 错误码 | 行为 |
|---|---|---|
| 模型不在目录 | `not_visible` | 跳过，不发 POST |
| 搜索模型不存在 | `model_not_found` | 切换到下一候选 |
| 无权使用该模型 | `model_not_allowed` | 切换到下一候选 |
| 模型未执行联网搜索 | `search_not_performed` | 切换到下一候选 |

**禁止切换**的失败（立即抛出不重试、不切换）：401/403 鉴权（`auth_error`）、429
限流、HTTP 5xx、无效 2xx JSON、网络错误/超时、`AmbiguousSubmissionError`、
`ProtocolError`。全部候选耗尽时抛 `search_models_exhausted`。

注意事项：

- `/v1/models` 目录只证明**可见性**，不证明搜索能力；完成态 `web_search_call`
  或 `x_search_call` 才证明本次执行了联网搜索。
- 目录 GET 失败时回退到"原配置顺序"（不跳过 not_visible）。
- 实际搜索 POST 始终发送用户配置的原字符串（`Build/grok-4.5` 不会被重写为
  `grok-4.5`），Provider 前缀只用于目录可见性匹配。
- `search_reasoning_effort` 默认 `high`。插件只向已知支持该值的候选传入
  `reasoning.effort`；不支持或未知的模型省略该字段，不以此触发模型回退。

## 平台发送边界

- OneBot/NapCat：图片可放入一个多图 `MessageChain`；视频用 `Video.fromFileSystem`。
- QQ Official：图片逐张独立发送，单次最多 4 张；不构造 `Node/Nodes`、合并转发、
  不调用 `/v2/groups/.../files` 或 `/v2/users/.../files`，不保存 QQ 官方凭据。
- 发送抛错后的交付状态可能不确定，插件不自动重发同一媒体。

## 视频状态机

```
create_video -> request_id -> wait_for_video (poll) -> done/failed
   -> download /v1/videos/{id}/content (.part, 原子改名) -> send_video
```

- 轮询支持 `pending/done/failed`，progress 限制到 0–100。
- 总等待上限 `video_max_wait_seconds`；超时不取消上游任务，提示可稍后由管理员查看。
- 完成后忽略响应里的绝对 `video.url` 主机，固定向配置的 base URL 请求 content。

## 清理生命周期

- 所有生成媒体落在 `StarTools.get_data_dir(plugin_name)/workspace`。
- `save_media=false` 时发送后删除（成功/失败都删）；`true` 时成功文件移到
  `workspace/archive/` 子目录保留，启动清理跳过 `archive/`。
- 启动时清理超过 `temp_retention_hours` 的 workspace 根目录临时文件和 `.part`。
- 路径通过 `Path.resolve().relative_to(root)` 校验，阻止越界删除。
