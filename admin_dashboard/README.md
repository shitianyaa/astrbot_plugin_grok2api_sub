# Grok2API 管理面临时状态查看工具

**非交付代码，仅供 bot 主人过目当前服务状态。**

## 功能

从 grok2api 管理面拉取：

- **账号区**：分渠道（Build/Web/Console）+ 全部账号 + 异常明细（冷却/待重置/探测中/风控/停用/失效）
- **媒体库**：图片库（张数/空间）、视频库（任务数/状态分布）
- **调用状况**：按周期（24h/7d/30d/90d）显示总请求/成功率/Tokens/估算费用
- **按模型统计**：本地逐条分组，显示每个模型的调用次数/成功率/平均耗时/总 Tokens

渲染一个**自包含的 `view.html`**（浏览器双击即可查看，无 CORS 问题、无服务端依赖）。

## 用法

```bash
# 1. 配置连接参数（首次运行会生成模板）
#    编辑 admin_dashboard/config.local.json，填写 base/proxy/username/password
#    密码也可留空走环境变量 GROK_ADMIN_PASSWORD

# 2. 拉取并生成
python admin_dashboard/fetch.py                  # 默认拉 24h,7d,30d,90d
python admin_dashboard/fetch.py --periods 7d,30d # 指定周期

# 3. 浏览器打开 admin_dashboard/view.html
```

## 文件

- `fetch.py` — 拉取脚本（登录 → 调管理面只读 GET → 渲染 HTML）
- `template.html` — 页面骨架 + CSS
- `render.js` — 前端渲染逻辑（账号/媒体/调用/模型表）
- `config.local.json` — 连接配置（**已进 .gitignore，不入库**）
- `view.html` — 生成的看板（浏览器打开）

## 安全

- 凭据绝不硬编码；`config.local.json` 已被 `.gitignore` 忽略
- 登录 JWT 仅在拉取进程内使用，不落盘
- 端点全部只读 GET；逐条审计用 cursor 分页（默认 50/页，防失控上限 10k 页）
- 生成的 `view.html` 不含凭据/账号邮箱

## 成功口径

**grok2api 自身定义**：`statusCode` 2xx **且** `errorCode` 为空。

- 管理面 summary 端点返回的 `successfulRequests` 即此口径
- 本地按模型统计也用同一口径，与 summary 对齐

## 限制

- **临时工具**：用于快速过目，不替代正式监控
- **无历史**：每次重跑覆盖 `view.html`，不保留快照
- **只读**：不做任何写操作（账号操作/配置修改等请走正式管理面或 CLI）
