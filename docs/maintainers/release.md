# 发布与恢复手册

## 发布生命周期

1. 在 PR 中填写双语 Release Notes metadata，并确认变更分类、平台影响和迁移说明。
2. 合并到 `main` 后等待只读 CI 通过；由维护者创建不可变 `vX.Y.Z` tag。
3. 运行 `Release plugin archive` 的 `workflow_dispatch`，先用 `dry_run=true` 验证；正式发布只接受 tag push 或显式手动输入。
4. workflow 校验 tag 是默认分支祖先、版本来源一致、没有同名 Release，然后审计 PR/direct commit 来源。
5. 测试构建与干净 runner 生产重建分别生成归档，脚本复核成员、版本、Python 语法、manifest 和 SHA-256。
6. `publish` 在受保护 `release` environment 中创建 draft Release，比较远端资产集合后才公开。

正文按 English / 简体中文对称渲染，包含分类变更、PR/Issue 来源、普通贡献者、新贡献者、共同作者、破坏性变更、迁移说明和 Full Changelog。没有公开 GitHub login 的 direct commit 不会被猜测归因；strict 模式会阻断发布。

## 失败恢复

- Tag 校验失败：修复分支后重新运行 CI；不要移动已有 tag。
- dry-run 失败：只修复代码或 metadata 后重新 dry-run，不会创建 Release。
- draft 资产审计失败：保留 draft，人工检查 manifest 和 asset 集合；不得使用 `--clobber` 覆盖发布资产。
- 已公开 Release 后的下游或外部平台失败：记录“核心 Release 已发布、下游未完成”，只做公开回读、补发或新修复版本，不删除或改写已发布资产。
- 同名 Release 已存在：workflow 直接失败。恢复必须由维护者显式执行，先比较 tag commit、正文和 SHA-256，再决定创建新修复版本。

## 证据与演练

保留 workflow run URL、`release-notes-audit.json`、manifest、checksum、验证命令和 `SKIP` 项。artifact retention 只有有限期限，发布后应在维护记录中保存审计摘要，但不要保存 token、邮箱、Cookie、完整日志或真实媒体 URL。
