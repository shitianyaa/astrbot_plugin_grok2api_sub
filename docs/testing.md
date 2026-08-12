# 测试与验证

## 本地单元测试

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
$env:PYTHONIOENCODING='utf-8'
python -m json.tool _conf_schema.json
python -m compileall main.py core tests
python -m pytest -q
ruff check .
ruff format --check .
```

## 测试文件

| 文件 | 覆盖 |
|---|---|
| `tests/test_config.py` | 4 分组配置解析、`search_models` 解析（去空白/去重/上限/中文逗号拒绝）、Web/X 开关与思考强度、默认值、脱敏 |
| `tests/test_schema.py` | `_conf_schema.json` 顶层恰好 4 个 `object` 分组、远端默认空、默认模型顺序稳定 |
| `tests/test_transport.py` | 认证头、同源相对路径、重试矩阵、`.part` 原子下载、Retry-After、安全模型错误码提取（64 KiB 有界、只保留 model_not_found/model_not_allowed） |
| `tests/test_search.py` | Responses Web/X 搜索工具组合、`reasoning` 请求契约与 parser |
| `tests/test_search_models.py` | `catalog_model_id` Provider 前缀归一、`partition_visible_models` 保序分区、模型思考强度映射、大小写敏感 |
| `tests/test_client_models.py` | 目录缓存 300s TTL、并发单 GET、force_refresh、过期失败抛原错、去重排序 |
| `tests/test_client_images.py` | 生图/改图路径、b64/url 格式、可配置重试与协议校验 |
| `tests/test_client_video.py` | 视频创建/轮询/鉴权下载、request_id 校验 |
| `tests/test_media.py` | 路径安全、图片归一化、解压炸弹、清理、archive/ 归档 |
| `tests/test_access.py` / `test_platform.py` | 黑白名单、平台识别 |
| `tests/test_sender.py` | OneBot/QQ Official 发送、限值、不重发 |
| `tests/test_service.py` | 预检、并发、会话锁、清理、状态脱敏、**有序搜索回退矩阵**（3 类可切换/7 类禁止切换/耗尽/重启优先）、**状态候选分区不执行搜索 POST** |
| `tests/test_observability.py` | 日志脱敏、trace_id 传播、白名单字段、debug 开关 |
| `tests/test_runtime_wiring.py` | 配置注入 transport/client/media 的运行时接线 |
| `tests/test_main_commands.py` | 命令注册参数模型（GreedyStr）、star_handlers_registry 验证 |
| `tests/test_tools.py` | Tool 策略（search_models 空/非空）、JSON 输出、不直接发送 |

## Fake 设计

- `tests/fakes.py` 提供 `FakeSession`/`FakeResponse`/`StreamReader`，无需真实网络。
- 测试中禁止出现真实 Client Key；一律使用 `g2a_test_key` 之类的占位符。

## 真实环境验收矩阵

使用专门测试 Client Key 和测试群，不在测试记录中粘贴 Key。

| 场景 | OneBot 私聊 | OneBot 群聊 | QQ Official C2C | QQ Official 群聊 |
|---|---:|---:|---:|---:|
| `/g2搜索` 正文+来源 | 必测 | 必测 | 必测 | 必测 |
| 主模型自动选择搜索 Tool | 必测 | 必测 | 必测 | 必测 |
| `/g2生图 1` | 必测 | 必测 | 必测 | 必测 |
| `/g2生图 4` | 抽测 | 必测 | 抽测 | 必测 |
| `/g2生图 5` | 受配置上限 | 受配置上限 | 必测拒绝 | 必测拒绝 |
| 当前消息附图 `/g2改图` | 必测 | 必测 | 必测 | 必测 |
| 回复图片 `/g2改图` | 必测 | 必测 | 必测 | 必测 |
| 文生视频 | 必测 | 必测 | 必测 | 必测 |
| 图片引导视频 | 抽测 | 必测 | 抽测 | 必测 |
| 黑名单拒绝且无 API 请求 | 必测 | 必测 | 必测 | 必测 |

## 失败验收矩阵

- 401/403：提示 Client Key/权限错误，不打印 Key。
- 404：区分 base URL/endpoint 和 video job 不存在。
- 429/503：所有远端请求按所属重试组退避；`Retry-After` 优先，排除列表可禁止重试。
- 生成 POST read timeout：按所属重试组重试；可能重复生成或扣费，配置排除项可停止重试。
- Responses 无完成态 `web_search_call` 或 `x_search_call`：当前模型按 `model_retry_count` 耗尽后，再继续候选回退。
- 视频 failed、单次状态查询超时、下载超限：各有单一、可理解回复，临时文件清理。
- 输入图片损坏、超限、解压炸弹：在调用 API 前拒绝。
- QQ Official 5 图请求：在调用 API 前拒绝。
- QQ 发送异常：不重发，日志标记 `delivery_unknown`。
- 插件重载：Tool 不重复注册，HTTP session、任务和临时文件正确清理。

## 未验证项

真实平台（OneBot / QQ Official 四类会话）的验收需在 AstrBot 运行环境中执行，
单测不能代替真实平台验证。
