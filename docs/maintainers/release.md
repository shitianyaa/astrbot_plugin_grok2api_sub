# 发布说明

## 发布流程

1. 更新 `metadata.yaml`、`pyproject.toml`、`core/config.py`、README 版本徽章和 `CHANGELOG.md`，确保版本一致并包含该版本的变更记录。
2. 合并/推送代码到 `main` 分支：
   - 工作流自动检测 `metadata.yaml` 中的版本号。
   - 若该版本尚未在 GitHub 发布，自动触发测试、打包、提取 `CHANGELOG.md` 对应的 Release Notes，并创建 Git Tag 和 GitHub Release。
   - 若该版本已发布（常规 commit），工作流自动跳过发版。
3. （备选）亦可直接推送不可变的 `vX.Y.Z` tag，或在 GitHub Actions 中手动输入 tag 进行 `workflow_dispatch` 发布或 dry-run 验证。

## 自动验证与打包

`Release plugin archive` 包含以下步骤：

- 检查版本格式与同名 Release 状态；
- 校验全项目版本来源一致性、配置 JSON 格式与 Python 语法；
- 运行全量 pytest 测试与 Ruff 代码检查；
- 构建 ZIP、SHA-256 校验和与 `manifest.json`，并复核产物完整性；
- 调用 `scripts/extract_changelog.py` 从 `CHANGELOG.md` 抽取当前版本的变更说明；
- 使用 `--notes-file` 将提取的 Markdown 内容发布至 GitHub Release。

## 失败处理

- 验证失败：修复代码或版本信息后提交；不要移动已经发布的 tag。
- CHANGELOG 提取失败：检查 `CHANGELOG.md` 是否包含 `## vX.Y.Z (YYYY-MM-DD)` 格式标题且内容非空。
- 同名 Release 已存在：工作流在 push 到 main 时会自动安全跳过；如需发布新修复请提升版本号。
