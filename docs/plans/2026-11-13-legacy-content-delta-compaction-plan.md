# 历史 content.delta 行压缩实施计划

日期：2026-11-13
状态：待评审
关联设计：[2026-11-13-legacy-content-delta-compaction-design.md](../design/2026-11-13-legacy-content-delta-compaction-design.md)

涉及栈：`dataagent-backend`（Alembic 数据迁移）。前端、API、表结构不改。

## 1. 任务

1. 新增迁移 `dataagent-backend/alembic/versions/20261113_000034_compact_legacy_content_deltas.py`（`down_revision = 20261106_000033`）：
   - 纯函数 `compact_task_rows(rows) -> (updates, delete_ids)`：输入一个任务的 `content.delta` / `content.completed` 行（按 id 升序），输出要改写的行（id → 新 data）与要删除的 id；
   - `upgrade()`：查出终态任务中含 `content.delta` 的 task_id；逐任务读取、计算、改写、分批删除，各自提交；记录日志（任务数、改写行数、删除行数）；
   - `downgrade()`：空操作。
2. 测试 `dataagent-backend/tests/test_compact_legacy_content_deltas.py`：
   - 已完成块的增量全部删除；
   - 被打断块保留首行并改写为全文 `text`，其余删除；已有 `text` 的行按覆盖累积；
   - `content_id` 为空的行不动；不同 turn / kind 的同名 `content_id` 各自成块；
   - 压缩前后经 `_project_sdk_records` 投影的块文字一致；
   - 对已压缩数据再运行，无改写、无删除。
3. `docs/TODO.md`：会话分页条目补充本次结论（大会话主因是存量增量行，已由本迁移处理）。

## 2. 验证

1. `pytest dataagent-backend/tests`。
2. 本地库（`127.0.0.1:3326/dataagent`）：
   - 迁移前备份 `da_agent_sdk_record`（mysqldump 到临时目录）；
   - 对受影响任务记录压缩前的投影结果；
   - `alembic upgrade head`；
   - 对比压缩后的投影结果一致；统计增量行数与 `topic_47941a2a…` 消息接口大小；
   - 再执行一次迁移逻辑，确认无变更。

## 3. 上线与回退

- 随后端发布执行。迁移只处理终态任务，与运行中的任务无冲突。
- 回退：迁移不可逆（downgrade 为空操作）；内容等价，不需要恢复。如需原始行，用发布前的数据库备份。
