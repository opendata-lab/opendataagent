# da_agent_settings 精简设计

日期：2026-11-06。范围：`dataagent-backend` 的管理设置存储、设置接口与 Alembic 迁移；前端不受影响。

## 问题

升级部署（复用旧 OpenDataWorks 部署留下的 `dataagent` 库）启动失败：
`Agent profile bootstrap failed: (1049, "Unknown database 'dataagent'")`，而 `.env` 指向的 MySQL 上该库确实存在。

原因：启动时 `bootstrap_admin_settings()` 先用 `.env` 的连接读出 `da_agent_settings`，再把其中保存的
`mysql_host/port/user/password/database`、`session_mysql_database` 合并（库内值优先）并 `update_settings()`。
旧记录指向另一个 MySQL 实例，此后所有连接都去了那里，于是报 1049。连接信息存在需要该连接才能读到的表里，本身就是自相矛盾的。

## 现状：表里各字段的去向

| 字段 | 现在的唯一来源 | 处理 |
| --- | --- | --- |
| `provider_id`、`model_name` | 设置页选择的默认供应商/模型 | 保留 |
| `skills_output_dir`、`raw_json.skill_runtime` | 设置页技能配置 | 保留 |
| `raw_json.widget_allowed_sites` | 设置页 Widget 白名单（无环境变量来源） | 保留 |
| `mysql_*`、`raw_json.session_mysql_database` | 部署配置（`.env`） | 删除 |
| `doris_*` | 无（OpenDataWorks 遗留，从未读取） | 删除 |
| `anthropic_*`、`raw_json.provider_settings` | `da_model_provider`（20260911_000025 已回填） | 删除 |
| `raw_json.validated_*` | 每次由供应商表重新计算 | 不再存 |

## 方案

- 数据库连接只来自部署配置：设置合并、`runtime_patch_from_payload` 和对外接口都不再包含 `mysql_*`、`session_mysql_database`。
- `SkillAdminStore` 只读写 `provider_id`、`model_name`、`skills_output_dir` 与 `raw_json`（仅 `skill_runtime`、`widget_allowed_sites`）。
- `GET/PUT /api/v1/nl2sql-admin/settings` 去掉 `anthropic_*`、`mysql_*`、`doris_*`、`session_mysql_database`、`settings_file_path`、`settings_local_file_path`；`/runtime-config` 不再带 `mysql_*`。前端未使用这些字段。删除未被引用的 `SettingsUpdateRequest`。
- 迁移 `20261106_000033`：清理 `raw_json` 只保留上述两个键，删除上表中的列。降级重新加回空列（数据不恢复，供应商数据在注册表中）。
- 空注册表首次启动时，仍由环境变量中的 `ANTHROPIC_*` 生成默认供应商（行为不变）。

## 取舍

- 降级不恢复旧值：旧值要么是过期副本（供应商），要么正是本次的故障来源（连接）。
- `load_settings_record` 现在会读出 `updated_at`，保存设置的响应里该字段不再恒为空。
