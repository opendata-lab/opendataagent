# 全栈时区统一（计划）

- 设计：[2026-11-26-timezone-unification-design.md](../design/2026-11-26-timezone-unification-design.md)
- 状态：设计草案，待确认后实施
- 交付方式：两个互相独立的 PR，各自一步到位、无过渡期：
  - **PR 1（小，先合入）**：`DATAAGENT_TIMEZONE` 配置与校验、沙箱 `TZ`、系统提示词当前日期（Step 1 的配置部分 + Step 2 的“系统提示词、沙箱与公开配置”中前两项 + Step 5 的部署配置）。不依赖存储改造，立即修复“模型日期错一天、报告时间差 8 小时”。
  - **PR 2**：其余全部（数据库连接与 UTC 写入、统一序列化、迁移、日期边界、前端格式化、`/auth/config` 时区字段、评测脚本）。前后端与迁移同版本发布。
- 本地冒烟只用 schema `dataagent_qa`：本机 `dataagent` schema 与正在运行的 Docker 栈共用，在其上执行新迁移会影响旧版本后端。

## 任务拆解

### Step 1：配置、数据库连接与时间工具模块

- `dataagent-backend/config.py`：新增 `dataagent_timezone: str = "Asia/Shanghai"`，并用 `ZoneInfo` 校验；更新 `dataagent_experience_digest_hour` 注释为按 `DATAAGENT_TIMEZONE` 计算。
- `dataagent-backend/requirements.txt`：显式增加 `tzdata`。
- 新增 `dataagent-backend/core/db.py`：提供唯一 `connect_mysql(*, database=None, autocommit=False)`，固定传 `init_command="SET time_zone = '+00:00'"`。
- 新增 `dataagent-backend/core/time_utils.py`：提供 `get_business_tz()`、`utc_now()`、`utc_now_naive()`、`format_utc_iso()`、`parse_iso_to_utc_naive()`、`parse_query_boundary_to_utc()`、`business_day_utc_bounds()`。
- 7 处 store 统一改为调用 `connect_mysql`：
  - `dataagent-backend/core/topic_task_store.py`
  - `dataagent-backend/core/agent_profile_service.py`
  - `dataagent-backend/core/skill_admin_store.py`
  - `dataagent-backend/core/runtime_registry_store.py`
  - `dataagent-backend/core/accounts/store.py`
  - `dataagent-backend/core/remote_mcp_tokens.py`
  - `dataagent-backend/core/eval_store.py`
- `dataagent-backend/alembic/env.py`：`create_engine` 传入 `connect_args={"init_command": "SET time_zone = '+00:00'"}`。

### Step 2：后端写入修正、API 统一序列化与业务边界

- **写入修正**：
  - `core/topic_task_store.py`：`_parse_client_ts` 改为调用 `parse_iso_to_utc_naive`（不再转后端容器本地时区）；`admin_list_topics`、`list_message_feedback` 的 `start`/`end` 筛选经 `parse_query_boundary_to_utc` 转 naive UTC（纯日期 `end` 用 `< 次日 00:00:00 UTC`）。
  - `core/pi_event_writer.py`：`_parse_occurred_at` 改为调用 `parse_iso_to_utc_naive`（确保非 UTC 偏移不会在写入 MySQL 时被静默丢弃）。
  - `core/skill_admin_service.py`：`_now_iso()` 改为返回 `format_utc_iso(utc_now())`。
  - `core/runtime_registry_store.py`：`save_provider` 写入 `validated_at` 前先经 `parse_iso_to_utc_naive` 转 naive UTC `datetime`，避免带 `Z` 字符串触发 MySQL `1292` 错误。
  - `core/eval_store.py`：删除 `dt.datetime.utcnow()`；写入时间统一用 `utc_now_naive()`，`ingest_run` 的 `started_at` 经 `parse_iso_to_utc_naive` 归一化（回退 `run_label` 时按 `get_business_tz()` 转 UTC）；所有读接口与 `trend_series` 输出经 `format_utc_iso` 序列化。
  - `core/accounts/store.py`、`core/remote_mcp_tokens.py`、`core/task_submission_service.py`：删除各自 UTC 当前时间函数，统一改调 `utc_now_naive()`。
- **统一序列化**：
  - 删除 `topic_task_store`（含 `get_task_system_prompt:1672`）、`skill_admin_store`、`agent_profile_service`、`runtime_registry_store`、`accounts/store`、`remote_mcp_tokens` 中各自的 `_to_iso` / `_json_default` / `+ "Z"` 逻辑，全部改调 `format_utc_iso`。
  - `interaction_policy.py:71`、`mcp_admin_service.py:215`、`stream_events.py:27`、`topic_files.py:44`、`topic_task_store.py:1836,1869,1940,1972` 统一输出 `Z` 结尾。
- **每日经验总结与定时任务**：
  - `core/experience.py`：删除 `db_now()`；`build_session_records` 和 `agents_with_tasks` 改用 `business_day_utc_bounds(day)`。
  - `core/task_coordinator.py`：`_experience_loop` 改用 `datetime.now(get_business_tz())`；`_fire_schedule` 缺省时区改取 `get_settings().dataagent_timezone`。
  - `api/routes.py`、`models/schemas.py`、`core/task_submission_service.py`、`core/topic_task_store.py`：定时任务默认时区从硬编码 `"Asia/Shanghai"` 改为读取 `get_settings().dataagent_timezone`。
- **系统提示词、沙箱与公开配置**：
  - `core/task_executor.py`：`runtime_env` 增加 `"TZ": cfg.dataagent_timezone`。
  - `core/agent_runtime.py`：`_build_system_prompt` 在 `# 运行时上下文` 注入 `- 当前日期：{today}（时区：{tz}）`。
  - `api/auth_routes.py`：`GET /api/v1/nl2sql/auth/config` 返回新增 `"timezone": get_settings().dataagent_timezone`。

### Step 3：Alembic 历史数据迁移

- 新增 `dataagent-backend/alembic/versions/20261126_000043_unify_utc_timestamps.py`（`down_revision = "20261123_000042"`）：
  - 通过临时 `SET SESSION time_zone = @@global.time_zone` 读取 `TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())`（支持 `DATAAGENT_LEGACY_DB_TZ_OFFSET_SECONDS` 覆盖），随后切回 `+00:00`。
  - 若 `offset == 0`（生产 DB 已是 UTC）：记日志并直接返回。
  - 若 `offset != 0`：
    - 仅更新设计 §5.2 中 A 类列（每张带 `ON UPDATE CURRENT_TIMESTAMP` 的表均显式赋值 `updated_at`）；
    - `da_agent_sdk_record` 按主键 `id` 每批 5,000 行在 `autocommit_block()` 中分批更新；
    - 不触碰 B 类（已 UTC）、C 类（`da_agent_session_checkpoint.updated_at` `TIMESTAMP`）及 `client_ts` / `validated_at` / `occurred_at`；
    - 按 §5.2 规则条件修正 `eval_dataset.updated_at` 与 `eval_run.started_at`。
  - `downgrade()` 按相同检测逻辑在 `offset != 0` 时对 A 类列执行 `DATE_ADD(..., INTERVAL %s SECOND)`（并显式赋值 `updated_at`）。

### Step 4：前端与 SDK 统一格式化

- 新增 `dataagent-frontend/src/utils/time.js`（基于 `Intl.DateTimeFormat`，支持 `setDisplayTimezone` / `getDisplayTimezone`、`formatDateTime`、`formatDate`、`formatShortDateTime`、`formatClockTime`、`formatRelativeTime`）。
- `dataagent-frontend/src/stores/auth.js`：`bootstrap()` 读取 `config.timezone` 并调用 `setDisplayTimezone(...)`。
- `dataagent-frontend/src/widget/WidgetChat.vue` & `src/widget/config.js`：支持 `data-timezone`，并在挂载时非阻塞调用 `api.authApi.getAuthConfig()` 同步业务时区；用 `formatRelativeTime` / `formatClockTime` 替换本地格式化（彻底修复「-1天前」）。
- 替换主应用所有散落格式化与排序：
  - `src/views/intelligence/NL2SqlChatV2.vue`、`src/views/intelligence/topicHelpers.js`（`compareTopicsByRecency` 改用 `Date.parse` 数值比较）
  - `src/views/settings/UsersView.vue`、`FeedbackView.vue`（同时改为直接传 `YYYY-MM-DD` 筛选参数）、`KnowledgeMemoryView.vue`、`McpTokensView.vue`、`SkillDetailView.vue`
  - `src/components/experience/ExperienceDrawer.vue`
  - `src/views/evaluation/EvaluationSetsView.vue`、`EvaluationSetDetailView.vue`、`EvaluationResultsView.vue`、`EvaluationRunDetailView.vue`
- 独立 SDK `dataagent-frontend/packages/agent-conversation`：
  - 新增 `src/utils/time.js`（零依赖 `Intl.DateTimeFormat`）；
  - `src/ui/MessageActions.vue` 改用 `formatClockTime(createdAt, timezone)`，主应用传入当前业务时区。
- 完成后更新 `docs/TODO.md`，将“Widget 历史时间显示为负数”移入“已完成”。

### Step 5：部署配置与评测工具

- `deploy/docker-compose.yml`：`dataagent-backend` 增加 `DATAAGENT_TIMEZONE: ${DATAAGENT_TIMEZONE:-Asia/Shanghai}`。
- `deploy/.env.example`：补充 `DATAAGENT_TIMEZONE=Asia/Shanghai` 说明。
- `tools/dataagent-evals/{builtin,deepeval,opik}/run.py`：相对日期校验改读 `expected_time.timezone` → `DATAAGENT_TIMEZONE` → `Asia/Shanghai`；`deepeval` 与 `opik` 上报摘要同步携带 UTC `started_at`。

## 测试与验证

### 1. 单元测试与契约测试

- **后端（`pytest`）**：
  - `tests/test_time_utils.py`（新增）：`format_utc_iso`（naive / aware / 非 UTC / 幂等）、`parse_iso_to_utc_naive`、`parse_query_boundary_to_utc`（纯日期、naive 时间、带偏移、跨午夜边界）、`business_day_utc_bounds`（东八区与负偏移如 `America/Los_Angeles`）、`Settings` 非法 `DATAAGENT_TIMEZONE` 报错。
  - `tests/test_timezone_migration.py`（新增，连接本地 Docker MySQL 临时 schema）：
    - 模拟非 UTC DB（`+08:00`）：验证 A 类列全部减 8 小时、B 类与 `TIMESTAMP` 列不变、`updated_at` 未被 `ON UPDATE CURRENT_TIMESTAMP` 误覆写、`eval_dataset.updated_at` 仅改写被触发过自动更新的行、重复 `upgrade` 幂等、`downgrade` 精准还原。
    - 模拟 UTC DB（`+00:00`）：验证 `upgrade` 与 `downgrade` 均零数据改动。
  - 既有单测回归：`tests/test_accounts.py`、`tests/test_experience.py`、`tests/test_remote_mcp.py`、`tests/test_pi_event_writer.py`、`tests/test_eval_store.py`、`tests/test_runtime_registry.py`（重点验证 `save_provider` 重复保存带 `Z` 的 `validated_at` 不报 MySQL `1292`）、`tests/test_task_executor.py`（验证 `runtime_env["TZ"]` 与系统提示词包含当前日期与时区）。
- **前端（`nvm use && npm run test`、`npm run test:sdk`、`npm run build`、`npm run build:widget`）**：
  - 新增 `src/utils/__tests__/time.spec.js` 与 `packages/agent-conversation/src/__tests__/time.spec.js`：在非 +8 环境（如 `TZ=America/Los_Angeles` 或 `TZ=UTC` 下运行 Vitest）断言同一 UTC `Z` 时间始终按 `Asia/Shanghai` 展示；验证 `formatRelativeTime` 对未来时钟微偏返回 `'刚刚'` 而非 `'-1天前'`；验证 `compareTopicsByRecency` 在 `...00Z` 与 `...00.123Z` 混排时顺序正确。

### 2. 本地端到端冒烟

按 `AGENTS.md` 本地冒烟规范（MySQL `127.0.0.1:3326` `dataagent_qa`、Redis `127.0.0.1:6379`、`oda-sandbox:latest`、真实后端 + 前端 dev server）：

1. **迁移验证**：先备份 `dataagent_qa`，运行 `alembic upgrade head`，抽查迁移后 `da_user`（`created_at`/`updated_at`/`last_login_at` 同为 UTC、无 8 小时差）和 `da_agent_sdk_record`（`created_at` 与 `occurred_at` 差值从 480 分钟变为 0 分钟）。
2. **DB 会话与写入验证**：登录后创建新话题并发送 `"请执行 date 命令并告诉我今天是几月几日、当前几点"`：
   - 验证沙箱内 `date` 输出为 `CST` / `Asia/Shanghai`，与系统提示词注入的当前日期一致；
   - 查 `da_agent_topic`、`da_agent_task`、`da_agent_message`、`da_agent_sdk_record` 新写入行，确认均为 UTC 且同行 `created_at` 与 `occurred_at` 一致。
3. **API 与前端展示验证**：
   - 检查 `/api/v1/nl2sql/auth/config` 返回 `"timezone": "Asia/Shanghai"`；
   - 检查 `/topics`、`/messages`、`/users`、`/feedback`、`/mcp/tokens`、`/eval/runs` 所有时间字段均以 `Z` 结尾；
   - 在浏览器（包括使用 DevTools / Playwright 模拟 `America/Los_Angeles` 时区）打开 Chat、Widget、用户管理、用户反馈、知识与记忆、MCP 令牌页：确认时间全部展示为北京时间、刚发的会话置顶不倒跳、Widget 不出现「-1天前」、反馈按“今天”日期筛选能命中刚点踩/点赞的记录。
4. **清理**：删除冒烟创建的测试会话，停止本地服务进程。

## 上线与回退

### 上线步骤

1. 维护窗口内先停服或切换只读（避免迁移过程中产生混合时区写入），确认目标 MySQL 当前全局时区：
   ```sql
   SELECT @@global.time_zone, @@system_time_zone, NOW(), UTC_TIMESTAMP();
   ```
2. 执行全量逻辑备份：`scripts/backup-db.sh`。
3. 如非 `Asia/Shanghai`，在 `deploy/.env` 中设置 `DATAAGENT_TIMEZONE=<IANA 时区>`。
4. 同步升级 `dataagent-backend` 与 `dataagent-frontend` 镜像并启动（`docker-entrypoint.sh` 自动执行 `alembic upgrade head`）。
5. 核对启动日志中 `20261126_000043` 输出的检测偏移（`offset_seconds=0` 或 `28800`）与抽样时间展示。

### 回退方案

- **未产生新写入前发现问题**：停止新镜像，对旧镜像前先执行 `alembic downgrade 20261123_000042`（自动按原 DB 偏移逆向加回；若原 DB 本身是 UTC 则为空操作），再恢复旧版 backend 与 frontend 镜像。
- **已对外产生新写入后需要回退**：**不要直接执行 `alembic downgrade`**（会把新写入的 UTC 行错误加偏）；直接使用上线前 `scripts/backup-db.sh` 备份恢复数据库并切回旧版镜像。
