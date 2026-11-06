# da_agent_settings 精简计划

日期：2026-11-06。关联：[设计](../design/2026-11-06-settings-table-cleanup-design.md)。

## 任务

1. `core/skill_admin_service.py`：设置载荷、合并、运行时补丁与 `resolved_chat_settings_payload` 去掉 `mysql_*`、`session_mysql_database`；删除未使用的 `RUNTIME_SETTING_KEYS`。
2. `core/skill_admin_store.py`：读写只涉及保留的列；`raw_json` 只存 `skill_runtime`、`widget_allowed_sites`；删除 `_normalize_provider_settings_blob`。
3. `models/schemas.py`、`api/admin_routes.py`：设置响应/请求去掉废弃字段；删除 `SettingsUpdateRequest`。
4. 迁移 `alembic/versions/20261106_000033_settings_table_cleanup.py`。
5. 测试：`tests/test_skill_admin_store.py` 改为断言只存保留字段；`tests/test_skill_admin_service.py` 新增“库内旧连接不覆盖 env”的回归测试（旧代码下失败）。

## 验证

- `pytest tests/`（`test_agent_record_compat` 需要已构建的 Pi，交给 CI）。
- 本地 MySQL（3326）临时库：`alembic upgrade` 到 000032 → 写入指向 3306 的旧连接与旧 `raw_json` → `upgrade head` → 检查列与 `raw_json` → `downgrade -1` → `upgrade head`。
- 启动后端（临时库 + 本地 Redis）：启动无 1049，`/health`、`POST /topics`、`GET/PUT /nl2sql-admin/settings` 正常。

## 上线与回滚

- 后端容器启动时执行 `alembic upgrade head`，无需手工步骤。
- 回滚：`alembic downgrade 20261103_000032` 加回空列，再回退代码。
