# 项目开发指南

修改本仓库前先读 `AGENTS.md`——它是安全规范、协作流程、模块路径、测试隔离、日志与 Git 规则的权威来源。

改动插件运行时代码（`main.py`、`core/`、`_conf_schema.json`）前，必须先调用 `skill-astrbot-dev` skill，按其 Mandatory workflow 从单一入口查证 AstrBot 的钩子、事件流、消息链、生命周期、配置 Schema 与 Agent/Tool 签名，不要凭记忆下笔。文档与 `Progress/` 类改动不需要。

## 不可破坏的不变量

以下行为一旦改变即为回归，除任务明确要求外必须原样保留：

- **能力收敛**：提示词处理与视觉资料检索仅服务 `/g2生图`；`/g2改图` 与 `/g2视频` 永远原文直传，检测到生图专用参数时在远端调用前拒绝。
- **两个 API 面隔离**：业务请求锁死在 `/v1/`（`core/common/transport.py` 的 `_validate_relative_path`）；管理面板独立走 `core/panel/client.py` 的 `AdminClient`，使用 `/api/admin/v1` 只读路径白名单。严禁为新端点放宽 `_validate_relative_path`。
- **重试与回退语义**：模型重试按轮次（来回）计算；单次尝试失败立即切换至下一个候选模型，遍历完所有候选后进入下一轮，总轮次数对齐 `model_retry_count`（视频对齐 `video_retry_count`）。
- **平台与下发**：平台路由、消息下发、配置的代理/TLS 行为，以及面向用户的错误契约。
- **背景图源降级**：按配置的图源顺序尝试并校验结果，全部失败时回退本地缓存或 CSS 默认背景。

## 硬性红线

严禁查看或暴露 `.env` 与本地测试用例中的凭据；严禁在日志打印签名 URL、媒体 URL、请求 ID、上游响应正文或明文鉴权信息。真机测试与临时产物只允许放在 `testignore/`。报告完成前必须跑完 `AGENTS.md` 的验证门禁，并如实报告全部警告、跳过项与风险。
