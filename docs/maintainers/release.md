# 发布说明

## 发布前准备

1. 更新 `metadata.yaml`、`pyproject.toml`、`core/config.py`、README 版本徽章和 `CHANGELOG.md`，确保版本一致。
2. 合并改动到默认分支，等待普通 CI 通过。
3. 创建并推送不可变的 `vX.Y.Z` tag，或在 GitHub Actions 中手动输入已存在的 tag。

## 自动验证

`Release plugin archive` 只有一个 job，会依次完成：

- 检查稳定版 tag、默认分支祖先关系和同名 Release；
- 校验版本来源、配置 JSON 和 Python 语法；
- 运行 pytest 与 Ruff；
- 构建 ZIP、SHA-256 和 `manifest.json`，并复核产物。

正式发布时使用 GitHub 自带的 `--generate-notes` 生成 Release Notes，不再额外维护 Release Notes 收集脚本的 CI 编排。

手动运行时默认 `dry_run=true`，只验证和打包，不发布。Tag push 会在全部验证通过后创建 GitHub Release。工作流不会创建、移动或覆盖 tag，也不会覆盖已有 Release。

## 失败处理

- 验证失败：修复代码或版本信息后创建新的提交；不要移动已经发布的 tag。
- Release Notes 内容不完整：直接编辑 GitHub Release 文本，或在后续版本的 PR 描述中补充变更说明。
- 同名 Release 已存在：工作流会直接停止；需要修复时发布新的补丁版本。
