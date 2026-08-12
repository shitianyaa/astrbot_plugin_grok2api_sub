# 多搜索模型与配置减负设计

## 目标

让搜索能力支持英文逗号分隔的有序模型候选列表，优先使用排在前面的可用模型，并在不产生模糊重复请求的前提下回退。同时将当前约 38 个扁平配置项直接收纳为 4 个分组。插件尚未发布，不实现旧配置兼容或迁移。

## 当前模型依据

2026-08-12 通过已配置的远端 grok2api `GET /v1/models` 拉到 11 个可见模型：

- 文本候选：`grok-chat-fast`、`grok-4.3`、`grok-4.5`、`grok-build-0.1`、`grok-4.20-0309-non-reasoning`、`grok-4.20-0309-reasoning`、`grok-4.20-multi-agent-0309`。
- 媒体模型：`grok-imagine-image`、`grok-imagine-image-lite`、`grok-imagine-image-quality`、`grok-imagine-video`。

`GET /v1/models` 只说明模型对当前 Client Key 可见，不等于所有 grok2api 实例都提供同一列表。grok2api 当前 Build、Console、Web 的 Responses 适配路径均存在 `web_search` 兼容逻辑，因此插件可以提供保守默认候选，但运行时仍以目标实例返回的模型列表为准。

默认搜索候选顺序：

```text
grok-4.5,grok-4.3,grok-4.20-0309-reasoning,grok-4.20-0309-non-reasoning,grok-4.20-multi-agent-0309,grok-build-0.1,grok-chat-fast
```

用户可以自行调整候选顺序。默认同时开启 `web_search` 和 `x_search`，并把
`search_reasoning_effort` 设为 `high`。

## 配置格式

新键名为 `search_models`，类型仍为 `string`：

```text
grok-4.5,grok-4.3,grok-4.20-0309-reasoning,grok-4.20-0309-non-reasoning,grok-4.20-multi-agent-0309,grok-build-0.1,grok-chat-fast
```

解析规则：

1. 只用英文逗号 `,` 分隔。
2. 去除每项两侧空白。
3. 忽略空项。
4. 大小写敏感、按首次出现保序去重。
5. 单项最长 255 个 Unicode 字符，总计最多 12 项。
6. 空字符串、纯空白或只有英文逗号的值解析为空 tuple，表示显式关闭搜索能力。
7. 中文逗号 `，` 不自动转换，明确报错，避免模型 ID 被错误拼接。
8. `enable_web_search` 与 `enable_x_search` 默认均为 true；二者都关闭时搜索能力不可用。
9. `search_reasoning_effort` 允许 `none/low/medium/high/xhigh`，默认 `high`。已知模型不支持时省略 `reasoning` 字段，未知自定义模型也不强加该字段。

运行时模型存储为：

```python
search_models: tuple[str, ...]
```

代码只接受新键 `search_models`，不读取或迁移旧的单值键。

## 有序选择流程

每次搜索遵循相同顺序：

1. 从配置读取候选列表。
2. 读取最多缓存 300 秒的 `GET /v1/models` 可见模型集合。
3. 模型列表获取成功时，按用户配置顺序筛出可见候选；不重新排序。
4. 配置项 `Build/grok-4.5` 之类带 Provider 前缀时，允许用最后一个 `/` 后的外部模型 ID 与 `/v1/models` 匹配，但实际 POST 仍发送用户原始配置值。
5. 模型列表获取失败时，不阻断搜索，保留完整配置顺序并直接尝试第一个候选。
6. 对候选逐个调用 `/v1/responses`；第一个成功且确认执行了 `web_search` 或 `x_search` 的模型立即返回。
7. 一次搜索结束后不永久改变配置顺序。下一次仍从第一候选开始，使恢复后的高优先级模型重新获得优先权。

模型目录缓存放在 `Grok2APIClient`，使用一个异步锁合并并发刷新。缓存 TTL 固定为 300 秒，不新增 WebUI 配置项；刷新失败不使用过期目录阻止搜索。

## 回退矩阵

| 结果 | 是否尝试下一模型 | 原因 |
|---|---:|---|
| 候选未出现在成功获取的 `/v1/models` | 是，不发送 POST | 明确不可见，无副作用 |
| HTTP 错误码 `model_not_found` | 是 | 明确模型不存在 |
| HTTP 错误码 `model_not_allowed` | 是 | 当前 Client Key 明确无该模型权限 |
| 请求完成但没有 completed `web_search_call` 或 `x_search_call` | 是 | 模型明确未完成所需搜索；记录一次降级 |
| 401 `invalid_api_key` / 通用认证失败 | 否 | 更换模型不能修复凭据 |
| 429、billing limit、quota exhausted | 否 | 防止扩大限流或额外用量 |
| 连接异常、读取超时 | 否 | 前一请求结果可能未知 |
| HTTP 5xx | 否 | 前一请求可能已提交 |
| 无效 2xx JSON | 否 | 前一请求可能已完成但结果丢失 |
| 其他 4xx、协议错误、内容安全拒绝 | 否 | 不应把请求或系统错误误判为模型不可用 |

如果全部候选均明确不可用，抛出稳定错误 `search_models_exhausted`，用户消息列出已尝试的模型名，但不包含查询正文、密钥或上游原始响应。

## HTTP 错误结构

插件当前把 401/403/404 压缩为通用错误，无法区分模型错误。transport 需要从 OpenAI 风格响应中只提取安全字段：

```json
{
  "error": {
    "code": "model_not_allowed"
  }
}
```

仅接受匹配 `[A-Za-z0-9_.-]{1,64}` 的 `error.code`，不记录或透传 `message`、响应体及未知字段。明确识别 `model_not_found`、`model_not_allowed`；其他错误继续使用现有稳定映射。

## 状态、帮助与日志

`/g2状态` 增加：

- 搜索候选配置顺序。
- 当前模型目录是否获取成功。
- 可见候选和不可见候选。
- 不执行真实搜索探针，不产生额外生成用量。

`/g2帮助` 仅显示“搜索：可用/未配置”，不展开模型列表。

安全日志在 `debug_mode=true` 时记录：

- `search_model_selected`：候选序号和模型名。
- `search_model_skipped`：稳定原因 `not_visible/model_not_found/model_not_allowed/search_not_performed`。
- `search_models_exhausted`：候选数量，不记录查询内容。

超时、5xx 和无效 2xx 沿用 ambiguous 日志与错误，禁止产生下一次 POST。

## 配置减负

WebUI 从约 38 个顶层配置项改成 4 个 `object` 分组：

1. `connection_settings`：启用、远端 API 地址、Client Key、TLS、可选代理。新安装的 `api_base_url` 和 `client_proxy_url` 默认都必须是空字符串，不内置任何本地服务地址或本地代理端口。
2. `capability_settings`：搜索模型列表、Web/X 搜索开关、思考强度、图片/改图/视频模型、LLM 搜索 Tool、来源显示、输出限制、视频分辨率、响应格式、单次图片数、视频进度提示。
3. `access_settings`：用户与群聊黑白名单。
4. `advanced_settings`：连接/请求/下载超时、媒体大小、并发、GET 重试、媒体保留、清理时间和 debug 日志。

未发布版本直接重构：

- `_conf_schema.json` 顶层只保留上述 4 个 `object`，不保留隐藏旧键，不增加 `config_schema_version`。
- `PluginConfig.from_astrbot()` 只读取新分组，不实现扁平键回退，不调用 `save_config()`。
- `connection_settings.api_base_url` 和 `client_api_key` 允许为空，使新插件在未配置远端服务时仍能正常初始化并响应帮助/状态命令。
- 搜索、图片、改图、视频的调用前检查必须把“未配置远端 API 地址”和“未配置 Client Key”作为稳定的能力不可用原因；`/g2状态` 在缺少任一连接项时不发出 `/v1/models` 请求。
- 新安装默认内置 `search_models` 候选列表；将其清空表示显式关闭搜索能力。

## 前置安全门禁

实现本功能前必须先处理前序复审遗留：

1. 吊销/轮换 `testignore/video_test.py` 曾写入磁盘的 Client Key。
2. 从仓库移除真实实测媒体与带凭据脚本，测试脚本只能读取环境变量。
3. 修复会话锁未回收。
4. 将 `debug_mode` 真正接入请求状态、耗时、attempt 日志。
5. 校验下载图片的真实格式与内容。
6. 合并搜索响应顶层 `citations`。

## 测试边界

- 配置解析：空项、空格、重复、中文逗号、超长、12 项边界、空远端配置可启动。
- 目录筛选：有缓存、缓存过期、并发刷新、Provider 前缀、目录请求失败。
- 回退：不可见、`model_not_found`、`model_not_allowed`、未执行搜索、全部耗尽。
- 禁止回退：401、429、5xx、超时、连接重置、无效 2xx、其他 4xx。
- 状态输出：有目录、目录失败、无候选；不泄漏查询和凭据。
- Schema：JSON 校验、顶层恰好 4 个 object、远端地址与代理默认空值、默认搜索模型顺序正确、无旧扁平键和迁移版本键。

## 非目标

- 不为生图、改图或视频增加多模型回退。
- 不做定时真实搜索健康探针。
- 不永久记忆“上次成功模型”并改变用户优先级。
- 不增加模型缓存 TTL、最大候选数或回退错误码的 WebUI 配置。
- 不内置本地 grok2api 地址或 `127.0.0.1:3067` 代理默认值。
- 不实现任何旧配置兼容、迁移或自愈回写；插件发布前直接采用最终配置结构。
