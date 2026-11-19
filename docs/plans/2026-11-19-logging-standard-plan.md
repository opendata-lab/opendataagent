# 日志统一标准计划（DataAgent）

日期：2026-11-19
设计：[2026-11-19-logging-standard-design.md](../design/2026-11-19-logging-standard-design.md)
涉及：后端、Pi 运行时、部署。OntoFoundry、OpenDataWorks 在各自仓库按同一标准实现。

## 任务

1. `dataagent-backend/core/logging_config.py`（新增）、`config.py`、`main.py`：JSON 日志、`app.log`/`runtime.log` 按天轮转、请求 ID 中间件。
2. `core/task_coordinator.py`：任务上下文的 `task_id`、`topic_id`。
3. `core/pi_runtime.py`：stderr 经 `log_runtime_line` 写 `runtime.log`。
4. `dataagent-runtime-pi/src/protocol/channel.ts`：`logDiagnostic` 输出 JSON 行；调用处补级别与 `source`。
5. `deploy/docker-compose.yml`、`dataagent-backend/docker-entrypoint.sh`、`deploy/README.md`。

## 验证

- 后端 `tests/test_logging_config.py`：单行 JSON 与上下文 ID、脱敏、`app.log` 与 `runtime.log` 分流（含非 JSON 行）、Uvicorn 接管、请求 ID 透传与重新生成；全量 pytest。
- 运行时 `test/diagnostics.test.ts`：诊断为带级别和来源的 JSON 行；全量 `npm test`、`tsc`。
- 用真实构建的运行时启动一次，经 `log_runtime_line` 写入临时目录，确认 `runtime.log` 与 `app.log` 内容。
- `docker compose config`（以 `.env.example` 作为 `.env`）、`sh -n docker-entrypoint.sh`。
- CI 的 `runtime-http` 冒烟覆盖真实后端、运行时与 Docker 沙箱路径。

## 上线与回退

- 重建后端容器后生效，`deploy/logs/dataagent-backend/` 由入口脚本创建并授权给运行时用户。
- 回退：去掉 `DATAAGENT_LOG_DIR` 只输出控制台；运行时诊断格式变化只影响日志，不影响协议。
