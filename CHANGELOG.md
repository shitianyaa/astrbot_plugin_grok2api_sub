# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/) 规范。

## [v0.1.0] - 2026-08-11

### Added

- 通过 grok2api Client Key 提供联网搜索、生图、改图、生视频。
- 双平台发送：OneBot/NapCat（aiocqhttp）与 QQ Official。
- 六个命令：`/g2搜索`、`/g2生图`、`/g2改图`、`/g2视频`、`/g2状态`、`/g2帮助`。
- 会话级注册的 `grok2api_web_search` FunctionTool，由 AstrBot 主模型决定是否调用。
- 配置集中校验、访问控制（黑白名单）、并发/大小限制、临时文件清理。
- HTTP 传输：同源相对路径、受控重试矩阵、`.part` 原子下载、代理支持。
- 生成类 POST 状态不确定时不自动重试（`AmbiguousSubmissionError`）。

### Security

- 只保存 Client Key；日志、状态页、错误消息均不泄露密钥或完整 Base64。
- 拒绝把上游提供的绝对 URL 作为鉴权请求目标，避免 Key 外泄。
- 图片输入归一化并防解压炸弹；输出媒体先落盘再由 AstrBot 发送。