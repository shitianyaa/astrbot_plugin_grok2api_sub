# 仓库维护指南

## 日常协作

- 从 `main` 创建短分支，一个 Pull Request 只处理一个主题。
- PR 必须填写唯一的 `release-note` JSON；用户可见变化需要中英文摘要，破坏性变化需要中英文迁移说明。
- 修改命令、配置、平台行为或日志时，同步 README、`docs/`、`_conf_schema.json` 和测试。
- `Progress/`、`.codegraph/`、`data/`、`.env*`、本机配置和临时产物不属于提交或发布输入。

## 本地质量门

```powershell
python -m pip install -e ".[dev]"
python -m json.tool _conf_schema.json > $null
python scripts/check_pr.py --event .tmp-event.json
python scripts/check_repository.py --tag vX.Y.Z
python -m compileall main.py core tests scripts
python -m pytest -q
ruff check .
ruff format --check .
git diff --check
```

发布前额外运行 `python scripts/package_plugin.py --tag vX.Y.Z --output-dir .tmp-release-dist`，检查 ZIP、`.sha256` 和 `manifest.json`。脚本拒绝符号链接、敏感路径、危险归档成员和覆盖既有输出。

## 平台侧管理员清单

以下项目不能由仓库文件证明，必须在 GitHub 管理界面核验：

- `main` 分支保护、required CI checks、禁止直接 push 和强制线性历史策略。
- `vX.Y.Z` tag ruleset，禁止移动或删除已发布 tag。
- `release` environment 的 required reviewers；只有该 environment 可执行 Release 写入。
- Actions 使用的 token 权限和分支创建权限。
- 发布前至少完成一次 dry-run 和一次真实新 tag 演练；OneBot/QQ Official 真实验收单独记录，缺凭据写 `SKIP`。

CODEOWNERS 只提供 reviewer 路由，不能替代分支保护。发布成功也不代表外部平台验收成功，必须分别记录。
