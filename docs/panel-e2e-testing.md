# 面板全链路测试

验证路径：

```text
AstrBot 管理员命令
  -> grok2api 管理面只读聚合
  -> 多源背景（Wallhaven / LoliAPI / t.alcy / 缓存 / 默认）
  -> AstrBot 全局 HTML-to-image
  -> 图片校验
  -> 当前会话或定时 UMO 发送
```

`/g2面板` 不调用 LLM，也不要求 `api_key`。管理密码、API Key、管理 JWT、完整 UMO 和远端原始响应均不得写入文档、终端输出或日志。

## 1. 前置条件

- AstrBot 已加载本插件，发送平台已在线。
- 发出命令的账号拥有 AstrBot `ADMIN` 权限。
- AstrBot 全局已配置可用的 HTML-to-image 服务。插件调用 `Star.html_render()`，不保存 T2I 地址或凭据，也不使用 Playwright。
- grok2api 管理面可从 AstrBot 主机访问；管理账号仅有读取聚合数据所需的权限。
- 若需经代理访问管理面或背景源，代理已在本机可用；背景 API 与图片下载统一沿用同一显式代理配置。

## 2. WebUI 最小配置

在 AstrBot 插件配置中设置以下字段，然后保存并重载插件：

| 分组 | 字段 | 测试值 / 要求 |
|---|---|---|
| `connection_settings` | `enabled` | `true` |
| `connection_settings` | `api_base_url` | grok2api 根地址，不带 `/v1` |
| `connection_settings` | `admin_username` / `admin_password` | 管理面登录凭据；不填入 API Key、JWT 或 SSO/OAuth |
| `connection_settings` | `verify_tls` | 正式证书使用 `true` |
| `connection_settings` | `client_proxy_url` | 需要代理时填写；管理面和背景请求共用 |
| `advanced_settings` | `panel_period` | 选 `24h`、`7d`、`30d` 或 `90d`；默认 `7d` |
| `advanced_settings` | `panel_sections` | 首次全选：账号池、图片库、视频库、请求审计汇总、按模型统计 |
| `advanced_settings` | `panel_t2i_enabled` | `true`，用于图片路径；后续关闭以验证文本回退 |
| `advanced_settings` | `panel_resolution` | 默认 `1080p`；也可选 `720p` 或 `1440p` |
首次验证不要填写 `panel_push_targets`，也不要启用 Cron 或间隔推送，避免向非测试会话发送。

## 3. 命令全链路验收

在测试私聊或测试群中以管理员身份发送：

```text
/g2面板
```

通过标准：

- 默认收到一张 1920x1080 的 JPEG 面板图，而不是 LLM 改写后的文字；尺寸应与 `panel_resolution` 一致。
- 图中只包含在 `panel_sections` 勾选的数据块；账号、媒体、审计和模型统计为聚合值，不出现邮箱、API Key 名、请求 ID 或原始审计记录。
- 审计卡应显示汇总请求、成功/失败、Token、计费请求和计费 Token；右侧“请求行为”卡显示请求类型、Provider、计量来源、流式/重试、工具和媒体输出。`审计行覆盖 X/Y` 中，`Y` 是 summary 请求数，`X` 是列表接口实际可读的安全审计行数；两者不同属于接口覆盖范围差异，不是插件将 X 误算为 Y。
- 背景在玻璃卡片外部可见；文字无溢出、遮挡或空白占位。
- DEBUG 日志出现 `panel_background_ready`，字段 `background_source` 为 `fresh`、`cache` 或 `default`，并包含 `background_provider`；远程图命中时还包含清理后的 `background_image_name`。DEBUG 日志按失败图源输出 `panel_background_provider_failed`；所有日志都不应出现凭据、完整 UMO、下载 URL、query 参数或原始管理数据。

分别取消勾选一个数据块、重载配置并重复 `/g2面板`。通过标准是对应块不显示，且该块不发起管理请求。将 `panel_sections` 全部取消后再发送命令，应返回“未启用任何面板数据块”，且不发起管理请求。

## 4. T2I 与文本回退

先保留 `panel_t2i_enabled=true`，执行一次 `/g2面板`，确认图片路径成功。

随后将 `panel_t2i_enabled=false`，保存并重载插件，再执行：

```text
/g2面板
```

通过标准：收到与图片相同统计周期、相同已选数据块的纯文本面板。此路径不请求背景源，也不调用 T2I。

恢复 `panel_t2i_enabled=true` 后重载。若全局 T2I 服务不可用、返回空文件或错误内容，插件应记录 `panel_render_failed`，并发送同一份 `PanelReport` 的纯文本；不能重新拉取管理数据，也不能把错误 JSON/HTML 当作图片发送。

## 5. 背景回退

连续执行两次 `/g2面板`。正常情况下每次都会随机打乱 Wallhaven、LoliAPI、t.alcy 的尝试顺序，单个图源失败后继续尝试剩余图源。

| 场景 | 预期日志 | 预期图片 |
|---|---|---|
| 刷新并下载成功 | `background_source=fresh` | 使用新背景 |
| 暂时阻断多源或图片下载，已有缓存 | `background_source=cache` | 复用最近有效背景 |
| 暂时阻断多源，且无有效缓存 | `background_source=default` | 使用卡片内置默认背景，仍继续 T2I |

验证缓存时仅对测试环境短暂阻断多源背景。不要删除生产插件数据目录；缓存文件由插件管理。

## 6. 定时发送

定时发送可用固定 UMO 或命令订阅，两类目标会合并去重。相同目标在同一自然分钟最多发送一次；Cron 与间隔可以同时启用。

### 当前会话订阅

在目标测试会话以管理员身份依次发送：

```text
/g2面板订阅
/g2面板订阅列表
```

预期列表显示当前会话已订阅、命令订阅会话数和固定配置目标数，但不显示完整 UMO。测试结束后发送：

```text
/g2面板退订
```

### 固定目标与 Cron

在 `advanced_settings.panel_push_targets` 中仅添加测试 UMO，并启用：

| 字段 | 建议测试值 |
|---|---|
| `panel_cron_enabled` | `true` |
| `panel_cron_expression` | 选择未来 2 至 3 分钟内的一次五段 Cron，例如 `15 10 * * *` |
| `panel_interval_enabled` | 首次 Cron 测试时 `false` |

保存并重载后等待触发。通过标准：目标会话收到一张面板图，日志依次出现 `panel_push_started` 与 `panel_push_completed`，且 `trigger=cron`、`target_count` 与测试目标数一致。

### 午夜对齐间隔

关闭 Cron，设置：

| 字段 | 建议测试值 |
|---|---|
| `panel_interval_enabled` | `true` |
| `panel_interval_minutes` | `30` |

间隔从本地每日 `00:00` 对齐：30 分钟时仅在整点和半点触发。为快速观察可临时改为 `1`，完成后恢复 `30`。通过标准：目标会话只收到一次面板，日志 `trigger=interval`。

最后同时启用 Cron 和间隔，并让 Cron 落在一个间隔触发分钟。通过标准：同一目标只收到一条面板，日志最多出现一次该目标的发送尝试。测试结束后关闭两个定时开关、清空测试固定目标，并执行 `/g2面板退订`。

## 7. 本地开发探针

脚本 [testignore/panel_live_check/live_panel_t2i_smoke.py](../testignore/panel_live_check/live_panel_t2i_smoke.py) 可在不启动机器人平台时验证：管理端取数、多源背景和指定 T2I 服务。

它只从当前进程读取 `G2_ADMIN_USER` 与 `G2_ADMIN_PASS`，不会打印或写入凭据、Token、管理响应或原始记录。当前脚本中的管理面、代理和 T2I 地址是开发测试常量；生产验收必须使用第 3 至 6 节的 AstrBot 路径。

在 PowerShell 中临时设置环境变量并执行：

```powershell
$env:G2_ADMIN_USER = '<管理员用户名>'
$env:G2_ADMIN_PASS = '<管理员密码>'
python testignore\panel_live_check\live_panel_t2i_smoke.py
Remove-Item Env:G2_ADMIN_USER
Remove-Item Env:G2_ADMIN_PASS
```

通过标准：输出 `selected_blocks=5`、`failed_blocks=0`、`data_check` 字段关系校验通过、`text_fallback: generated=true`、背景来源和所选分辨率的 `t2i` 图片信息。变量仅存在于当前 PowerShell 进程，不要把命令历史、截图或输出提交到仓库。

可分别定位登录和背景失败：

```powershell
python testignore\panel_live_check\live_panel_t2i_smoke.py --diagnose-login
python testignore\panel_live_check\live_panel_t2i_smoke.py --diagnose-background
```

诊断输出只包含 HTTP 状态、响应字段形状或失败码；不应包含 Token、凭据和下载 URL。

## 8. 代码回归

在仓库根目录执行：

```powershell
$env:PYTHONIOENCODING='utf-8'
python -m json.tool _conf_schema.json
python -m compileall main.py core tests
python -m pytest -q tests/test_admin_client.py tests/test_panel_background.py tests/test_panel_models.py tests/test_panel_card.py tests/test_panel_schedule.py tests/test_main_commands.py
ruff check .
ruff format --check .
git diff --check
```

全量回归使用 `python -m pytest -q`。测试覆盖管理鉴权和聚合、背景新图/缓存/默认回退、卡片模板、T2I 文本回退契约、订阅持久化、Cron/间隔去重与命令注册；无法替代真实 OneBot 或 QQ Official 会话的发送验收。
