# 贡献指南

感谢你为 `astrbot_plugin_grok2api_sub` 提交问题、文档或代码。这个仓库是 AstrBot 插件，贡献内容需要同时考虑插件行为、配置说明、平台兼容性和发布归档。

## 开始之前

- 请先搜索现有 Issue、Pull Request 和 [README](README.md)，避免重复工作。
- 安全漏洞不要公开创建 Issue，请使用仓库的 [GitHub Security Advisory](https://github.com/shitianyaa/astrbot_plugin_grok2api_sub/security/advisories/new) 私下报告。
- 一次 Pull Request 只解决一个可描述的主题；跨主题改动请拆分提交。
- 默认从 `main` 创建短生命周期分支，例如 `fix/panel-timeout` 或 `docs/release-guide`。
- 不要提交 `data/`、`Progress/`、`.env`、API Key、JWT、Cookie、密码、真实媒体 URL、完整上游响应或构建产物。

## 提交 Pull Request

Pull Request 至少应说明：

1. 发生了什么变化，以及用户能观察到什么结果。
2. 如何验证，包含实际运行的命令和结果；没有条件运行的检查要明确写出原因。
3. 是否需要同步 README、`docs/`、配置 schema、测试或 CHANGELOG。
4. 是否影响 AstrBot、OneBot、QQ Official 或其他平台；平台差异要分别说明。
5. 是否存在兼容性、迁移、性能、权限或安全影响。

请完整填写 Pull Request 模板中的唯一 `release-note` JSON 声明。它是后续 Release Notes 审计的机器可读输入，不要在同一个 Pull Request 中添加第二个同名声明。字段约定如下：

- `category` 只能是 `Added`、`Changed`、`Fixed`、`Removed`、`Security`、`Documentation`、`Maintenance` 或 `None`。
- `breaking` 必须是 JSON 布尔值。若为 `true`，必须同时填写中英文迁移说明或对应文档链接。
- 面向用户的变更必须填写 `summary_zh_cn` 和 `summary_en`；无用户可见变化时使用 `category: "None"`，并填写非空 `none_reason`。
- `migration_zh_cn`、`migration_en` 没有迁移要求时使用空字符串，有迁移要求时两者都要填写。
- `issue` 使用 `#123` 或本仓库 Issue URL；没有关联 Issue 时使用空字符串，不要填写外部追踪系统的敏感地址。
- `platforms` 使用明确的平台名，例如 `astrbot`、`onebot`、`qq-official`、`platform-neutral`。

## 本地验证

本仓库没有 PR/main 的 CI 检查，本地门禁是唯一的质量关卡。完整命令清单见 `AGENTS.md` 的「自动化验证门禁」一节——那里是唯一权威定义，请直接照它执行，不要依赖本文的副本。

只修改文档或模板时，也要确认 Markdown 链接、YAML 结构和隐藏的 `release-note` JSON 没有语法错误。不要为了让检查通过而关闭类型、lint、安全校验或吞掉异常。

## 文档与配置同步

如果改动命令、配置项、平台行为、日志或用户可见错误，请同步相关 README、`docs/`、`_conf_schema.json` 和测试。新增配置必须说明默认值、风险和兼容性；敏感配置只能写字段名和脱敏示例。

## 提交信息

提交信息应简洁描述实际变化，可使用 `feat:`、`fix:`、`docs:`、`test:`、`refactor:` 或 `chore:` 前缀。不要在提交信息、Release Notes、PR 描述或标签中添加 AI 署名或生成声明。

## Release Notes 约定

发布版本会根据已合并 PR 的声明生成双语 Release Notes，并尽量列出 PR、Issue、普通贡献者、首次贡献者和共同作者。贡献者署名以公开 GitHub login 和 PR 链接为准，不使用邮箱推断账号。

如果一个变更没有对应 PR，发布审核会将其标记为需要人工归因，而不会猜测贡献者。请尽量通过 PR 合并变更，以便保留讨论、审查和贡献记录。

## 行为与安全

涉及凭据、用户数据、上游响应或真实服务的验证必须使用脱敏数据和最小权限。可利用的安全问题统一通过 GitHub Security Advisory 私下报告，不要在公开 Issue、PR 或日志中披露细节。
