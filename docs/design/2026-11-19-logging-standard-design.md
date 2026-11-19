# 日志统一标准与 DataAgent 实现设计

日期：2026-11-19
状态：待评审
关联计划：[2026-11-19-logging-standard-plan.md](../plans/2026-11-19-logging-standard-plan.md)
相关：OntoFoundry、OpenDataWorks 各自仓库中的同名设计，第 2 节逐字相同。

## 1. 现状

- 后端用 `logging.basicConfig` 输出文本到 stdout（`main.py`），Uvicorn 另有自己的默认格式；异常堆栈跨多行。
- Pi 运行时把诊断以 `[PiCell] ...` 文本写 stderr，后端逐行转成 `pi_cell.stderr task_id=... <原文>` 写进同一个服务日志，运行时、沙箱和服务日志混在一起。
- 日志只在 Docker 里：`deploy/docker-compose.yml` 没有日志限额，也没有挂载日志目录；`scripts/dump-logs.sh` 只能事后导出，容器重建后历史丢失。log-collector 已在 `3dbb572`（2026-11-04）删除。
- 没有请求关联 ID；任务日志靠消息文本里的 `task_id=%s` 识别。

## 2. 目标

运维打开 `deploy/logs/dataagent-backend/` 就能看到服务日志和运行时日志，按天归档、自动清理；与 OntoFoundry、OpenDataWorks 格式相同，能用同一个请求 ID 串起跨服务的一次操作。

## 2.1 统一标准（OntoFoundry、OpenDataWorks、DataAgent 三仓库相同）

三个仓库各自实现，下面的约定逐字相同。改动约定时三处同步修改。

#### 目录

部署目录下有一个 `logs/`，每个服务一个子目录，由 Compose 绑定挂载到容器内 `/var/log/<服务名>`：

```text
<部署目录>/logs/
  <服务名>/
    app.log                    当前日志
    app.log.2026-11-18.gz      已轮转的历史日志，按 UTC 日期命名
  dataagent-backend/
    app.log
    runtime.log                仅 DataAgent：Pi 运行时与沙箱的诊断
```

- 目录名与日志里的 `service` 字段相同，三个项目不重名：`of-backend`、`dataagent-backend`、`opendataworks-backend`。
- 应用通过环境变量 `<前缀>_LOG_DIR` 得到目录；未设置时（本地源码运行）只输出到控制台，不写文件。
- 宿主机上的日志文件属主是容器内运行服务的用户：OntoFoundry、OpenDataWorks 为 root，DataAgent 为运行时用户（默认 uid 1000，入口脚本负责建目录并授权）。

#### 轮转与保留

- 应用自己写文件、自己轮转：每天 UTC 零点切分，历史文件 gzip 压缩，保留 30 天，更早的自动删除。Java（Logback）的滚动策略要求文件名带序号，历史文件名为 `app.log.2026-11-18.0.gz`。
- 每个日志文件只能有一个进程写入。服务以单进程运行；改为多进程前必须先改为每进程一个文件。
- 同一行同时写控制台（stdout），`docker logs` 仍可用于实时查看。Docker 自身的日志用 `json-file` 驱动并限额（`max-size: 10m`、`max-file: 3`），只作临时缓冲，不作历史。
- 数据库、Redis、Nginx 等第三方服务不写 `logs/`，只用上面的 Docker 限额。Nginx 的访问记录已由后端的请求日志覆盖。

#### 格式

UTF-8，每个事件一行 JSON，不换行、不带颜色，异常堆栈作为字符串字段。

| 字段 | 说明 |
| --- | --- |
| `timestamp` | UTC，RFC 3339，毫秒，例如 `2026-11-19T08:03:12.345Z` |
| `level` | `DEBUG`、`INFO`、`WARN`、`ERROR` |
| `service` | 服务名，与目录名相同 |
| `logger` | 模块或类名 |
| `message` | 一句话说明 |
| `request_id` | HTTP 请求关联 ID，非请求上下文省略 |
| `task_id`、`run_id`、`topic_id`、`session_id` | 已有业务 ID，适用时附带，不新造 |
| `source` | 仅 DataAgent `runtime.log`：`runtime` 或 `sandbox` |
| `error_code`、`stack_trace` | 出错时附带 |

请求完成时记一条：`method`、`path`（不含查询串）、`status_code`、`duration_ms`。

#### 请求关联

HTTP 入口读取 `X-Request-ID`（1～64 位字母、数字、`-`、`_`），缺失或不合法时生成新值，并在响应头返回；调用其他服务时带上同一个值。后台任务显式带上自己的 `task_id`/`run_id`，不依赖已结束的请求上下文。

#### 级别

生产默认 `INFO`，第三方库 `WARN`，`DEBUG` 只临时开启。请求完成、任务开始与结束、超时、重试、异常各记一条；不记录逐 token 输出或完整协议帧。

#### 禁止记录

令牌、密码、API Key、Cookie、`Authorization` 头、私钥、带凭据的连接串和 URL、完整环境变量或配置对象；默认也不记录请求和响应正文、完整 SQL 及参数、用户提示词、模型回答、工具原始输出。异常消息和子进程输出写入前先脱敏并限制长度。

#### 库

- Python：标准库 `logging`，配一个约 40 行的 JSON Formatter；文件用 `TimedRotatingFileHandler(when="midnight", utc=True, backupCount=30)`，用 `namer`/`rotator` 压缩（Python 官方 Logging Cookbook 的写法）。不引入 structlog、loguru。
- Java：SLF4J + Logback（Spring Boot 自带），用 Spring Boot 的结构化日志和滚动策略配置，不加依赖。
- Node（DataAgent 运行时）：stdout 只用于协议；诊断以 JSON 行写 stderr，由 Python 后端接收后写入 `runtime.log`。

#### 不在范围内

ELK、Loki、OpenTelemetry 等日志收集与检索系统；日志收集容器（OpenDataWorks 现有的 log-collector 删除）。

## 3. DataAgent 的实现

### 3.1 后端

- 新增 `core/logging_config.py`：JSON Formatter、脱敏与截断、`request_id`/`task_id`/`topic_id` 上下文变量、按天轮转文件 handler、`configure_logging`、`log_runtime_line`。
  - 根 logger 写控制台和 `app.log`；`dataagent.runtime` logger 写控制台和 `runtime.log`，不向根传播。
  - Uvicorn 错误日志并入根 logger，访问日志关闭，由请求中间件代替；第三方库设为 `WARN`；`logging.captureWarnings(True)`。
- `config.py`：`dataagent_log_dir`（环境变量 `DATAAGENT_LOG_DIR`）、`dataagent_log_level`。
- `main.py`：用 `configure_logging` 取代 `basicConfig`；最外层中间件处理 `X-Request-ID`，记录请求完成（跳过健康检查）。OntoFoundry 调用时带来的 `X-Request-ID` 原样沿用。
- `core/task_coordinator.py`：任务运行开始时设置 `task_id`，读到任务后设置 `topic_id`。该协程在自己的 asyncio 任务里运行，它启动的子任务继承这两个值，所以任务期间每行日志自动带上，不需要改现有 54 处 `task_id=%s` 文本。
- `core/pi_runtime.py`：stderr 每行交给 `log_runtime_line`，带上 `task_id` 写入 `runtime.log`。JSON 行取其 `level`、`source`、`message`；非 JSON 行（Node 崩溃堆栈等）按 `WARN` 原样保留。内存溢出识别和 CELL_LOSS 的 stderr 末尾仍读原始行。

### 3.2 Pi 运行时

- `logDiagnostic(message, { level, source })` 改为在 stderr 写一行 `{"level","source","message"}`。stdout 仍只用于协议帧。
- 沙箱生命周期诊断（`sandbox start ...`）标 `source: "sandbox"`；模型重试 `WARN`，模型错误、运行失败 `ERROR`，MCP 连接与调用失败 `WARN`。
- 沙箱容器的主进程是 `sleep`，没有输出；工具命令输出经 `docker exec` 回到运行时，进入结果文件和事件，不进日志。沙箱容器不另设 Docker 日志参数：守护进程的默认驱动未必接受 `max-size`，设错会让沙箱创建失败。

### 3.3 部署

- `deploy/docker-compose.yml`：`x-logging` 锚点（`json-file`、`10m`×3）用于全部服务；后端设置 `DATAAGENT_LOG_DIR=/var/log/dataagent-backend`、`DATAAGENT_LOG_LEVEL`，挂载 `./logs/dataagent-backend`。
- `docker-entrypoint.sh`：设置了 `DATAAGENT_LOG_DIR` 时创建目录并交给运行时用户（后端以该用户运行）。
- `deploy/logs/` 已被 `.gitignore` 忽略，`scripts/start.sh` 已创建它。`scripts/dump-logs.sh` 保留，用于导出 Redis、前端等只在 Docker 中的日志。
- `deploy/README.md` 的「日志」一节改写。

## 4. 取舍

- **运行时日志单独成文件**：用户要求运行时与沙箱能区分开。按 `source` 字段区分 runtime 与 sandbox，按文件区分服务与运行时；不再细分到每个沙箱一个文件，沙箱按任务短暂存在，按 `task_id` 查找即可。
- **不改现有日志文本**：`task_id=%s` 这类文本保留，字段由上下文变量补充。逐条改写收益小、改动面大。
- **只记 stderr，不记工具输出**：工具输出可能包含业务数据，已经进入结果文件和 MySQL 事件，重复写进运维日志只会增加泄露面。
