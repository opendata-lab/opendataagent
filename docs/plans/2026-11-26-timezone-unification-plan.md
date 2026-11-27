# 全栈时区统一（计划）

- 设计：[2026-11-26-timezone-unification-design.md](../design/2026-11-26-timezone-unification-design.md)
- 状态：设计草案，待确认后实施
- 交付方式：两个互相独立的 PR，各自一步到位、无过渡期：
  - **PR 1（小，先合入）**：`DATAAGENT_TIMEZONE` 配置与校验、沙箱 `TZ`、系统提示词当前日期（Step 1 的配置部分 + Step 2 的“系统提示词、沙箱与公开配置”中前两项 + Step 5 的部署配置）。不依赖存储改造，立即修复“模型日期错一天、报告时间差 8 小时”。
  - **PR 2**：其余全部（数据库连接与 UTC 写入、统一序列化、迁移、日期边界、前端格式化）。前后端与迁移同版本发布。
- 本地冒烟只用 schema `dataagent_qa`：本机 `dataagent` schema 与正在运行的 Docker 栈共用，在其上执行新迁移会影响旧版本后端。

## 任务拆解

### Step 1：配置、数据库连接与时间工具模块

- `dataagent-backend/config.py`：新增 `dataagent_timezone: str = "Asia/Shanghai"`，并用 `ZoneInfo` 校验；更新 `dataagent_experience_digest_hour` 注释为按 `DATAAGENT_TIMEZONE` 计算。
- 新增 `dataagent-backend/core/db.py`：提供唯一 `connect_mysql(*, database=None, autocommit=False, io_timeout_seconds=None)`，固定传 `init_command="SET time_zone = '+00:00'"` 与时间转换器 `conv`（写 aware→UTC、naive 报错；读 DATETIME 附 UTC），保留现有 I/O 超时参数。
- 新增 `dataagent-backend/core/time_utils.py`：只提供 `utc_now()`、`get_business_tz()`、`business_day_bounds()`、`to_rfc3339()`。
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
  - `core/topic_task_store.py`：`_parse_client_ts` 改为 `datetime.fromisoformat`，要求带偏移（不再转后端容器本地时区）；`admin_list_topics`、`list_message_feedback` 的 `start`/`end` 为 aware 时间，条件改为 `>= start AND < end`。`api/admin_routes.py` 对应参数改用 `AwareDatetime`。
  - `core/pi_event_writer.py`：`_parse_occurred_at` 保留 aware 值，由数据库转换器换成 UTC。
  - `core/skill_admin_service.py`：`_now_iso()` 改为返回 `to_rfc3339(utc_now())`。
  - `core/runtime_registry_store.py`：`save_provider` 写入 `validated_at` 前用 `datetime.fromisoformat` 解析为 aware 时间，避免带 `Z` 字符串触发 MySQL `1292` 错误。
  - `core/eval_store.py`：删除 `dt.datetime.utcnow()`；写入时间统一用 `utc_now()`；`ingest_run` 的 `started_at` 必须带偏移，否则留空；所有读接口与 `trend_series` 输出经 `to_rfc3339`。
  - `core/accounts/store.py`、`core/remote_mcp_tokens.py`、`core/task_submission_service.py`：删除各自 UTC 当前时间函数，统一改调 `utc_now()`；与数据库值比较的 Python 代码全部改为 aware 比较。
- **统一序列化**：
  - 删除 `topic_task_store`（含 `get_task_system_prompt:1672`）、`skill_admin_store`、`agent_profile_service`、`runtime_registry_store`、`accounts/store`、`remote_mcp_tokens` 中各自的 `_to_iso` / `_json_default` / `+ "Z"` 逻辑，全部改调 `to_rfc3339`。
  - `interaction_policy.py:71`、`mcp_admin_service.py:215`、`stream_events.py:27`、`topic_files.py:44`、`topic_task_store.py:1836,1869,1940,1972` 统一输出 `Z` 结尾。
- **每日经验总结与定时任务**：
  - `core/experience.py`：删除 `db_now()`；`build_session_records` 和 `agents_with_tasks` 改用 `business_day_bounds(day)`。
  - `core/task_coordinator.py`：`_experience_loop` 改用 `datetime.now(get_business_tz())`；`_fire_schedule` 缺省时区改取 `get_settings().dataagent_timezone`。
  - 定时任务：`schemas.py` 时区改为可选；只在创建时把缺省值解析为 `get_settings().dataagent_timezone` 并据此计算、保存 `next_run_at`；更新未传时区则保留已存值。执行路径不改。
- **系统提示词、沙箱与公开配置**：
  - `core/task_executor.py`：`runtime_env` 增加 `"TZ": cfg.dataagent_timezone`。
  - `core/agent_runtime.py`：`_build_system_prompt` 在 `# 运行时上下文` 注入 `- 当前日期：{today}（时区：{tz}）`。

### Step 3：Alembic 历史数据迁移

- 新增 `dataagent-backend/alembic/versions/20261127_000045_unify_utc_timestamps.py`（`down_revision = "20261126_000044"`，接在写操作审批迁移之后）：
  - 临时 `SET SESSION time_zone = @@global.time_zone` 读取 `TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())`，随后切回 `+00:00`；不提供覆盖开关。
  - `offset == 0`：记日志并返回。
  - `offset != 0`：只更新设计 §5.2 的 A 类列（带 `ON UPDATE CURRENT_TIMESTAMP` 的表显式赋值 `updated_at`）；全部更新与版本号同一事务提交；不触碰 B、C、D 类列（含评测两列）。
  - `downgrade()` 直接抛错，提示恢复升级前备份（同 `20261027_000030`）。

### Step 4：前端统一格式化

- 新增 `dataagent-frontend/src/utils/time.js`（`parseTimestamp`、`formatDateTime`/`formatDate`/`formatShortDateTime`/`formatClockTime` 默认浏览器本地时区、可传 `timeZone`，`formatRelativeTime` 按自然日差且未来时间返回“刚刚”）。
- `FeedbackView.vue`：用 `localDayRange` 把所选本地日期换成 `[当天 00:00, 次日 00:00)` 的 ISO 时刻发送。
- 替换散落格式化与排序：`NL2SqlChatV2.vue`、`topicHelpers.js`（`Date.parse` 数值比较）、`UsersView.vue`、`FeedbackView.vue`、`KnowledgeMemoryView.vue`、`McpTokensView.vue`、`SkillDetailView.vue`、`ExperienceDrawer.vue`、`Evaluation*.vue`、`widget/WidgetChat.vue`。
- Widget 不拉取时区、不加 `data-timezone`；SDK 包不改。
- 完成后更新 `docs/TODO.md`：“Widget 历史时间显示为负数”移入“已完成”；新增“评测时间”条目（评测表历史混合时间、三个评测脚本写死 +8）。

### Step 5：部署配置与评测工具

- `deploy/docker-compose.yml`：`dataagent-backend` 增加 `DATAAGENT_TIMEZONE: ${DATAAGENT_TIMEZONE:-Asia/Shanghai}`。
- `deploy/.env.example`：补充 `DATAAGENT_TIMEZONE=Asia/Shanghai` 说明。
- 评测脚本不改（记入 TODO）。

## 测试与验证

### 1. 单元测试与契约测试

- **后端（`pytest`）**：
  - `tests/test_time_utils.py`（新增）：`to_rfc3339`（aware / 非 UTC 偏移 / naive 抛错）、`business_day_bounds`（东八区与 `America/Los_Angeles`）、`Settings` 非法 `DATAAGENT_TIMEZONE` 报错。
  - `tests/test_db_conv.py`（新增，MySQL）：aware 非 UTC 写入后读出为同一时刻；naive 写入抛 `TypeError`；读出值带 UTC 时区；会话时区为 `+00:00`。
  - 接口：时间入参不带偏移返回 422（FastAPI 参数校验错误）。
  - `tests/test_timezone_migration.py`（新增，连接本地 Docker MySQL 临时 schema）：
    - 模拟非 UTC DB（`+08:00`）：验证 A 类列全部减 8 小时、B/C/D 类列不变、`updated_at` 未被 `ON UPDATE CURRENT_TIMESTAMP` 误覆写、`downgrade` 抛错。
    - 模拟 UTC DB（`+00:00`）：验证 `upgrade` 零数据改动。
  - 既有单测回归：`tests/test_accounts.py`、`tests/test_experience.py`、`tests/test_remote_mcp.py`、`tests/test_pi_event_writer.py`、`tests/test_eval_store.py`、`tests/test_runtime_registry.py`（重点验证 `save_provider` 重复保存带 `Z` 的 `validated_at` 不报 MySQL `1292`）、`tests/test_task_executor.py`（验证 `runtime_env["TZ"]` 与系统提示词包含当前日期与时区）。
- **前端（`nvm use && npm run test`、`npm run build`、`npm run build:widget`）**：
  - 新增 `src/utils/__tests__/time.spec.js`：在 `TZ=America/Los_Angeles` 下运行 Vitest，断言默认按浏览器本地时区、传入 `timeZone: 'Asia/Shanghai'` 时按业务时区；`formatRelativeTime` 跨日不足 24 小时显示“昨天/1天前”；验证 `formatRelativeTime` 对未来时钟微偏返回 `'刚刚'` 而非 `'-1天前'`；验证 `compareTopicsByRecency` 在 `...00Z` 与 `...00.123Z` 混排时顺序正确。

### 2. 本地端到端冒烟

按 `AGENTS.md` 本地冒烟规范（MySQL `127.0.0.1:3326` `dataagent_qa`、Redis `127.0.0.1:6379`、`oda-sandbox:latest`、真实后端 + 前端 dev server）：

1. **迁移验证**：先备份 `dataagent_qa`，运行 `alembic upgrade head`，抽查迁移后 `da_user`（`created_at`/`updated_at`/`last_login_at` 同为 UTC、无 8 小时差）和 `da_agent_sdk_record`（`created_at` 与 `occurred_at` 差值从 480 分钟变为 0 分钟）。
2. **DB 会话与写入验证**：登录后创建新话题并发送 `"请执行 date 命令并告诉我今天是几月几日、当前几点"`：
   - 验证沙箱内 `date` 输出为 `CST` / `Asia/Shanghai`，与系统提示词注入的当前日期一致；
   - 查 `da_agent_topic`、`da_agent_task`、`da_agent_message`、`da_agent_sdk_record` 新写入行，确认均为 UTC 且同行 `created_at` 与 `occurred_at` 一致。
3. **API 与前端展示验证**：
   - 检查 `/topics`、`/messages`、`/users`、`/feedback`、`/mcp/tokens`、`/eval/runs` 所有时间字段均以 `Z` 结尾；
   - 在浏览器（包括使用 DevTools / Playwright 模拟 `America/Los_Angeles` 时区）打开 Chat、Widget、用户管理、用户反馈、知识与记忆、MCP 令牌页：确认时间全部展示为北京时间、刚发的会话置顶不倒跳、Widget 不出现「-1天前」、反馈按“今天”日期筛选能命中刚点踩/点赞的记录。
4. **清理**：删除冒烟创建的测试会话，停止本地服务进程。

## 上线与回退

### 上线步骤

1. 维护窗口内先停服或切换只读（避免迁移过程中产生混合时区写入），确认目标 MySQL 当前全局时区，并确认历史数据一直由该时区的实例写入（未从其他时区实例导入）；否则停止上线：
   ```sql
   SELECT @@global.time_zone, @@system_time_zone, NOW(), UTC_TIMESTAMP();
   ```
2. 执行全量逻辑备份：`scripts/backup-db.sh`。
3. 如非 `Asia/Shanghai`，在 `deploy/.env` 中设置 `DATAAGENT_TIMEZONE=<IANA 时区>`。
4. 同步升级 `dataagent-backend` 与 `dataagent-frontend` 镜像并启动（`docker-entrypoint.sh` 自动执行 `alembic upgrade head`）。
5. 核对启动日志中 `20261127_000045` 输出的检测偏移（`offset_seconds=0` 或 `28800`）与抽样时间展示。

### 回退方案

- 回退只走备份恢复：停新版本，用上线前 `scripts/backup-db.sh` 的备份恢复数据库，切回旧版 backend 与 frontend 镜像。`alembic downgrade` 会直接报错，不要使用。
- 迁移中途失败：整体回滚、版本号不变，修复原因后直接重跑即可，不会重复减偏移。
