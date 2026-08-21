# 仓库开发规范与规则

## 适用范围

本仓库是为 AstrBot 提供的 Grok2API 插件，支持联网搜索、文生图、改图、视频生成以及管理面板功能。改动应严格聚焦于请求的目标行为，并保护用户其他不相关的修改。

## 平台 API 查证

- 改动插件运行时代码（`main.py`、`core/`、`_conf_schema.json`）前必须先调用 `skill-astrbot-dev` skill，按其 Mandatory workflow 从单一入口进入，查证 AstrBot 的钩子与装饰器、事件流、消息链转换、生命周期、配置 Schema 与 Agent/Tool 签名。严禁凭记忆写平台 API。
- 遵循该 skill 的「避免宽泛加载」要求：只打开与当前任务相关的那一个主题目录，不要通读全部参考文档。
- 文档与代码冲突时以 AstrBot 实际安装版本的代码为准。当前 `metadata.yaml` 声明 `astrbot_version: ">=4.26.0,<5"`；跨版本行为差异需实际核对已安装版本。
- 纯文档、`Progress/` 记录、CI 配置类改动不需要走这一步。

## 安全规范

- 严禁读取、打印、提交或硬编码 `.env` 配置、API Key、Token、密码、Cookie、JWT 或私有 URL。
- 禁止在日志中输出凭据、Bearer/JWT 值、Base64 媒体数据、签名 URL、用户信息、媒体 URL、请求 ID 或上游原始响应正文。
- 远端 API 响应与图片 URL 必须视为不可信输入。使用前必须对协议（scheme）、重定向、图片字节合法性、体积、解码、尺寸及宽高比进行校验。
- **两个 API 面必须保持隔离**：业务请求由 `core/common/transport.py` 的 `_validate_relative_path` 锁死在 `/v1/` 前缀；管理面板走独立的 `core/panel/client.py::AdminClient`，使用管理员凭据登录并受 `_READ_ONLY_PATHS` 白名单约束的 `/api/admin/v1` 只读端点。新增管理端点必须扩充该白名单，**严禁**为此放宽 `_validate_relative_path`。


## 开发与协作规范

- 针对架构重构、规则修改、元数据更新、测试增删等重大决策，必须先向用户阐明方案与选型，待用户确认后再执行代码修改。
- 手动修改时优先使用精确替换。切勿使用破坏性 Git 命令或丢弃不相关的用户改动。
- 若存在 `.codegraph/` 目录，在搜索或读取源文件前应优先运行 `codegraph explore`。
- 事实性任务记录保存在 `Progress/YYYY-MM-DD*.md`；严禁将 `Progress/` 提交到 Git。
- `Progress/`、`docs/superpowers/plans/`、`docs/superpowers/specs/` 均已被 `.gitignore` 忽略，是**历史记录**：可新增，但不要为了迎合当前改动而改写或删除既有记录。
- 未经确认现有依赖技术栈并记录充分理由前，切勿随意引入新依赖。

## 模块路径约定

`core/` 下有 20 个形如 `"""Backward-compatibility bridge: re-exports from X."""` 的桥接模块（`core/errors.py`、`core/config.py`、`core/transport.py`、`core/panel_*.py` 等），它们把旧的平铺路径重定向到重构后的子包。

- 真实实现位于子包：`core/common/`（config、errors、models、transport、observability、sender、platform、deadline、access、prompt_processor）、`core/media/`、`core/search/`、`core/panel/`、`core/handlers/`。
- **新增代码一律 import 真实子包路径**（如 `from ..common.errors import PluginError`）。桥接模块只为兼容存量调用，不得新增，也不要在新文件里引用。
- 存量测试仍有相当数量走桥接路径，这不是待修的缺陷；除任务明确要求批量迁移，不要顺手改动。


## 本地测试与调试隔离

- 所有真机网络调用脚本、端到端测试、临时产物（图片/视频/缓存）必须严格隔离在 `testignore/` 目录（或其子目录）中执行。严禁在项目根目录、父级目录或系统用户目录下随意创建一次性测试文件或残留输出。
- 测试脚本的调用逻辑与事件流必须严格与插件运行时的真实架构保持一致（包括生命周期、事件对象、消息链组装、配置加载与服务调度），严禁手拼脱离实际的模拟请求。

## 配置 Schema 与文案约定

- 配置项术语保持标准直观（如 `api_key`），杜绝生造词与历史别名（如 Client Key）。
- `_conf_schema.json` 中的 `description` 与 `hint` 保持精炼，直击配置核心用途，严禁堆砌常识性说教文案。

## 代码与日志约定

- 遵循既有的 Python、aiohttp、AstrBot 和 pytest 设计模式。
- INFO 级别日志仅保留简洁的任务块；网络传输、轮询、单次模型尝试、面板子请求以及平台发送细节均置于 DEBUG 级别。
- 严格遵循重试契约：模型重试按轮次（来回）计算；单次尝试失败立即切换至下一个候选模型，遍历完所有候选后进入下一轮，总轮次数对齐 `model_retry_count`（视频对齐 `video_retry_count`）。候选遍历顺序由 `model_retry_strategy` 选择：默认「轮询重试」`round_robin`（每轮遍历全部候选）；「依次重试」`sequential` 时先对单个候选模型耗尽所有重试轮次，再切换到下一个候选模型，行为以 `core/service.py` 的 `_iter_model_attempts` 为准。非首轮重试（round > 1）在发起请求前等待 `retry_base_delay_seconds` 退避，避免对故障上游连番请求。
- 多模态处理具备自愈能力：图片宽高比按对数距离自动对齐到支持的合法集合（`closest_aspect_ratio`）；默认模型候选必须与远端各端点实际能力精确匹配。
- 保持媒体背景的轮询降级顺序以及本地缓存/CSS 默认样式的回退行为，除非任务明确要求修改。
- 能力收敛：提示词处理与视觉资料检索仅服务 `/g2生图`；`/g2改图` 与 `/g2视频` 始终原文直传，检测到生图专用参数时在任何远端请求前拒绝。

## 用户可见错误消息契约

- `PluginError`（含子类）会把消息脱敏并在 **200 字符**处静默截断（`core/common/errors.py` 的 `_MAX_USER_MSG`）。动态拼接的消息必须自行控长，例如列举 token 时限定条数与单条长度。
- `core/handlers/base.py` 的 `_send_error` 取 `_ERROR_HINTS.get(exc.code, exc.user_message)`：给某个 error code 添加 `_ERROR_HINTS` 条目会**覆盖**该异常携带的动态消息。需要在消息中回显具体参数、字段或数值时，**不要**为其添加 `_ERROR_HINTS` 条目。
- 错误消息只允许包含用户自己的输入与固定文案，严禁回显上游正文、URL、请求 ID 或凭据。


## 测试套件维护

- `tests/` 目录严格聚焦于插件运行时业务功能与协议安全（搜索、生图、改图、视频、面板、鉴权、权限、配置校验）。
- 避免在主测试套件中堆积非功能性测试（如仅用于发版的工具脚本）或过度白盒的私有变量镜像断言。

## 版本管理与 Git

- 运行时版本、打包版本、元数据（metadata.yaml）、README 徽标与 CHANGELOG 版本必须严格一致。
- 除非用户明确授权，否则切勿擅自修改版本号、提交（commit）、推送（push）、发布（publish）或创建 Release。
- 严禁在提交信息（commit message）或 Release 说明中添加 AI 署名后缀（Attribution Trailers）。
- 发布工作流仅接受不可变的 `vX.Y.Z` 标签（Tag）。不得创建或移动 Tag，不得覆盖已有 Release，不得使用 `--clobber`。
- Release Notes 遵循 PR 的 `release-note` 元数据约定，并使用公开的 GitHub 账号/PR/Commit 链接进行致谢；切勿从邮箱地址推断身份。
- 在暂存变更（git stage）前，仔细检查 `git diff`，仅暂存属于本次目标改动的文件。

## 自动化验证门禁

本仓库**没有 PR/main 的 CI 检查**（`.github/workflows/` 只有发布用的 `release-plugin.yml`）。下列本地门禁是唯一的质量关卡，不跑就没有任何东西兜底。本节是门禁的唯一权威定义，其他文档只应引用它。

先运行相关的定向测试，然后执行全套验证：

```bash
python -m json.tool _conf_schema.json
python -m compileall main.py core tests scripts
python -m pytest -q
ruff check .
ruff format --check .
git diff --check
python scripts/check_repository.py --tag v0.3.0   # tag 用当前 metadata.yaml 的版本
```

`check_repository.py` 校验运行时版本、`pyproject.toml`、`metadata.yaml`、README 徽标与 CHANGELOG 的一致性，是「版本必须严格一致」这条规则的可执行形式。

执行完毕后，明确报告所有警告、跳过的检查项、外部服务限制以及潜在风险。`git diff --check` 在 Windows 上会输出 CRLF 提示，属正常现象。
