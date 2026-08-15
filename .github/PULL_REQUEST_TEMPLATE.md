## 变更摘要

<!-- 用简洁语言说明用户可观察到的变化、触发场景和解决方式。 -->

## 关联 Issue

<!-- 使用 Closes #123、Fixes #123 或留空。不要粘贴包含凭据、Cookie 或私有 URL 的链接。 -->

## 验证

- [ ] 我运行了与改动相关的测试，并在下方填写命令和结果。
- [ ] 我检查了 `git diff --check`。
- [ ] 未能运行的检查已在下方说明原因和剩余风险。

验证命令与结果：

```text
<!-- 例如：python -m pytest -q -> 123 passed -->
```

## 文档、配置与兼容性

- [ ] 已同步 README、`docs/`、`_conf_schema.json` 或测试（如适用）。
- [ ] 已说明 AstrBot、OneBot、QQ Official 或其他平台影响（如适用）。
- [ ] 已说明兼容性、迁移、权限、性能和安全影响（如适用）。
- [ ] 没有提交 `data/`、`Progress/`、`.env`、凭据、真实媒体 URL、完整日志或构建产物。

## Release Notes 元数据

<!--
必须保留且只能保留一个 release-note JSON 块。请修改其中的值，不要改字段名或添加第二个块。
category 只能是 Added、Changed、Fixed、Removed、Security、Documentation、Maintenance、None。
category 为 None 时必须填写 none_reason，并将 breaking 设为 false；其他分类必须填写中英文摘要。
breaking 为 true 时必须同时填写中英文迁移说明或文档链接。
issue 只能是 #123、本仓库 Issue URL 或空字符串；platforms 请填写实际受影响的平台。
-->
<!-- release-note:
{"category":"None","breaking":false,"summary_zh_cn":"","summary_en":"","migration_zh_cn":"","migration_en":"","issue":"","platforms":["platform-neutral"],"none_reason":"仅测试、文档或维护改动，不改变用户可见行为。"}
-->

## 其他说明

<!-- 提供评审者需要知道的取舍、后续工作或人工检查项。 -->
