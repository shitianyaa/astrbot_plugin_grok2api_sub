# 架构

## 模块依赖

```text
main.py -> config / access / service / tools / sender / platform
service.py -> client / media / models / errors / access / parser
client.py -> transport / parsers / models / errors
sender.py -> platform / models + AstrBot message components
models.py / errors.py / parsers.py -> 不依赖 AstrBot
```

- `main.py` 只保留生命周期、命令装饰器和 LLM Tool 暴露策略。
- 业务层（`service.py`、`sender.py`）不直接调用 OneBot action，也不直接调用
  QQ OpenAPI；所有 HTTP 都经由 `transport.py`，所有发送都经由 `sender.py`。

## 联网搜索的双层决策

1. AstrBot 主模型根据 `grok2api_web_search` 的 Tool 描述决定是否调用。
2. Tool 一旦被调用，内部请求固定 `tool_choice: "required"`。
3. grok2api 响应中若没有完成态 `web_search_call`，返回 `SearchNotPerformedError`，
   不把普通模型回答伪装成联网结果。

`required` 的跨 Provider 行为并不完全相同：Build 可强制 hosted tool，Console 在
仅有 web search 时可能降级为 auto。因此模型字段允许填写 `Build/<model>` 锁定
Provider；无论选哪个 Provider，插件都以完成态 `web_search_call` 作为联网成功的
最终证据。

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
- `save_media=false` 时发送后删除；`true` 时保留成功文件。
- 启动时清理超过 `temp_retention_hours` 的插件生成文件和 `.part`。
- 路径通过 `Path.resolve().relative_to(root)` 校验，阻止越界删除。