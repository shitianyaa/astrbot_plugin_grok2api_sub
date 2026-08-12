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

## 手动命令与 Tool 的边界

- `/g2搜索` 不经过 AstrBot 主模型。它直接向 grok2api 发送查询，固定要求远端执行至少一个
  当前全局启用的 Web/X 搜索工具，并将远端正文按来源展示配置发送给用户。
- `enable_web_search`、`enable_x_search`、`search_reasoning_effort` 是两条路径共享的全局设置；
  两个搜索开关都关闭时，命令和 Tool 都在发请求前拒绝。
- `grok2api_web_search` 是给 AstrBot 主模型选择的 FunctionTool。主模型决定是否调用；调用后 Tool
  同样强制远端搜索，但只返回受 `show_search_sources` 与 `max_search_sources` 限制的结果，最终用户回复仍由主模型组织。

## 多模型搜索回退矩阵

`capability_settings.search_models` 按英文逗号、左侧优先配置有序候选。每次搜索
都从配置第一项开始，**不**根据历史成功率/延迟/费用动态排序，**不**把成功模型
写回配置。

允许切换到下一候选的失败只有 3 类：

| 失败 | 错误码 | 行为 |
|---|---|---|
| 模型不在目录 | `not_visible` | 跳过，不发 POST |
| 搜索模型不存在 | `model_not_found` | 当前候选重试耗尽后切换到下一候选 |
| 无权使用该模型 | `model_not_allowed` | 当前候选重试耗尽后切换到下一候选 |
| 模型未执行联网搜索 | `search_not_performed` | 当前候选重试耗尽后切换到下一候选 |

远端 HTTP、网络、JSON、远端结构错误和 `search_not_performed` 都会先在当前候选上按
`model_retry_count` 重试。重试耗尽后，只有上表的三类候选级结果会继续下一候选；其他错误立即
向上抛出。`retry_excluded_errors` 只会跳过当前候选的重试，不会取消这三类候选回退。全部候选耗尽时
抛 `search_models_exhausted`。

注意事项：

- `/v1/models` 目录只证明**可见性**，不证明搜索能力；完成态 `web_search_call`
  或 `x_search_call` 才证明本次执行了联网搜索。
- 目录 GET 失败时回退到"原配置顺序"（不跳过 not_visible）。
- 实际搜索 POST 始终发送用户配置的原字符串（`Build/grok-4.5` 不会被重写为
  `grok-4.5`），Provider 前缀只用于目录可见性匹配。
- `grok-chat-*` 不支持 X 搜索：插件会在每个候选模型发起请求前移除 `x_search`，
  保留已启用的 Web 搜索；若因此没有任何可用工具则跳过该候选，不发送 Responses 请求。
- `search_reasoning_effort` 默认 `high`，也可设为 `auto`。`auto`、不支持或未知的模型
  都省略 `reasoning.effort`，由远端选择或使用默认值，不以此触发模型回退。

## 平台发送边界

- OneBot/NapCat：图片可放入一个多图 `MessageChain`；视频用 `Video.fromFileSystem`。
- QQ Official：图片逐张独立发送，单次最多 4 张；不构造 `Node/Nodes`、合并转发、
  不调用 `/v2/groups/.../files` 或 `/v2/users/.../files`，不保存 QQ 官方凭据。
- 发送抛错后的交付状态可能不确定，插件不自动重发同一媒体。

## 媒体进度与日志

- `send_media_progress` 默认开启。生图、改图、视频取得同会话任务锁后各发送一次进度提示；提示本身
  发送失败只记录安全日志，不取消已经接受的远端任务。
- 每个媒体任务记录开始、完成或失败事件，字段限于操作类型、模型、数量、耗时、安全 request ID、
  错误码和异常类型。日志不包含提示词、图片内容、完整 URL 或凭据。
- `debug_mode=true` 时，JSON HTTP 的每次尝试额外记录 method、相对 path、attempt、status、
  elapsed_ms 和 retryable；网络失败以 `status=0` 记录。

## 远端重试边界

- `model_retry_count` 管理搜索、生图、改图、模型目录和图片下载；`video_retry_count` 管理视频创建、
  视频状态轮询和视频下载。两项均表示首次调用以外的额外次数，默认 `2`。
- `retry_excluded_errors` 为空时，远端 HTTP、网络、JSON 解析和远端响应结构错误均允许重试；
  可按 HTTP 状态码或稳定错误码关闭特定重试。`Retry-After` 仍优先于指数退避。
- 生成 POST 也遵循此策略，因此可能产生重复生成或重复扣费。平台发送、访问控制、用户输入、媒体大小
  与路径安全错误均位于传输层之外，绝不自动重放。
- 每一次 HTTP 尝试只使用其操作自己的单次总超时。重试不裁剪单次超时，也不由视频轮询生命周期
  重新分配时间预算。

## 视频状态机

```
create_video -> request_id -> wait_for_video (poll) -> done/failed
   -> download /v1/videos/{id}/content (.part, 原子改名) -> send_video
```

- 轮询支持 `pending/done/failed`，progress 限制到 0–100。
- 每次状态查询使用 `video_poll_timeout_seconds`；处于 `pending` 时按
  `video_poll_interval_seconds` 继续轮询，直到远端返回 `done` 或 `failed`。没有插件侧总等待上限。
- 完成后忽略响应里的绝对 `video.url` 主机，固定向配置的 base URL 请求 content。

## 清理生命周期

- 所有生成媒体落在 `StarTools.get_data_dir(plugin_name)/workspace`。
- `save_media=false` 时发送后删除（成功/失败都删）；`true` 时成功文件移到
  `workspace/archive/` 子目录保留，启动清理跳过 `archive/`。
- 启动时清理超过 `temp_retention_hours` 的 workspace 根目录临时文件和 `.part`。
- 路径通过 `Path.resolve().relative_to(root)` 校验，阻止越界删除。
