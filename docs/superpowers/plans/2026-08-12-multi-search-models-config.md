# Multi Search Models And Config Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not commit or push unless the user explicitly authorizes it.

**Goal:** 为联网搜索增加按英文逗号配置的有序多模型候选和安全回退，并把未发布插件的扁平配置直接重构为 4 个 WebUI 分组。

**Architecture:** `_conf_schema.json` 只保留连接、能力、访问控制、高级设置 4 个 `object`；`PluginConfig` 将嵌套原始配置解析为扁平、不可变的运行时值。`Grok2APIClient` 负责缓存 `/v1/models` 和保留安全的模型错误码，`GrokService` 负责候选顺序、回退矩阵和状态汇总，`main.py` 只负责 AstrBot Tool/命令展示。

**Tech Stack:** Python 3.10+、AstrBot 4.26.6、aiohttp、pytest、pytest-asyncio、ruff、JSON Schema 风格的 AstrBot `_conf_schema.json`。

## Global Constraints

- 插件尚未发布：不保留旧扁平配置键，不增加 `config_schema_version`，不实现迁移、旧键回退或配置回写。
- 正式运行只调用用户填写的远端 grok2api；`connection_settings.api_base_url` 默认 `""`，`connection_settings.client_proxy_url` 默认 `""`。
- 代码、Schema、README、测试不得内置 `127.0.0.1:3067`；代理示例只能使用通用占位地址。
- `capability_settings.search_models` 默认严格为 `grok-chat-fast,grok-4.3,grok-4.5,grok-build-0.1`。
- `grok-4.20-0309-non-reasoning`、`grok-4.20-0309-reasoning`、`grok-4.20-multi-agent-0309` 只作为 README 可选模型，不进入默认顺序。
- 搜索模型仅使用英文逗号分隔；去空白、忽略空项、大小写敏感、按首次出现保序去重；最多 12 个候选，每项最多 255 个字符；中文逗号直接报配置错误。
- `/v1/models` 缓存 TTL 固定 300 秒，不增加 WebUI 配置；目录失败时不使用过期目录过滤候选。
- 每次搜索都从配置第一项开始；不得永久提升上次成功模型或轮询改变用户顺序。
- 仅 `not_visible`、`model_not_found`、`model_not_allowed`、`search_not_performed` 可以进入下一候选。
- 401、429、计费/额度、连接失败、超时、HTTP 5xx、无效 2xx JSON、其他 4xx 和协议错误不得切换模型重放 POST。
- `/g2状态` 只能调用 `GET /v1/models`，不得执行真实搜索探针。
- 不为图片、改图或视频实现多模型回退。
- 不增加生产依赖，不记录 Client Key、Authorization、代理凭据、查询正文、上游错误消息或原始响应体。
- 保持 OneBot `aiocqhttp` 与 `qq_official` 现有支持范围和现有命令名。
- 开始前先确认真实 Client Key 已在服务端轮换；自动化测试只能使用伪造 Key 和 fake HTTP 响应，不读取 `testignore/`。
- 保留当前工作树中已有的 DS 改动；不要重置、清理或覆盖无关文件。

---

## File Responsibility Map

| File | Responsibility after this plan |
|---|---|
| `_conf_schema.json` | 4 个最终 WebUI 分组及所有默认值，不含兼容键。 |
| `core/config.py` | 校验嵌套配置、解析搜索模型列表、提供运行时能力检查。 |
| `core/errors.py` | 保持稳定、安全的插件错误模型。 |
| `core/transport.py` | 有界读取 OpenAI 风格错误体，只保留允许的模型错误码。 |
| `core/client.py` | 调用 grok2api，缓存模型目录 300 秒，不决定业务回退。 |
| `core/search_models.py` | 纯函数完成 Provider 前缀归一与可见候选分区。 |
| `core/service.py` | 按用户顺序执行搜索和严格回退矩阵，生成状态报告。 |
| `core/models.py` | 扩展状态 DTO，明确区分目录模型与配置候选。 |
| `core/observability.py` | 允许安全的模型选择日志字段。 |
| `main.py` | Tool 可用性判断、状态与帮助命令展示。 |
| `README.md`、`docs/*.md` | 记录最终配置路径、模型顺序、回退边界和测试方法。 |

---

### Task 1: 直接切换到 4 分组配置和 `search_models` 运行时模型

**Files:**
- Replace: `_conf_schema.json`
- Modify: `core/config.py`
- Modify: `main.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_service.py`
- Modify: `tests/test_runtime_wiring.py`
- Create: `tests/test_schema.py`

**Interfaces:**
- Consumes: AstrBot 传入的 `Mapping[str, object]`，顶层键只允许 `connection_settings`、`capability_settings`、`access_settings`、`advanced_settings`。
- Produces: `parse_search_models(value: object) -> tuple[str, ...]`。
- Produces: `PluginConfig.search_models: tuple[str, ...]`、`PluginConfig.has_api_base_url: bool`。
- Keeps: 其余运行时字段继续是 `PluginConfig` 的扁平属性，业务层不直接读取嵌套字典。

- [ ] **Step 1: 写搜索模型解析与嵌套配置失败测试**

  在 `tests/test_config.py` 把 `_raw()` 改成只构造 4 个分组，并加入以下测试。测试辅助函数必须深合并子分组，不能用浅层 `dict.update()` 覆盖整组默认数据：

  ```python
  DEFAULT_SEARCH_MODELS = (
      "grok-chat-fast",
      "grok-4.3",
      "grok-4.5",
      "grok-build-0.1",
  )


  def test_search_models_are_trimmed_deduplicated_and_ordered():
      cfg = _cfg(capability_settings={
          "search_models": " grok-4.5, grok-chat-fast,,grok-4.5 "
      })
      assert cfg.search_models == ("grok-4.5", "grok-chat-fast")


  def test_empty_search_models_explicitly_disable_search():
      cfg = _cfg(capability_settings={"search_models": "  ,  "})
      assert cfg.search_models == ()
      assert cfg.missing_capability("search") == "未配置搜索模型"


  @pytest.mark.parametrize(
      "value",
      [
          "grok-4.5，grok-chat-fast",
          ",".join(f"model-{i}" for i in range(13)),
          "x" * 256,
          ["grok-4.5"],
      ],
  )
  def test_invalid_search_model_lists_are_rejected(value):
      with pytest.raises(ConfigurationError) as caught:
          _cfg(capability_settings={"search_models": value})
      assert caught.value.code == "invalid_config"


  def test_empty_remote_connection_can_initialize_as_disabled_capability():
      cfg = _cfg(connection_settings={"api_base_url": "", "client_api_key": ""})
      assert cfg.has_api_base_url is False
      assert cfg.missing_capability("search") == "未配置远端 API 地址"
  ```

- [ ] **Step 2: 写最终 Schema 结构测试**

  新建 `tests/test_schema.py`，通过 `json.loads()` 读取插件根目录的 `_conf_schema.json`：

  ```python
  EXPECTED_GROUPS = [
      "connection_settings",
      "capability_settings",
      "access_settings",
      "advanced_settings",
  ]


  def test_schema_has_only_four_final_groups(schema):
      assert list(schema) == EXPECTED_GROUPS
      assert all(schema[key]["type"] == "object" for key in EXPECTED_GROUPS)
      assert all(isinstance(schema[key]["items"], dict) for key in EXPECTED_GROUPS)


  def test_remote_connection_defaults_are_empty(schema):
      items = schema["connection_settings"]["items"]
      assert items["api_base_url"]["default"] == ""
      assert items["client_proxy_url"]["default"] == ""


  def test_search_model_default_order_is_stable(schema):
      value = schema["capability_settings"]["items"]["search_models"]["default"]
      assert value == "grok-chat-fast,grok-4.3,grok-4.5,grok-build-0.1"
      assert "search_model" not in schema
      assert "config_schema_version" not in schema
  ```

  `schema` fixture 必须从 `Path(__file__).parents[1] / "_conf_schema.json"` 读取 UTF-8 文件，不读取 AstrBot 运行目录。

- [ ] **Step 3: 运行配置测试并确认当前失败**

  Run: `python -m pytest tests/test_config.py tests/test_schema.py tests/test_runtime_wiring.py -q`

  Expected: FAIL，因为当前 Schema 是扁平结构、`PluginConfig` 只有单值 `search_model`，且空 `api_base_url` 会抛错。

- [ ] **Step 4: 在 `core/config.py` 实现严格解析器**

  增加常量和纯函数：

  ```python
  DEFAULT_SEARCH_MODELS = (
      "grok-chat-fast",
      "grok-4.3",
      "grok-4.5",
      "grok-build-0.1",
  )
  MAX_SEARCH_MODELS = 12
  MAX_MODEL_ID_CHARS = 255


  def parse_search_models(value: object) -> tuple[str, ...]:
      if not isinstance(value, str):
          _fail("capability_settings.search_models", "必须是英文逗号分隔的字符串")
      if "，" in value:
          _fail("capability_settings.search_models", "请使用英文逗号 , 分隔")
      result: list[str] = []
      seen: set[str] = set()
      for raw in value.split(","):
          model = raw.strip()
          if not model or model in seen:
              continue
          if len(model) > MAX_MODEL_ID_CHARS:
              _fail("capability_settings.search_models", "单个模型名不能超过 255 个字符")
          seen.add(model)
          result.append(model)
      if len(result) > MAX_SEARCH_MODELS:
          _fail("capability_settings.search_models", "最多配置 12 个模型")
      return tuple(result)
  ```

  增加 `_section(mapping, key)`，值不是 `Mapping` 时抛 `invalid_config`。`from_astrbot()` 只读取 4 个分组；不读取任何旧顶层键，不调用 `save_config()`。删除 `search_model` 字段，新增：

  ```python
  search_models: tuple[str, ...]

  @property
  def has_api_base_url(self) -> bool:
      return bool(self.api_base_url)
  ```

  `_normalize_url()` 继续允许空字符串；删除 `from_astrbot()` 中对空 `api_base_url` 的 `_fail()`。`missing_capability()` 的固定顺序为：插件禁用 -> API 地址为空 -> Client Key 为空 -> 对应模型为空。

- [ ] **Step 5: 用 4 个 `object` 重写 `_conf_schema.json`**

  子项归属必须严格按下表，不保留顶层平铺副本：

  | Group | Items |
  |---|---|
  | `connection_settings` | `enabled`, `api_base_url`, `client_api_key`, `verify_tls`, `client_proxy_url` |
  | `capability_settings` | `search_models`, `image_model`, `image_edit_model`, `video_model`, `enable_llm_search_tool`, `show_search_sources`, `max_search_sources`, `max_search_output_chars`, `video_resolution`, `image_response_format`, `max_images_per_request`, `send_media_progress` |
  | `access_settings` | `user_whitelist`, `user_blacklist`, `group_whitelist`, `group_blacklist` |
  | `advanced_settings` | `connect_timeout_seconds`, `search_timeout_seconds`, `image_timeout_seconds`, `video_create_timeout_seconds`, `video_poll_interval_seconds`, `video_max_wait_seconds`, `download_timeout_seconds`, `max_input_image_mb`, `max_image_download_mb`, `max_video_download_mb`, `max_concurrent_searches`, `max_concurrent_media_jobs`, `get_retry_attempts`, `retry_base_delay_seconds`, `save_media`, `temp_retention_hours`, `debug_mode` |

  新的远端地址字段使用：

  ```json
  "api_base_url": {
    "description": "远端 grok2api 基础地址",
    "type": "string",
    "default": "",
    "obvious_hint": true,
    "hint": "填写 https://grok.example.com 这类可由 AstrBot 访问的远端地址；不要附加 /v1。"
  }
  ```

  `search_models` 的描述必须明确“英文逗号、左侧优先、最多 12 个”。`client_proxy_url` 使用通用示例 `http://proxy.example:8080`，默认仍为 `""`。

- [ ] **Step 6: 更新运行时代码和全部配置 fixture**

  把 `main.py` 中两处 `bool(cfg.search_model)` 改成 `bool(cfg.search_models)`。更新 `tests/test_service.py`、`tests/test_runtime_wiring.py` 及其他 `_cfg()` fixture，使其输入 4 组嵌套配置并断言 transport/client 收到相同的扁平运行值。

  全仓必须满足：

  Run: `rg -n "\.search_model\b|\"search_model\"|'search_model'" main.py core tests _conf_schema.json`

  Expected: 无结果。`search_models` 命中不属于失败。

- [ ] **Step 7: 运行 Task 1 测试**

  Run: `python -m pytest tests/test_config.py tests/test_schema.py tests/test_service.py tests/test_runtime_wiring.py tests/test_main_contract.py -q`

  Expected: PASS。

---

### Task 2: 从错误响应中安全保留可回退的模型错误码

**Files:**
- Modify: `core/transport.py`
- Modify: `tests/fakes.py`
- Modify: `tests/test_transport.py`

**Interfaces:**
- Produces: `async HTTPTransport._status_error(status: int, resp: aiohttp.ClientResponse, operation: str) -> APIError`。
- Produces: `_extract_safe_error_code(resp) -> str`，只返回 `model_not_found`、`model_not_allowed` 或空字符串。
- Test helper: `async _post_search(transport: HTTPTransport) -> dict`，固定使用不重试的 `/v1/responses` POST。
- Keeps: 非模型错误继续映射为现有稳定代码；POST 的 5xx、网络错误、超时和无效 2xx 仍先变成 `AmbiguousSubmissionError`。

- [ ] **Step 1: 写模型错误码与泄漏回归测试**

  在 `tests/test_transport.py` 增加 `import json` 和以下 helper；测试沿用现有 `_make()`，不引入不存在的 fixture：

  ```python
  async def _post_search(transport: HTTPTransport) -> dict:
      return await transport.request_json(
          "POST",
          "/v1/responses",
          json_body={"model": "test-model", "input": "test"},
          timeout_seconds=5,
          retry_policy=_policy(allow=False, attempts=1),
          operation="搜索",
      )
  ```

  再增加：

  ```python
  @pytest.mark.asyncio
  @pytest.mark.parametrize(
      ("status", "upstream_code"),
      [(403, "model_not_allowed"), (404, "model_not_found")],
  )
  async def test_model_error_code_is_preserved(status, upstream_code):
      transport, session = _make()
      secret = "upstream detail with g2a_secret"
      session.push(FakeResponse(
          status=status,
          body=json.dumps({"error": {"code": upstream_code, "message": secret}}),
      ))
      with pytest.raises(APIError) as caught:
          await _post_search(transport)
      assert caught.value.code == upstream_code
      assert secret not in str(caught.value)
      assert "g2a_secret" not in str(caught.value)


  @pytest.mark.asyncio
  async def test_unknown_or_invalid_error_code_uses_stable_mapping():
      transport, session = _make()
      session.push(FakeResponse(
          status=403,
          body=json.dumps({"error": {"code": "bad code with spaces", "message": "raw"}}),
      ))
      with pytest.raises(APIError) as caught:
          await _post_search(transport)
      assert caught.value.code == "auth_error"
      assert "raw" not in str(caught.value)
  ```

  再增加大于 64 KiB、非 JSON、`error` 非对象、`code` 非字符串四种输入，全部断言使用状态码稳定映射且不泄漏 body。

- [ ] **Step 2: 运行测试并确认当前失败**

  Run: `python -m pytest tests/test_transport.py -q`

  Expected: FAIL，因为当前 `_status_error()` 不读取结构化 `error.code`。

- [ ] **Step 3: 实现 64 KiB 有界错误码提取**

  在 `core/transport.py` 增加：

  ```python
  import json
  import re

  _MAX_ERROR_BODY_BYTES = 64 * 1024
  _SAFE_ERROR_CODE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
  _MODEL_FALLBACK_CODES = frozenset({"model_not_found", "model_not_allowed"})
  ```

  `_extract_safe_error_code()` 先检查 `content_length`，再用 `await resp.content.read(_MAX_ERROR_BODY_BYTES + 1)`；超限、解码失败、JSON 形状错误、正则不匹配均返回空字符串。即使正则通过，也只返回 `_MODEL_FALLBACK_CODES` 中的值。不得调用 `resp.text()`，不得记录 body 或 `error.message`。

  把 `_status_error()` 改为异步，并在 `request_json()` 与 `download()` 中使用 `raise await self._status_error(...)`。模型错误使用固定中文消息：

  ```python
  if upstream_code == "model_not_found":
      return APIError(status, upstream_code, "搜索模型不存在")
  if upstream_code == "model_not_allowed":
      return APIError(status, upstream_code, "当前 Client Key 无权使用该搜索模型")
  ```

- [ ] **Step 4: 扩展 fake 响应为有界流接口**

  `tests/fakes.py::FakeResponse` 必须暴露 `content_length` 和 `content.read(n)`，并保证同一个 body 不会通过测试辅助层绕过字节上限。保留现有 `json()` 行为供 200 响应测试使用。

- [ ] **Step 5: 证明模糊提交仍不回退到普通 APIError**

  运行：

  `python -m pytest tests/test_transport.py -q -k "model_error or ambiguous or invalid_2xx or timeout or five"`

  Expected: PASS；搜索 POST 的 500、超时、连接断开和无效 200 JSON仍是 `AmbiguousSubmissionError`。

---

### Task 3: 缓存模型目录并纯函数筛选可见候选

**Files:**
- Create: `core/search_models.py`
- Modify: `core/client.py`
- Create: `tests/test_client_models.py`
- Create: `tests/test_search_models.py`

**Interfaces:**
- Produces: `catalog_model_id(configured_model: str) -> str`。
- Produces: `partition_visible_models(configured: tuple[str, ...], catalog: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]`，返回 `(visible, not_visible)`。
- Produces: `Grok2APIClient.list_models(*, force_refresh: bool = False) -> tuple[str, ...]`，成功结果缓存 300 秒。
- Test helpers: `FakeClock` 提供 `__call__()`/`advance()`；`FakeJSONTransport` 提供可脚本化的 `request_json()` 与 `call_count`。
- Keeps: 实际搜索 POST 始终发送用户配置的原字符串，不发送归一后的目录 ID。

- [ ] **Step 1: 写 Provider 前缀与顺序筛选测试**

  新建 `tests/test_search_models.py`：

  ```python
  def test_partition_preserves_config_order_and_provider_value():
      configured = ("Build/grok-4.5", "grok-chat-fast", "missing")
      visible, missing = partition_visible_models(
          configured,
          ("grok-chat-fast", "grok-4.5"),
      )
      assert visible == ("Build/grok-4.5", "grok-chat-fast")
      assert missing == ("missing",)


  def test_catalog_match_is_case_sensitive():
      visible, missing = partition_visible_models(("Grok-4.5",), ("grok-4.5",))
      assert visible == ()
      assert missing == ("Grok-4.5",)
  ```

  `catalog_model_id()` 只用最后一个 `/` 后的非空部分做目录匹配；`Build/grok-4.5` -> `grok-4.5`。实际候选值不得被重写。

- [ ] **Step 2: 写目录缓存、过期与并发测试**

  `tests/test_client_models.py` 先定义以下可控 helper，不依赖真实 aiohttp：

  ```python
  class FakeClock:
      def __init__(self, value: float):
          self.value = value

      def __call__(self) -> float:
          return self.value

      def advance(self, seconds: float) -> None:
          self.value += seconds


  class FakeJSONTransport:
      def __init__(self, responses):
          self.responses = list(responses)
          self.call_count = 0

      async def request_json(self, *args, **kwargs):
          self.call_count += 1
          await asyncio.sleep(0)
          value = self.responses.pop(0)
          if isinstance(value, BaseException):
              raise value
          return value

      async def close(self) -> None:
          return None
  ```

  然后写缓存测试：

  ```python
  @pytest.mark.asyncio
  async def test_model_catalog_is_cached_for_300_seconds():
      clock = FakeClock(100.0)
      transport = FakeJSONTransport([
          {"data": [{"id": "grok-4.5"}]},
          {"data": [{"id": "grok-chat-fast"}]},
      ])
      client = Grok2APIClient(transport, monotonic=clock)
      assert await client.list_models() == ("grok-4.5",)
      clock.advance(299.0)
      assert await client.list_models() == ("grok-4.5",)
      assert transport.call_count == 1
      clock.advance(1.0)
      assert await client.list_models() == ("grok-chat-fast",)
      assert transport.call_count == 2


  @pytest.mark.asyncio
  async def test_concurrent_refresh_uses_one_successful_get():
      transport = FakeJSONTransport([{"data": [{"id": "grok-4.5"}]}])
      client = Grok2APIClient(transport, monotonic=lambda: 100.0)
      results = await asyncio.gather(client.list_models(), client.list_models())
      assert results == [("grok-4.5",), ("grok-4.5",)]
      assert transport.call_count == 1
  ```

  另测：`force_refresh=True` 绕过新鲜缓存；过期刷新失败时抛原 `PluginError`，不返回旧目录；重复/空模型 ID 被去除并按字符串排序。

- [ ] **Step 3: 运行测试并确认当前失败**

  Run: `python -m pytest tests/test_search_models.py tests/test_client_models.py -q`

  Expected: FAIL，因为新模块、TTL 和锁尚不存在。

- [ ] **Step 4: 实现纯函数候选分区**

  `core/search_models.py` 不导入 AstrBot、aiohttp、client 或 service，只包含：

  ```python
  def catalog_model_id(configured_model: str) -> str:
      _, separator, suffix = configured_model.rpartition("/")
      return suffix if separator and suffix else configured_model


  def partition_visible_models(configured, catalog):
      visible_ids = set(catalog)
      visible: list[str] = []
      missing: list[str] = []
      for model in configured:
          if model in visible_ids or catalog_model_id(model) in visible_ids:
              visible.append(model)
          else:
              missing.append(model)
      return tuple(visible), tuple(missing)
  ```

- [ ] **Step 5: 在 client 实现双重检查的 300 秒缓存**

  `Grok2APIClient.__init__()` 新增可选 `monotonic=None`，并初始化：

  ```python
  self._monotonic = monotonic or time.monotonic
  self._models_cache: tuple[str, ...] = ()
  self._models_cache_expires_at = 0.0
  self._models_cache_lock = asyncio.Lock()
  ```

  `list_models(force_refresh=False)` 在锁外和锁内各检查一次 `now < expires_at`。只有 GET 成功且响应解析完成后才同时更新 cache 和 `expires_at = monotonic() + 300.0`；异常时不延长 TTL，不返回过期目录。`force_refresh=True` 必须仍进入同一把锁，避免与正常刷新并发写缓存。

- [ ] **Step 6: 运行 Task 3 测试**

  Run: `python -m pytest tests/test_search_models.py tests/test_client_models.py tests/test_client_images.py tests/test_client_video.py -q`

  Expected: PASS，且现有图片/视频 client 行为不变。

---

### Task 4: 在服务层实现严格的有序搜索回退

**Files:**
- Modify: `core/service.py`
- Modify: `core/observability.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Consumes: `PluginConfig.search_models`、`Grok2APIClient.list_models()`、`Grok2APIClient.search(query, model=..., required=...)`。
- Consumes: `partition_visible_models()`。
- Produces: `GrokService.search()` 返回第一个成功且 `search_performed=True` 的 `SearchResult`。
- Produces: 全部明确不可用时抛 `PluginError(code="search_models_exhausted")`。
- Test helpers: `ScriptedSearchClient` 脚本化目录/搜索结果；`_make_scripted_service()` 只替换 client，继续使用真实 `PluginConfig`、`MediaWorkspace`、`DeliveryAdapter`。

- [ ] **Step 1: 增加可断言调用顺序的 service fake**

  在 `tests/test_service.py` 增加所需错误类型导入，并定义：

  ```python
  class ScriptedSearchClient:
      def __init__(self, *, models=(), models_error=None, search_results=()):
          self.models = tuple(models)
          self.models_error = models_error
          self.search_results = list(search_results)
          self.list_models_calls = 0
          self.search_calls: list[str] = []

      async def list_models(self, *, force_refresh: bool = False):
          self.list_models_calls += 1
          if self.models_error is not None:
              raise self.models_error
          return self.models

      async def search(self, query, *, model, required=True):
          self.search_calls.append(model)
          value = self.search_results.pop(0)
          if isinstance(value, BaseException):
              raise value
          return value

      async def close(self):
          return None


  def _search_result(model: str) -> SearchResult:
      return SearchResult(
          response_id=f"resp-{model}",
          model=model,
          status="completed",
          text="answer",
          sources=(),
          search_performed=True,
      )


  def _make_scripted_service(tmp_path, client, search_models):
      cfg = _cfg(capability_settings={
          "search_models": ",".join(search_models),
      })
      workspace = MediaWorkspace(tmp_path)
      return GrokService(cfg, client, workspace, DeliveryAdapter(workspace))
  ```

  `ScriptedSearchClient.search()` 在没有脚本结果时应让测试立即失败；每个成功路径测试都必须显式传入 `_search_result(...)`，不能让 fake 暗中制造默认成功。

- [ ] **Step 2: 写目录筛选与优先顺序测试**

  在 `tests/test_service.py` 的 fake client 中分别记录 `list_models_calls` 与 `search_calls`：

  ```python
  @pytest.mark.asyncio
  async def test_search_skips_catalog_missing_models_and_uses_first_visible(tmp_path):
      client = ScriptedSearchClient(
          models=("grok-4.3", "grok-4.5"),
          search_results=(_search_result("grok-4.5"),),
      )
      service = _make_scripted_service(
          tmp_path, client, ("missing", "grok-4.5", "grok-4.3")
      )
      result = await service.search(FakeEvent(), "current question")
      assert result.model == "grok-4.5"
      assert client.search_calls == ["grok-4.5"]


  @pytest.mark.asyncio
  async def test_catalog_failure_tries_original_first_model_only_on_success(tmp_path):
      client = ScriptedSearchClient(
          models_error=PluginError("目录失败", code="network_error"),
          search_results=(_search_result("first"),),
      )
      service = _make_scripted_service(tmp_path, client, ("first", "second"))
      await service.search(FakeEvent(), "current question")
      assert client.search_calls == ["first"]
  ```

- [ ] **Step 3: 写仅四类结果允许切换的参数化测试**

  ```python
  @pytest.mark.asyncio
  @pytest.mark.parametrize(
      "first_error",
      [
          APIError(404, "model_not_found", "missing"),
          APIError(403, "model_not_allowed", "forbidden"),
          SearchNotPerformedError(),
      ],
  )
  async def test_explicit_model_failure_advances_to_next(tmp_path, first_error):
      client = ScriptedSearchClient(
          models=("first", "second"),
          search_results=[first_error, _search_result("second")],
      )
      service = _make_scripted_service(tmp_path, client, ("first", "second"))
      result = await service.search(FakeEvent(), "question")
      assert result.model == "second"
      assert client.search_calls == ["first", "second"]
  ```

  `not_visible` 已由 Step 2 证明为跳过且不发 POST。

- [ ] **Step 4: 写禁止切换测试**

  参数化覆盖：`APIError(401, "auth_error", ...)`、`APIError(429, "rate_limited", ...)`、`APIError(400, "http_error", ...)`、`AmbiguousSubmissionError`、`ProtocolError`、`PluginError(code="network_error")`、`asyncio.TimeoutError`。每种情况均断言：

  ```python
  with pytest.raises(type(first_error)):
      await service.search(FakeEvent(), "question")
  assert client.search_calls == ["first"]
  ```

  对 `asyncio.CancelledError` 单独断言原样向上传播，不能转换为耗尽错误。

- [ ] **Step 5: 写全部耗尽和每次恢复优先级测试**

  覆盖三种耗尽组合：目录过滤后无候选；两个模型分别 `model_not_found`/`model_not_allowed`；两个模型均 `search_not_performed`。断言错误 code 为 `search_models_exhausted`，错误消息不含 query、Key、原始响应。再调用第二次搜索，并让第一候选成功，断言第二次仍从第一候选开始。

- [ ] **Step 6: 运行搜索服务测试并确认当前失败**

  Run: `python -m pytest tests/test_service.py -q -k search`

  Expected: FAIL，因为当前服务只调用一个 `search_model`。

- [ ] **Step 7: 实现目录筛选和顺序循环**

  `GrokService.search()` 保持一个 `_search_sem` 配额覆盖整次候选循环，不能让每个候选重新抢 semaphore。目录获取失败只记录稳定错误码并使用原候选；目录成功则跳过 `not_visible`。核心异常边界为：

  ```python
  try:
      result = await self._client.search(query, model=model, required=required)
  except APIError as exc:
      if exc.code not in {"model_not_found", "model_not_allowed"}:
          raise
      self._log_search_skip(model, index, exc.code)
      continue
  except SearchNotPerformedError:
      self._log_search_skip(model, index, "search_not_performed")
      continue
  ```

  不得增加 `except PluginError: continue` 或 `except Exception: continue`。成功结果立即返回；禁止把成功模型写回配置或移动列表顺序。

  耗尽消息最多 200 字符：显示候选总数和按顺序能容纳的模型名，超出部分以 `等 N 个模型` 收尾；code 固定为 `search_models_exhausted`。

- [ ] **Step 8: 增加安全搜索选择日志字段**

  `core/observability.py::ALLOWED_FIELDS` 增加 `model`、`model_index`、`reason`、`candidate_count`、`catalog_count`。只有 `self._config.debug_mode` 为真时记录：

  - `search_model_skipped`: `model`, `model_index`, `reason`。
  - `search_model_selected`: `model`, `model_index`。
  - `search_models_exhausted`: `candidate_count`。

  日志不得包含 query。HTTP/请求模糊状态仍使用 transport 的稳定事件，不得因搜索回退被降级为普通 warning。

- [ ] **Step 9: 运行 Task 4 测试**

  Run: `python -m pytest tests/test_service.py tests/test_observability.py tests/test_search.py -q`

  Expected: PASS。

---

### Task 5: 同步状态命令、LLM Tool 和目录可见性

**Files:**
- Modify: `core/models.py`
- Modify: `core/service.py`
- Modify: `main.py`
- Modify: `tests/test_service.py`
- Modify: `tests/test_main_commands.py`
- Modify: `tests/test_main_contract.py`
- Modify: `tests/test_tools.py`

**Interfaces:**
- Produces: `StatusReport.configured_search_models: tuple[str, ...]`。
- Produces: `StatusReport.available_search_models: tuple[str, ...]`。
- Produces: `StatusReport.unavailable_search_models: tuple[str, ...]`。
- Produces: `StatusReport.catalog_available: bool`，保留 `visible_models` 表示完整远端目录。
- Keeps: `SearchToolPolicy.has_model` 是布尔值，来源改为 `bool(cfg.search_models)`。

- [ ] **Step 1: 写状态不执行搜索 POST 的测试**

  在 `tests/test_service.py` 增加：

  ```python
  @pytest.mark.asyncio
  async def test_status_partitions_candidates_without_search_probe(tmp_path):
      client = ScriptedSearchClient(models=("grok-chat-fast", "grok-4.5"))
      service = _make_scripted_service(
          tmp_path,
          client,
          ("Build/grok-4.5", "missing", "grok-chat-fast"),
      )
      report = await service.status(FakeEvent())
      assert report.configured_search_models == (
          "Build/grok-4.5", "missing", "grok-chat-fast"
      )
      assert report.available_search_models == (
          "Build/grok-4.5", "grok-chat-fast"
      )
      assert report.unavailable_search_models == ("missing",)
      assert report.catalog_available is True
      assert client.search_calls == []
  ```

  再测空 `api_base_url` 或空 Key：`list_models_calls == 0`，`catalog_available is False`，`error_code` 分别为 `api_base_url_missing`、`client_key_missing`。

- [ ] **Step 2: 写 `/g2状态` 与 Tool 契约测试**

  在命令测试中 fake `StatusReport`，断言状态文本按配置顺序显示“搜索候选”“当前可见候选”“当前不可见候选”；目录失败显示稳定错误码而不是“0 个可见模型”。断言帮助只显示“搜索：可用/未配置”，不展开模型名单。

  `tests/test_tools.py` 增加 `search_models=()` 禁止 Tool、非空 tuple 允许 Tool 的测试；不能让 Tool 自己循环模型，仍只调用 `service.search()` 一次。

- [ ] **Step 3: 运行状态与 Tool 测试并确认当前失败**

  Run: `python -m pytest tests/test_service.py tests/test_main_commands.py tests/test_main_contract.py tests/test_tools.py -q -k "status or help or tool"`

  Expected: FAIL，因为 `StatusReport` 尚无候选分区字段，`main.py` 仍引用单值模型。

- [ ] **Step 4: 扩展 `StatusReport` 并复用同一分区函数**

  `GrokService.status()` 必须复用 `partition_visible_models()`，不能复制另一套 Provider 前缀规则。连接配置不完整时直接构造报告，不调用 client。目录 GET 失败时：

  ```python
  catalog_available = False
  available_search_models = ()
  unavailable_search_models = ()
  error_code = exc.code
  ```

  目录成功但返回空列表仍是 `catalog_available=True`，所有已配置候选进入 `unavailable_search_models`。

- [ ] **Step 5: 更新 `main.py` 展示和 Tool 条件**

  `_register_search_tool()` 与 `_tool_allowed_for_event()` 都使用 `bool(cfg.search_models)`。`/g2状态` 输出以下稳定结构：

  ```text
  Grok2API Sub 状态：
  - Base URL: 未配置
  - TLS 校验: 开
  - Client Key: 未配置
  - 已启用能力: 无
  - 搜索候选: grok-chat-fast -> grok-4.3 -> grok-4.5 -> grok-build-0.1
  - 当前可见候选: 未检查
  - 当前不可见候选: 未检查
  - 模型目录: 未检查（client_key_missing）
  ```

  已配置连接时 Base URL 可显示，但不得显示代理凭据或 Client Key。候选超过状态消息合理长度时最多展示前 8 个并追加总数。

- [ ] **Step 6: 运行 Task 5 测试**

  Run: `python -m pytest tests/test_service.py tests/test_main_commands.py tests/test_main_contract.py tests/test_tools.py -q`

  Expected: PASS。

---

### Task 6: 同步文档并执行发布前总验证

**Files:**
- Modify: `README.md`
- Modify: `docs/configuration.md`
- Modify: `docs/architecture.md`
- Modify: `docs/commands.md`
- Modify: `docs/testing.md`
- Modify: `CHANGELOG.md`
- Modify: `metadata.yaml` only if the implementation changes the documented version

**Interfaces:**
- Documents: 4 个配置分组、远端部署方向、默认模型顺序、安全回退矩阵、状态含义。
- Does not document: 真实远端地址、真实 Client Key、本地 `3067` 默认代理、真实生成媒体。

- [ ] **Step 1: 更新 README 的首次配置和模型说明**

  README 必须按以下顺序指导用户：

  1. 在 `connection_settings.api_base_url` 填写远端 grok2api 根地址。
  2. 在 `connection_settings.client_api_key` 填写专用 Client Key，不使用管理员 JWT。
  3. 保持 `verify_tls=true`；只有明确的测试环境才关闭。
  4. `client_proxy_url` 仅控制 AstrBot 到远端 API 的链路，不是 grok2api 服务端出口代理。
  5. `capability_settings.search_models` 左侧优先，以英文逗号分隔，清空即禁用搜索。

  内置默认列表写成单行代码；另列 3 个 `grok-4.20-*` 可选模型，并注明模型列表来自一次远端实例快照，实际可用性以用户自己的 `/v1/models` 和 Client Key 权限为准。不得宣称这些模型永久存在或必然支持搜索。

- [ ] **Step 2: 写清安全回退矩阵和状态行为**

  `docs/architecture.md` 使用与代码一致的矩阵：四种允许继续，其余全部停止。明确 `/v1/models` 只证明可见性，不证明搜索能力；完成的 `web_search_call` 才证明本次执行了联网搜索。

  `docs/commands.md` 说明 `/g2状态` 只做模型目录 GET、不消耗搜索生成；`/g2帮助` 只展示能力是否配置。`docs/configuration.md` 用 4 组路径写配置表，不再保留扁平键文档。

- [ ] **Step 3: 更新测试文档与变更记录**

  `docs/testing.md` 增加配置/schema、模型缓存、严格回退、状态无 POST 四类测试命令。`CHANGELOG.md` 在未发布版本下记录：多搜索模型、4 分组配置、远端地址空默认、无旧配置迁移。

- [ ] **Step 4: 执行敏感内容和旧字段扫描**

  Run:

  ```powershell
  rg -n "127\.0\.0\.1:3067|g2a_[A-Za-z0-9_]+" --glob '!testignore/**' .
  rg -n "\.search_model\b|\"search_model\"|'search_model'|config_schema_version" main.py core tests _conf_schema.json README.md docs
  ```

  Expected: 两条命令均无结果。若 `CHANGELOG.md` 需要描述字段更名，只允许在那里出现反引号包裹的旧字段名。

- [ ] **Step 5: 执行针对性验证**

  Run:

  ```powershell
  python -m json.tool _conf_schema.json > $null
  python -m pytest tests/test_config.py tests/test_schema.py tests/test_transport.py tests/test_client_models.py tests/test_search_models.py tests/test_service.py tests/test_tools.py tests/test_main_commands.py -q
  ```

  Expected: JSON 合法，针对性测试全部 PASS。

- [ ] **Step 6: 执行全量验证**

  Run:

  ```powershell
  python -m compileall -q main.py core tests
  ruff check .
  ruff format --check .
  python -m pytest -q
  git diff --check
  ```

  Expected: 全部退出码 0。现有基线为 202 passed、2 warnings；新增测试后通过数应增加，不能通过删除原测试维持基线。

- [ ] **Step 7: 人工检查最终 diff**

  只检查并记录，不提交或推送：

  - `_conf_schema.json` 顶层只有 4 组。
  - 正式默认值没有本地服务或本地代理。
  - 搜索默认顺序完全一致。
  - 任意模糊 POST 失败都不会触发第二模型。
  - 状态命令没有真实搜索调用。
  - 日志没有 query、Key、错误 body。
  - `testignore/` 的真实凭据和媒体未被纳入实现、测试或提交范围。

---

## Review Gates

1. **Task 1 gate:** 运行时配置只有 `search_models` tuple，Schema 顶层只有 4 组，插件在远端地址未配置时仍能初始化。
2. **Task 2 gate:** 只有两个精确模型错误码能穿过 transport，其他 body 字段完全丢弃。
3. **Task 3 gate:** 目录缓存 300 秒、并发成功刷新只发一个 GET、Provider 前缀不改变 POST 模型字符串。
4. **Task 4 gate:** 允许回退与禁止回退的参数化测试均通过，未出现宽泛 catch 后继续。
5. **Task 5 gate:** 状态只做 GET，Tool 只委派 service，帮助不暴露模型细节。
6. **Task 6 gate:** 文档、Schema、运行时、测试完全一致，全量验证退出码为 0。

## Explicit Non-Goals

- 不实现旧配置迁移。
- 不检测或自动修复旧扁平配置文件。
- 不给媒体能力增加候选模型列表。
- 不做定时健康检查或真实搜索探针。
- 不根据历史成功率、延迟或费用动态排序。
- 不在插件中管理 grok2api 上游账号、管理员 JWT、SSO/OAuth 或服务端出口代理。
- 不在本计划中提交、推送、发布或部署。
