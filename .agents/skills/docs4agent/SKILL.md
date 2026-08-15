---
name: docs4agent
description: 遵循极简主义、代码优先、AI 友好的技术文档写作规范。
---
# docs4agent (文档写作规范)

当需要为 AI Agent 编写或优化技术文档（尤其是 AstrBot 插件 API）时，使用此 Skill。目标是创建 100% 准确、无废话、机器易读的代码字典。

## 写作核心原则

1. **极简主义**：删除所有客套话、目的说明、意义介绍和人类常识。
2. **代码优先**：将 API 示例压缩为单行代码块，并标注 `python` 语言标识。
3. **AI 友好**：使用结构化的标题和列表，确保 AI 能快速定位方法名、参数和类型。
4. **排除常识**：不需要解释“为什么要这样做”，只需告知“怎么做”。
5. 插件开发可直接调用,需要给出全部的插件可以使用的方法
6. 可以去参考sdk（例如astrbot sdk）

## 文档结构模版

### 1. 标题与摘要

标题采用 `# 功能名`，摘要仅保留一行功能描述。

### 2. API 调用区

使用 `###` 级标题区分方法，代码块必须单行化。
示例：
`await manager.method_name(param1="value", param2=True)`

### 3. 维护/工具列表

使用无序列表描述辅助方法：

- `method(id)`: 描述。

### 4. 关键注意事项 (可选)

仅保留不符合常规逻辑或容易导致程序崩溃的底层细节（如 Handler 绑定丢失）。

## 示例 (Cron API)

# Cron #英文后面不需要加一个小括号解释中文

用于定时执行逻辑或唤醒 AI。AI 任务触发生成 `CronMessageEvent`。

## 插件开发 API

通过 `self.context.cron_manager` 调用：

### 1. 注册 Python 函数 (Basic Job)

`await cron_mgr.add_basic_job(name="任务名", cron_expression="*/5 * * * *", handler=self.your_method, payload={"key": "value"}, persistent=False)`

### 2. 注册 AI 唤醒 (Active Agent Job)

`await cron_mgr.add_active_job(name="AI 任务", cron_expression="0 8 * * *", payload={"session": "UMO", "note": "指令"}, run_once=False)`
