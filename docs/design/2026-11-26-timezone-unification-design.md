# 全栈时区统一（设计）

- 日期：2026-11-26
- 状态：草案，待确认（Neon 排查起草，Claude 核实：见 `~/.agents/handoffs/2026-11-26-neon-timezone-audit/`）
- 计划：[2026-11-26-timezone-unification-plan.md](../plans/2026-11-26-timezone-unification-plan.md)
- 涉及：`dataagent-backend`（配置、数据库连接、写入与查询、API 序列化、Alembic 迁移、系统提示词与沙箱环境变量）、`dataagent-frontend`（主应用、Widget 与 `packages/agent-conversation`）、`deploy`（Compose 与 `.env.example`）、`tools/dataagent-evals`（评测脚本）；`dataagent-runtime-pi` 协议无需改动

## 1. 现状（origin/main `402af6d` 核实）

当前全栈同时存在 **4 种时钟/时区口径**，且彼此混用：

1. **数据库写入两条路径**：
   - **路径 A（MySQL `CURRENT_TIMESTAMP` / `NOW()`）**：跟随 MySQL 会话时区（本地 `dataagent-mysql` 为 `SYSTEM / CST +08:00`，生产库时区未知）。涵盖 `da_agent_topic`、`da_agent_task`、`da_agent_message`、`da_agent_sdk_record.created_at`、`da_user.created_at/updated_at`、`da_agent_message_schedule_log.finished_at` 等 47 个 `DATETIME` 列，以及全库唯一一个 `TIMESTAMP` 列 `da_agent_session_checkpoint.updated_at`。
   - **路径 B（Python naive UTC）**：`core/task_submission_service.py:18` `current_utc_naive`、`core/accounts/store.py:33` `_utc_now`、`core/remote_mcp_tokens.py:40` `_now`、`core/eval_store.py:22` `_now_str`、`core/pi_event_writer.py:15` `_parse_occurred_at`。涵盖 `da_agent_message_schedule.last_run_at/next_run_at`、`da_agent_message_schedule_log.started_at`、`da_mcp_access_token.*`、`da_user.last_login_at`、`da_agent_sdk_record.occurred_at`、`eval_*`。
   - **路径 C（后端容器/宿主机本地时区）**：`core/topic_task_store.py:75` `_parse_client_ts`（调用 `.astimezone().replace(tzinfo=None)` 写 `da_agent_widget_event.client_ts`）和 `core/skill_admin_service.py:99` `_now_iso`（调用 `datetime.now()` 写 `da_model_provider.validated_at` 与 `model_detections.checked_at`）。
2. **数据库连接分散**：全后端有 7 处独立的 `pymysql.connect(...)`（`topic_task_store`、`agent_profile_service`、`skill_admin_store`、`runtime_registry_store`、`accounts/store`、`remote_mcp_tokens`、`eval_store`）和 1 处 `alembic/env.py` 的 `create_engine`，均未设置 MySQL 会话 `time_zone`。
3. **API 序列化不统一**：
   - `topic_task_store`、`skill_admin_store`、`agent_profile_service`、`runtime_registry_store`、`experience` 输出无时区后缀的 naive ISO（`2026-11-26T09:00:00`）；`eval_store` 直接返回 naive `datetime` 对象由 Pydantic 输出无 `Z` 字符串，趋势接口甚至返回 `str(dt)` 空格格式。
   - `accounts/store.py:39` 与 `remote_mcp_tokens.py:53` 强制拼 `+ "Z"`——前者把 MySQL 写入的 `+08:00` `da_user.created_at/updated_at` 误标成了 `Z`。
   - `interaction_policy`、`mcp_admin_service`、`stream_events`、`topic_files`、`topic_task_store`（`decided_at`/`answered_at`）输出 `+00:00`。
4. **前端解析与展示各自为政**：存在 4 套写法——字符串切片 `replace('T',' ').slice(0,19)` / `slice(5,10)`（忽略时区）、`dayjs(val).format(...)`（按浏览器本地时区）、`new Date(val).toLocaleString('zh-CN')`（按浏览器本地时区）、直接读取 `Date.getHours()`（按浏览器本地时区）。筛选条件（`FeedbackView.vue:117-118`）向后端发送无时区本地日期字符串。
5. **沙箱与系统提示词**：沙箱容器 `oda-sandbox:latest` 内置 `tzdata` 但未设 `TZ`，默认跑在 UTC；`core/agent_runtime.py:31-45` 的系统提示词不含当前日期，模型靠在沙箱执行 `date` 获取日期，导致北京时间 00:00–08:00 模型误以为是“昨天”。
6. **每日经验总结与评测**：
   - 经验总结通过 `core/experience.py:474` `db_now()`（`SELECT NOW()`）和无时区日期边界 `datetime.combine(day, min)` 查询 `da_agent_task.finished_at`，强绑定 MySQL 自身时钟。
   - `tools/dataagent-evals/{builtin,deepeval,opik}/run.py` 硬编码 `dt.timezone(dt.timedelta(hours=8))` 判定相对日期 SQL。

## 2. 已确认的真实缺陷

以下缺陷均已在源码或本地 MySQL（`dataagent` / `dataagent_qa`）实测核实：

1. **同行字段相差 8 小时**：
   - `da_user`（`core/accounts/store.py`）：`updated_at`（`18:28:49`，+8）与 `last_login_at`（`10:28:49`，UTC）在同一行差 480 分钟；且 `_to_iso` 给所有字段无差别拼 `Z`，导致 `created_at` 变成未来 8 小时。
   - `da_agent_message_schedule_log`（`task_coordinator.py:598` vs `topic_task_store.py:3081`）：`started_at` 为 UTC，`finished_at` 与 `created_at` 为 `CURRENT_TIMESTAMP`（+8），耗时虚增 8 小时。
   - `da_agent_sdk_record`（`pi_event_writer.py:25` vs DB default）：`occurred_at` 为 UTC，`created_at` 为 +8，实测差 480 分钟。
   - `da_agent_message_schedule`：`last_run_at` / `next_run_at` 为 UTC，`created_at` / `updated_at` 为 +8。
   - `da_agent_widget_event` 与 `da_model_provider`：容器内 Python 写入的 `client_ts` / `validated_at` 为 UTC，同行的 `created_at` 为 +8（实测差 480 分钟）。
   - `eval_dataset`（`core/eval_store.py:103,123,146`）：插入时 `created_at`/`updated_at` 写 UTC，更新时走 `ON UPDATE CURRENT_TIMESTAMP` 变为 +8。
2. **用户管理页最后登录时间直接少 8 小时**：`UsersView.vue:188` 对已带 `Z` 的 `last_login_at` 做 `.replace('T',' ').slice(0,19)`，截掉 `Z` 直接展示 UTC 钟面时间。
3. **Widget 历史列表出现「-1天前」**（对应 `docs/TODO.md` 已知问题）：`WidgetChat.vue:578,597-600` 用 `new Date()` 按浏览器本地时区解析后端无 `Z` 的 +8 时间，非 +8 浏览器（如 PST）会把 +8 早上算成“明天”，`diffDays = -1` 命中 `diffDays <= 7` 分支。
4. **发消息后会话在侧边栏倒跳**：`NL2SqlChatV2.vue:1079`、`useNl2SqlChat.js:213` 乐观更新 `topic.updated_at = new Date().toISOString()`（UTC），而列表中其他话题为无 `Z` 的 +8 字符串；`topicHelpers.js:41` 使用 `localeCompare` 字典序比较，导致刚发完消息的话题反而排到过去 8 小时内的旧话题下方。
5. **模型在北京时间 00:00–08:00 日期错一天**：沙箱默认 UTC 且提示词无当前日期，“今天 / 近 7 天 / 本月”等 NL2SQL 查询和 `generate_report.py:193` 报告生成时间全偏 8 小时。
6. **评测页面时间与排序错位**：`eval_store.py` 写入 UTC 却无 `Z` 返回，前端 `dayjs(val)` 按浏览器本地时区解析，北京时间页面直接慢 8 小时；`eval_run.started_at` 在无传参时从本地时区的 `run_label` 解析，再与 UTC 的 `ingested_at` 混排 `ORDER BY COALESCE(started_at, ingested_at)`。
7. **经验总结日界线与生产 DB 时区强耦合**：若生产 DB 为 UTC，`db_now()` 与 `finished_at` 均按 UTC 切日，北京时间 00:00–08:00 的对话被归入“前一天”，且 `digest_hour=2` 实际在北京时间 10:00 触发。

## 3. 目标与非目标

### 目标

按通行最佳实践（用户 2026-11-26 确认）：**时间只在边界处换算，中间一律带时区**。一步到位交付（无过渡期、无兼容双轨）。

1. **区分时刻与日历时间**：消息、任务、登录等“发生在某一瞬间”的值是时刻，统一用 UTC 表示；“某一天、今天、每天 9 点”是日历时间，必须带 IANA 时区名（不写死 `+8`）。日历时间只出现在：每日总结的“前一天”、定时任务（已有时区列）、提示词的“今天”、沙箱的 `date`。
2. **代码里只有带时区的时间**：后端唯一的当前时间来源是 `utc_now()`（aware UTC，可在测试中替换）；不带时区的 datetime 视为缺陷，不允许出现在业务代码里。由此消除各处“这个时间是哪个时区”的判断。
3. **三个边界，各一处换算**：
   - 数据库：唯一连接入口设会话时区 UTC，并注册 PyMySQL 转换器——写入时把 aware 时间换成 UTC、传入 naive 时间直接报错；读出的 DATETIME 附上 UTC 时区。（已验证 PyMySQL 2.2.8 默认会丢掉 tzinfo：`09:00+08:00` 被写成 `09:00`。）
   - 接口：输出统一 RFC 3339（`Z`）；时间入参用 Pydantic `AwareDatetime`（本地 2.14 已支持），不带偏移返回 422（FastAPI 参数校验错误）。
   - 界面：前端解析带偏移的时间，按浏览器本地时区显示，统一一个格式化模块。
4. **日期范围用半开区间** `[start, end)`。
5. **谁的日历谁换算**：用户在界面选的日期（反馈筛选）是用户本地的日子，由前端换成带偏移的起止时刻；系统按业务日历做的事（每日总结、定时任务默认值、提示词日期、沙箱 `TZ`）由后端用 `DATAAGENT_TIMEZONE` 计算。

### 非目标

- 不支持“按用户个人偏好切换时区”（全系统统一一个部署级业务时区；前端接口预留 `timezone` 参数，将来扩展成本低）。
- 不修改现有 `DATETIME` 列类型为 `TIMESTAMP`（避免 2038 问题与大表锁表重建），也不改已有 `da_agent_session_checkpoint.updated_at`（`TIMESTAMP` 在会话 `time_zone='+00:00'` 下天然读写 UTC）。
- 不调整 `logging_config.py` 日志格式与轮转策略（已明确为 UTC + `Z`、UTC 零点轮转，见 `deploy/README.md:61-62`）。
- 不改 `da_agent_message_schedule.timezone` 列的语义（允许单个定时任务自定义时区，未指定时默认取 `DATAAGENT_TIMEZONE`）。

## 4. 方案设计

### 4.1 统一配置：`DATAAGENT_TIMEZONE`

在 `dataagent-backend/config.py` `Settings` 新增：

```python
dataagent_timezone: str = "Asia/Shanghai"
```

- 启动时通过 Pydantic `@field_validator` 用 `ZoneInfo(v)` 校验，非法 IANA 时区名直接启动失败（fail-fast）。
- 2026-11-26 删减：不加 `DATAAGENT_LEGACY_DB_TZ_OFFSET_SECONDS`、不向前端下发业务时区、不做评测历史数据的启发式修正；不加 `tzdata` 依赖（已验证后端镜像 `python:3.13-slim-bookworm` 可加载 `Asia/Shanghai`）。
- `deploy/docker-compose.yml` 透传 `DATAAGENT_TIMEZONE: ${DATAAGENT_TIMEZONE:-Asia/Shanghai}`；`deploy/.env.example` 增加注释说明。
- `dataagent_experience_digest_hour` 注释从 `on the database clock` 改为 `in DATAAGENT_TIMEZONE`。

### 4.2 统一数据库连接与 UTC 写入

新建两个极简核心模块：

1. **`core/db.py`** — 唯一 PyMySQL 连接入口：
   ```python
   def connect_mysql(*, database: str | None = None, autocommit: bool = False, io_timeout_seconds: int | None = None):
       cfg = get_settings()
       return pymysql.connect(
           host=cfg.mysql_host,
           port=int(cfg.mysql_port or 3306),
           user=cfg.mysql_user,
           password=cfg.mysql_password,
           database=cfg.session_mysql_database if database is None else database,
           charset="utf8mb4",
           cursorclass=pymysql.cursors.DictCursor,
           autocommit=autocommit,
           # 保留现有系统提示词查询的连接/读/写超时（topic_task_store._connect 的 io_timeout_seconds）
           init_command="SET time_zone = '+00:00'",
       )
   ```
   - 7 处 store（`topic_task_store`、`agent_profile_service`、`skill_admin_store`、`runtime_registry_store`、`accounts/store`、`remote_mcp_tokens`、`eval_store`）的 `_connect()` 全部改为调用 `connect_mysql(...)`。
   - `alembic/env.py` 的 `create_engine` 传入 `connect_args={"init_command": "SET time_zone = '+00:00'"}`，确保后续所有迁移中的 `CURRENT_TIMESTAMP` 也运行在 UTC。
2. **数据库转换器**（在 `core/db.py`，随 `connect_mysql` 传入 `conv`）：
   - 写：`datetime` 带时区 → `astimezone(UTC)` 后按 `YYYY-MM-DD HH:MM:SS[.ffffff]` 写入；不带时区 → 抛 `TypeError`（测试期即暴露漏改的写入点）。
   - 读：`DATETIME` 列解析为 `datetime(..., tzinfo=UTC)`；`TIMESTAMP` 列在会话 UTC 下同样附 UTC。
3. **`core/time_utils.py`** — 只保留：
   - `utc_now() -> datetime`（aware UTC）；
   - `get_business_tz() -> ZoneInfo`；
   - `business_day_bounds(day: date) -> tuple[datetime, datetime]`（业务时区该日 00:00 到次日 00:00，aware）；
   - `to_rfc3339(value: datetime | None) -> str`（aware → UTC → `...Z`；`None` → `""`；naive 抛错）。
   不提供 naive 版本的函数，也不提供“猜测输入时区”的解析函数。

所有 Python 写入点同步修正（全部改用 aware 时间，由数据库转换器统一换成 UTC）：
- `core/topic_task_store.py:_parse_client_ts` → `datetime.fromisoformat`，必须带偏移（Widget 用 `toISOString()` 发送），不带则丢弃该值
- `core/pi_event_writer.py:_parse_occurred_at` → `datetime.fromisoformat`（Pi 发送带 `Z` 的时间），保留 aware 值
- `core/skill_admin_service.py:_now_iso` → `to_rfc3339(utc_now())`
- `core/runtime_registry_store.py:save_provider` → `validated_at` 用 `datetime.fromisoformat` 解析为 aware 后写入（避免带 `Z` 字符串触发 MySQL `1292`）
- `core/eval_store.py` → 写入时间用 `utc_now()`；`started_at` 用 `datetime.fromisoformat` 解析，必须带偏移，否则留空（不再从 `run_label` 推断）。

### 4.3 统一 API 序列化与接口变化

1. **输出**：所有从数据库读出的时间字段经 `to_rfc3339` 输出 `YYYY-MM-DDTHH:MM:SSZ`：删除 `topic_task_store`（含 `get_task_system_prompt`）、`skill_admin_store`、`agent_profile_service`、`runtime_registry_store`、`experience`、`accounts/store`、`remote_mcp_tokens` 各自的 `_to_iso` / `+ "Z"`；`eval_store` 读接口与 `trend_series` 同样经 `to_rfc3339`。运行时生成并原样存入 JSON 的时间戳（`deadline_at`、`decided_at`/`answered_at`、`checked_at`、`ts`、`modified_at`）已是带 `+00:00` 的 RFC 3339，保持不变。
2. **输入**：带时间的请求参数（反馈与管理端会话列表的 `start`/`end` 等）改用 Pydantic `AwareDatetime`，不带偏移返回 422（FastAPI 参数校验错误）。区间语义为 `[start, end)`。
3. **定时任务默认时区**：`MessageScheduleUpsertRequest.timezone` 改为 `Optional[str] = None`；只在**创建**时把缺省值解析为 `get_settings().dataagent_timezone`，并用同一时区计算、保存 `next_run_at`；更新时未传时区则保留已存值。执行路径已读取保存的时区，不需要再到处读配置。
4. `/auth/config` 不新增时区字段：前端不需要业务时区。

### 4.4 日期边界查询与每日经验总结

1. **每日经验总结**：
   - 删除 `core/experience.py:db_now()`。
   - `core/task_coordinator.py:_experience_loop` 改为：
     ```python
     now = datetime.now(get_business_tz())
     if now.hour >= int(self.settings.dataagent_experience_digest_hour):
         day = (now - timedelta(days=1)).date()
     ```
   - `core/experience.py:build_session_records(agent_id, day)` 与 `agents_with_tasks(day)` 改为 `start, end = business_day_bounds(day)`，查询 `k.finished_at >= %s AND k.finished_at < %s`。
2. **管理端时间范围筛选**：`admin_list_topics`、`list_message_feedback` 直接用 aware 的 `start`/`end` 做 `>= start AND < end`。`FeedbackView.vue` 把用户选择的本地日期换成本地当天 00:00 与次日 00:00 的时刻，用 `toISOString()` 发送。不同时区的管理员按各自本地日期筛选，这是预期行为。

### 4.5 沙箱与系统提示词

1. **沙箱 `TZ`**：`core/task_executor.py:129` 在 `runtime_env` 中增加 `"TZ": cfg.dataagent_timezone`。由于 `core/agent_profile_service.py:25` 的 `RESERVED_ENV_KEYS` 已包含 `"TZ"`，智能体自定义 `env_vars` 无法覆盖它。沙箱内的 `date` 命令、Python `datetime.now()`（包括 `report-generation/scripts/generate_report.py:193`）均自动使用业务时区。
2. **系统提示词当前日期**：`core/agent_runtime.py:_build_system_prompt()` 在 `# 运行时上下文` 中增加一行：
   ```
   - 当前日期：2026-11-26（时区：Asia/Shanghai）
   ```
   **取舍说明**：只注入日期与时区、不注入时分秒，避免破坏模型 prompt prefix cache（见 `config.py:116` 注释）；需要精确到时分秒时模型仍可在沙箱执行 `date`。

### 4.6 统一前端格式化模块

1. **主应用与 Widget**：新建 `dataagent-frontend/src/utils/time.js`（浏览器原生 `Intl.DateTimeFormat`）：
   - `formatDateTime` / `formatDate` / `formatShortDateTime` / `formatClockTime`：输入带偏移的时间，按浏览器本地时区显示；
   - `formatRelativeTime`：按本地自然日差计算；未来时间（时钟微偏）返回“刚刚”，根治「-1天前」；
   - `localDayRange(dateStr) -> { start, end }`：本地该日 00:00 与次日 00:00 的 ISO（带 `Z`），供日期筛选使用；
   - `topicHelpers.js` `compareTopicsByRecency` 改为 `Date.parse` 数值比较；
   - 替换散落的格式化：`ExperienceDrawer.vue`、`UsersView.vue`、`FeedbackView.vue`、`KnowledgeMemoryView.vue`、`McpTokensView.vue`、`SkillDetailView.vue`、`Evaluation*.vue`、`NL2SqlChatV2.vue`、`WidgetChat.vue`。
2. **SDK（`packages/agent-conversation`）**：无需改动（已按浏览器本地时间显示，后端改为带 `Z` 后解析正确）。

### 4.7 评测

- 评测数据写入改为 UTC 并经统一序列化输出 `Z`（随 4.2/4.3）。
- 不修正评测表的历史混合时间，也不改三个评测脚本写死的 +8；二者记入 `docs/TODO.md`（2026-11-26 删减）。

## 5. 历史数据迁移设计

新增单次 Alembic 迁移：`20261127_000045_unify_utc_timestamps.py`（`down_revision = "20261126_000044"`，接在写操作审批迁移之后）。

### 5.1 识别生产 DB 原时区（兼容两种情况）

迁移读取数据库全局时区相对 UTC 的偏移（临时 `SET SESSION time_zone = @@global.time_zone`，执行 `SELECT TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())`，再切回 `+00:00`）。不提供手动覆盖开关。

**前提（上线检查）**：历史数据必须一直由当前这个时区的数据库写入。若库曾从其他时区的实例导入（例如 +8 的 dump 恢复到 UTC 实例），检测到的偏移与历史数据不符，此时停止上线、人工处理，不运行迁移。

- **情况 A：生产 DB 本身就是 UTC（`offset == 0`）**：记录日志后直接返回，**零数据改动**。
- **情况 B：生产 DB 为非 UTC（如本地与常见国内部署的 `CST +08:00`，`offset == 28800`）**：对下方“路径 A 列清单”执行 `DATE_SUB(col, INTERVAL :offset SECOND)`。

### 5.2 列级迁移清单（全库 61 个时间列逐列划分）

| 分类 | 表与列 | 迁移动作（当 `offset != 0`） | 原因 |
|---|---|---|---|
| **A. 纯路径 A（须减 `offset`）** | `da_agent_chunk(created_at)`、`da_agent_draft(created_at, updated_at)`、`da_agent_experience(created_at, updated_at)`、`da_agent_grant(created_at)`、`da_agent_message(created_at, updated_at, feedback_at)`、`da_agent_message_queue(consumed_at, created_at, updated_at)`、`da_agent_message_schedule(created_at, updated_at)`、`da_agent_message_schedule_log(finished_at, created_at)`、`da_agent_profile(created_at, updated_at)`、`da_agent_sdk_record(created_at)`、`da_agent_settings(created_at, updated_at)`、`da_agent_task(cancel_requested_at, started_at, heartbeat_at, finished_at, created_at, updated_at)`、`da_agent_topic(created_at, updated_at)`、`da_agent_widget_event(created_at)`、`da_mcp_server(created_at, updated_at)`、`da_model_provider(created_at, updated_at)`、`da_role(created_at, updated_at)`、`da_skill_document(created_at, updated_at)`、`da_skill_document_version(created_at)`、`da_system_prompt(created_at)`、`da_user(created_at, updated_at)`、`da_user_identity(created_at)`、`da_user_role(created_at)` | 统一 `DATE_SUB(col, INTERVAL %s SECOND)`（可空列带 `IF(col IS NULL, NULL, ...)`） | 全部由 MySQL `CURRENT_TIMESTAMP` 写入，存的是原 DB 时区钟面时间 |
| **B. 纯路径 B（不动）** | `da_agent_message_schedule(last_run_at, next_run_at)`、`da_agent_message_schedule_log(started_at)`、`da_mcp_access_token(created_at, last_used_at, revoked_at)`、`da_user(last_login_at)`、`eval_case(created_at, updated_at)`、`eval_dataset(created_at)`、`eval_run(ingested_at)` | **不修改** | 已由 Python 写入 UTC |
| **C. `TIMESTAMP` 类型（不动）** | `da_agent_session_checkpoint(updated_at)` | **不修改** | MySQL 内部存 UTC 纪元秒，会话切到 `+00:00` 后自动读出 UTC |
| **D. 容器/混合列（特殊处理）** | `da_agent_sdk_record(occurred_at)`、`da_agent_widget_event(client_ts)`、`da_model_provider(validated_at)`、`eval_dataset(updated_at)`、`eval_run(started_at)` | 见下方逐项规则 | 容器内原本即 UTC，或在同列存在混合写入 |

D 类列的处理规则（简单明确；评测两列不做启发式修正）：
1. `da_agent_widget_event.client_ts` 与 `da_model_provider.validated_at`：生产容器默认 UTC，已是 UTC，**不修改**（`UPDATE` 同表其他列时不得触碰它们）。
2. `da_agent_sdk_record.occurred_at`：由 Pi `new Date().toISOString()` 写入 UTC，**不修改**（注：本地旧库发现极早期 2026-11-03..05 的 1445 条记录 `occurred_at == created_at`，该列仅作审计信封、不参与界面展示与排序，不做猜测式改写）。
3. `eval_dataset.updated_at`、`eval_run.started_at`：**不修改**（历史值混合，无法可靠判断；记入 TODO）。更新 `eval_dataset` 其他列时显式写 `updated_at = updated_at`。

### 5.3 安全细节、幂等性与回退

- **防 `ON UPDATE CURRENT_TIMESTAMP` 误覆盖**：所有含 `updated_at ... ON UPDATE CURRENT_TIMESTAMP` 的表，在 `UPDATE` 中**必须显式赋值 `updated_at`**（要么一起减 `offset`，要么写 `updated_at = updated_at`），防止 MySQL 自动将 `updated_at` 覆写为迁移时刻的 `NOW()`。
- **大表分批更新**：`da_agent_sdk_record` 按自增主键 `id` 每批 5,000 行在 `autocommit_block()` 中分批提交（沿用 `20261113_000034` 模式），避免大事务与长锁。
- **重跑与回退**：`da_agent_sdk_record` 分批提交，若迁移中途失败，已提交的批次已减过偏移，直接重跑会重复减。因此失败后必须先恢复备份再重跑。回退同样只走备份恢复：`downgrade()` 直接报错提示“恢复升级前备份并使用对应版本应用”（沿用 `20261027_000030` 的做法），不写反向 SQL。**生产发布前必须执行 `scripts/backup-db.sh` 全量备份。**

## 6. 已接受的取舍

1. **不做用户级时区**：单配置 `DATAAGENT_TIMEZONE` 覆盖整个部署，减少前后端与定时任务的复杂度。
2. **无过渡期 / 无双字段**：API 一次性切换为带 `Z` 的 UTC，前后端同版本一起升级（Compose 同步更新 backend 与 frontend 镜像）。
3. **系统提示词只注日期不注时分秒**：保证 prompt prefix cache 命中率；需要具体时刻时沙箱 `date` 命令已带正确 `TZ`。
4. **D 类极少量历史脏数据不做复杂启发式**：`da_agent_sdk_record.occurred_at` 早期记录、非容器环境写入的 `client_ts`/`validated_at`、评测表历史时间都不做不可靠猜测，保持迁移逻辑简洁可审计。
5. **前端按浏览器本地时区显示，日期筛选按用户本地日期**：外地用户看到、筛选的都是当地时间。
6. **回退只走备份恢复**：不写反向迁移，换取迁移逻辑简单可靠。
