# DataAgent 部署

`deploy/docker-compose.yml` 是一个独立的 Compose 项目（项目名固定为 `opendataagent`），
包含 Redis、backend 和前端，支持连接外部自备 MySQL，工具使用按需启动的 oda-sandbox 容器，不依赖任何其他平台。

```sh
cp deploy/.env.example deploy/.env      # 按需修改；缺失时 start.sh 会从模板复制
scripts/start.sh                         # 启动；另有 stop.sh、restart.sh、dump-logs.sh
```

`scripts/start.sh` 会把 `DATAAGENT_HOST_ROOT` 的相对路径按 `deploy/` 展开成宿主机绝对路径再启动 Compose；
不经脚本直接运行 `docker compose` 时，`DATAAGENT_HOST_ROOT` 必须填绝对路径。
升级镜像：`docker compose -f deploy/docker-compose.yml pull` 后再执行 `scripts/start.sh`。

| 服务 | 宿主机端口 | 说明 |
| --- | --- | --- |
| dataagent-frontend | 8901 | 管理台与问答页，`/widget/dataagent-widget.bundle.js` |
| dataagent-backend | 8900 | `/api/v1/nl2sql/*`、`/api/v1/dataagent/*`、`/api/v1/nl2sql-admin/*` |
| redis | 6389 | 任务协调 |

DataAgent 采用自备外部 MySQL 数据库架构（在 `.env` 中配置 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`，支持云 RDS 或已有 MySQL 实例）。
宿主机端口避开了 OpenDataWorks 默认使用的 3306/3316/6379，可以和它部署在同一台机器上。
backend 容器启动时先执行 `alembic upgrade head`，再启动服务。

## 离线部署

打包机（可访问镜像仓库）：

```sh
scripts/create-offline-package.sh --tag 1.0.0 --output oda-deployment-1.0.0.tar.xz [--platform linux/amd64]
```

包内含 `deploy/`（只有 `.env.example`，不带打包机的 `.env`）、部署脚本和去重后的单个镜像归档
`deploy/docker-images/all-images.tar`（oda-frontend/backend/sandbox、redis）。
`.env.example` 中的 `ODA_*_IMAGE` 会被改写为本地镜像名。发版 tag（`v*`）时 CI 自动生成并上传到 GitHub Release；main 分支每次推送刷新 `latest` Release。

目标机器（需 `xz`）：

```sh
tar -xJf oda-deployment-1.0.0.tar.xz && cd oda-deployment
scripts/load-package-and-start.sh --package .   # 加载镜像；deploy/.env 不存在时从模板复制；启动
```

升级时把新包内容同步到原部署目录即可，已有的 `deploy/.env` 不会被覆盖。

## 日志

- `scripts/dump-logs.sh` 导出全部服务日志到 `deploy/logs/services/`；
  打包带走：`tar -czf dataagent-logs.tgz -C deploy logs`。

## 运行配置

默认配置即可运行，只覆盖确有差异的字段。`.env` 支持代码原生字段的大写形式，例如
`TASK_MAX_CONCURRENCY=12`、`SCHEDULE_SCAN_INTERVAL_SECONDS=20`；更完整的运行策略写在
`docker/dataagent/dataagent_config_docker.py` 的 `DATAAGENT_SETTINGS` 中。两处不要重复覆盖同一字段。

- 助手负责能力与权限，以及可选的 `max_turns`：0 继承全局，正数覆盖每次任务的模型轮数。
- 模型负责 provider/model 和 `max_output_tokens`；Pi 未配置输出上限时使用 8192。
- 全局负责轮数默认、工具次数、超时、并发、上下文与沙箱。

所有任务使用同一档预算：

| 全局默认 | 值 |
| --- | ---: |
| `agent_max_turns` | 40 |
| `agent_max_tool_calls`（Pi） | 200 |
| `agent_timeout_seconds` | 1800 |
| `agent_bash_timeout_seconds`（Pi） | 600 |
| `agent_sql_read_timeout_seconds` | 300 |
| `agent_sql_write_timeout_seconds` | 60 |

旧的 `agent_interactive_*`／`agent_background_*` 配置名已删除：写在 `DATAAGENT_SETTINGS` 中会启动失败，写成
环境变量会被忽略，升级时改成上表名称。Pi 无活动超时 `dataagent_run_idle_timeout_seconds` 为 300 秒。
SQL/MCP 超时独立约束于剩余任务预算，Bash 小于总超时。反向代理的读写超时必须大于总超时（1800 秒），
前端镜像内置 nginx 为 1860 秒。

## 运行时目录与沙箱

- `DATAAGENT_HOST_ROOT`（默认 `/dataagent_runtime`）是宿主机持久化目录，挂到 backend 的 `/dataagent_runtime`。每个 topic 的 `workspace/` 读写挂为 `/mnt/workspace`，`results/` 只读挂为 `/mnt/results`；只有启用的技能目录只读挂到 `/opt/skills/<folder>`。
- backend 入口以 root 同步内置技能、初始化卷和 socket 附加组，再以 `DATAAGENT_RUNTIME_UID/GID`（默认 1000:1000）运行迁移和服务。沙箱使用相同 UID/GID；不修改宿主 socket 权限。
- 每个执行段的沙箱容器有资源上限：`DATAAGENT_SANDBOX_MEMORY`（默认 `2g`，同时作为 swap 上限）、`DATAAGENT_SANDBOX_CPUS`（`2`）、`DATAAGENT_SANDBOX_PIDS_LIMIT`（`512`）。
- 运行时安装：沙箱镜像把 `PYTHONUSERBASE` 指向 `/mnt/workspace/.dataagent/python`。开启网络后，技能或模型在沙箱里 `pip install` 的包写入本话题工作区，同一会话的后续执行段（含卡片等待后恢复）仍可用，不影响其他话题；系统 Python 保持只读。包随话题工作区保留和删除。常用依赖仍应放进 `requirements-sandbox.txt`。
- 沙箱网络由 `DATAAGENT_SANDBOX_NETWORK` 配置，默认 `none`（断网）。可信环境中技能需要访问内部服务（如调用业务平台接口）时设为 `bridge` 或自建网络名；开启后沙箱内由模型生成的代码能访问该网络可达的所有服务，只在可信部署中使用。
- `DATAAGENT_DOCKER_SOCKET` 只挂到 backend。Pi/MCP/模型编排在控制面；沙箱没有凭据或 socket，默认没有网络。第一次基础工具调用创建容器，同段复用，卡片、结束、取消及命令超时均销毁。不支持宿主工具执行回退。
- `ODA_SANDBOX_IMAGE` 必须预先拉取/加载，`scripts/start.sh` 不会为按需容器拉取镜像。在线升级除 Compose pull 外须执行 `docker pull <配置的 oda-sandbox 镜像>`。
- Skill 存放在命名卷 `opendataagent_dataagent-skills`，入口按内置技能内容版本同步通用技能，管理员导入的其它技能保留。新事件存于 MySQL，旧 home/logs 文件保留供回退。

## 本次架构升级与回退

先关闭入口并排空运行任务，备份 MySQL、runtime 目录和 skills 卷。使用新版镜像、现有配置和卷运行一次性命令（覆盖 entrypoint，避免启动协调器）：

```sh
docker compose -f deploy/docker-compose.yml run --rm --no-deps --entrypoint sh dataagent-backend -c \
  'alembic upgrade head && python scripts/migrate_runtime_results.py'
```

迁移把 `workspace/.dataagent/results` 的旧 JSONL 转为同名标准 JSON，保留原文件，重复执行不覆盖；原始缓存缺少的元数据明确标记。旧内存等待任务改为 suspended。随后 `scripts/start.sh`，核对 topic 创建、Pi 工具及卡片续聊。

回退同样先关闭入口、停止新版 backend，使用**新版镜像**运行：

```sh
docker compose -f deploy/docker-compose.yml run --rm --no-deps --entrypoint python dataagent-backend scripts/prepare_runtime_rollback.py
```

该脚本仅接受 revision 32，关闭等待卡片并将版本戳恢复为 31，保留 checkpoint 表和结果。恢复旧 Compose/镜像后启动；旧版使用历史文本，不恢复新版卡片或工具上下文。新 results 不自动转成旧分页缓存。再次升级保留表结构并清空旧 SDK 快照，从完整文本历史重建；避免漏掉回退期间新增消息。运行 root 的一次性命令后，正常入口会重新修正文件属主。

## 登录配置

`deploy/docker/dataagent/` 整体只读挂载进 backend（`/app/docker/dataagent`），并加入 `sys.path`。

| 文件 | 说明 |
| --- | --- |
| `dataagent_config.py` | 自带基础配置（默认 `AUTH_ENABLED=False`，不要直接改）；末尾加载同目录用户覆盖 |
| `dataagent_config_docker.py.example` | 用户覆盖示例（认证 + `DATAAGENT_SETTINGS`）；复制为 `dataagent_config_docker.py` 后填写 |
| `custom_sso_user_info.py.example` | OAuth UserInfo 归一化钩子示例；复制为 `custom_sso_user_info.py` 并在覆盖配置里挂 `OAUTH_USER_INFO` |

启用步骤：

1. `cd deploy/docker/dataagent && cp dataagent_config_docker.py.example dataagent_config_docker.py`
2. 填写 `SECRET_KEY`（`secrets.token_urlsafe(32)`）、`LOCAL_ADMINS`（bcrypt 哈希）、单项 `OAUTH_PROVIDERS`
   （Superset/FAB `remote_app` 形态）、`ADMIN_USERS`（`provider:sub`），置 `AUTH_ENABLED = True`。
   OAuth 的 `redirect_uri` 注册为 `https://<dataagent-host>/oauth-authorized/<provider_name>`，
   并从自定义模块导入 `OAUTH_USER_INFO(provider, token_response, oauth_remotes)`。
3. 重启 `dataagent-backend`。

配置文件缺失、不可读、语法错误或启用却缺合法 `SECRET_KEY` 时服务启动失败，不会静默降级为无认证。
认证关闭时匿名聊天没有已验证的用户身份，不能经 MCP 访问需要用户身份的数据。

宿主内嵌 widget 时，会话不经 DataAgent 登录：DataAgent 采信宿主通过 `X-DataAgent-User-Id`（widget 的 `userId`）上报的用户，
并以它作为执行身份（MCP 的 `X-Agent-User-Id`）。这个值来自宿主页面，只在可信宿主、可信网络中使用，并保证 8900 端口不对外暴露。

## 被其他门户内嵌

门户通过 widget 内嵌 DataAgent 时，由门户自己的反向代理把以下路径转发到本部署（可以在另一台机器上）：

| 门户路径 | 转发到 |
| --- | --- |
| `/dataagent/widget/` | `http://<DataAgent 主机>:8901/widget/` |
| `/api/v1/dataagent/` | `http://<DataAgent 主机>:8900/api/v1/dataagent/` |
| `/api/v1/nl2sql-admin/` | `http://<DataAgent 主机>:8900/api/v1/nl2sql-admin/` |
| `/api/v1/nl2sql/` | `http://<DataAgent 主机>:8900/api/v1/nl2sql/`（关闭缓冲，读写超时大于 1800 秒） |

上传类接口需要 `client_max_body_size 25m`。OpenDataWorks 的 `deploy/docker/nginx/frontend.conf` 已带注释示例。

## 从 OpenDataWorks 内置部署迁移

以前 DataAgent 跟随 OpenDataWorks 的 `deploy/docker-compose.*.yml` 部署，数据在三处：

- OpenDataWorks MySQL 里的 `dataagent` 库（会话、智能体、模型、MCP、Skill 记录）；
- Skill 目录：旧 compose 把宿主机目录 `${DATAAGENT_SKILLS_DIR:-../dataagent/.claude/skills}`（相对旧部署的 `deploy/`，
  即 `<旧 OpenDataWorks 目录>/dataagent/.claude/skills`）绑定挂载到容器，导入的 Skill 也写在这里；
- 宿主机目录 `DATAAGENT_HOST_ROOT`（默认 `/dataagent_runtime`，topic 工作区）。

升级 OpenDataWorks 不会删除这三处数据：MySQL 初始化脚本只在空卷上执行，宿主目录也不会被自动清理。
新部署使用自己的空库和 Skill 卷，需要迁移一次。在没有运行中任务时、于本仓库根目录逐条执行，任一步失败就停下处理：

```sh
# 1. 记下旧 Skill 目录的真实位置，再停掉旧的 DataAgent 容器（升级 OpenDataWorks 之前做）
OLD_SKILLS=$(docker inspect opendataworks-dataagent-backend \
  --format '{{range .Mounts}}{{if eq .Destination "/app/.claude/skills"}}{{.Source}}{{end}}{{end}}')
echo "$OLD_SKILLS" && ls "$OLD_SKILLS"            # 必须是非空目录
docker stop opendataworks-dataagent-frontend opendataworks-dataagent-backend opendataworks-dataagent-sandbox-runner

# 2. 导出旧会话库（密码取旧部署 .env 的 MYSQL_ROOT_PASSWORD，容器内已有该变量）
docker exec opendataworks-mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -uroot --single-transaction --routines --triggers --databases dataagent' > dataagent.sql

# 3. 启动新 MySQL，等首次初始化完成（日志出现 3306 端口的就绪行）后再导入
docker compose -f deploy/docker-compose.yml up -d mysql
until docker logs dataagent-mysql 2>&1 | grep -q 'ready for connections.*port: 3306'; do sleep 3; done
docker exec -i dataagent-mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot' < dataagent.sql

# 4. 初始化新 Skill 卷（首次挂载时带入镜像里的通用 Skill），再复制自己导入或放入的 Skill。
#    旧版本随仓库发布的 Skill 不复制：通用的 chart-visualization、report-generation 以镜像里的新版为准；
#    opendataworks-*、ontology-modeling-assistant 改名为 odw_* 工具后，从 OpenDataWorks 仓库 odw-skills/ 重新导入。
docker compose -f deploy/docker-compose.yml run --rm --no-deps -v "$OLD_SKILLS":/from:ro --entrypoint sh dataagent-backend -c '
  cd /from && for d in */; do
    case "${d%/}" in chart-visualization|report-generation|opendataworks-*|ontology-modeling-assistant) continue;; esac
    cp -a "$d" /app/.claude/skills/
  done
  chown -R ${DATAAGENT_RUNTIME_UID:-1000}:${DATAAGENT_RUNTIME_GID:-1000} /app/.claude/skills'

# 5. 复制登录配置等用户文件
cp <旧 OpenDataWorks 目录>/deploy/docker/dataagent/dataagent_config_docker.py deploy/docker/dataagent/
cp <旧 OpenDataWorks 目录>/deploy/docker/dataagent/custom_sso_user_info.py deploy/docker/dataagent/   # 如有

# 6. 启动全部服务；backend 启动时把导入的库升级到最新 Alembic 版本
scripts/start.sh
```

- 第 4 步只用本部署的 `dataagent-backend` 服务（覆盖入口执行复制，下一次正常启动初始化权限），离线环境不需要额外镜像。
- 同一台机器且 `DATAAGENT_HOST_ROOT` 不变时，topic 工作区直接沿用；换机器时用 `rsync -a` 把该目录复制到新机器的同一路径，Skill 目录同样先复制过去。
- 新部署的 `dataagent` 用户密码由本目录 `.env` 的 `DATAAGENT_MYSQL_PASSWORD` 决定。
- Redis 只保存任务协调状态，不需要迁移。
- 迁移版本 `20261027_000031` 会把旧的平台 MCP 记录和平台内置智能体转为普通可编辑记录；
  管理员随后更新 MCP 地址、`X-ODW-MCP-Token` 头和提示词中的 `odw_*` 工具名，并按上面第 4 步重新导入平台 Skill。
- 2026-10-28 之前的镜像在升级到 `20261027_000031` 时会把会话、任务的 `updated_at` 全部改成迁移时刻，会话列表因此都显示同一个时间。
  已经这样迁移过的库，用下面的语句按最后一条消息和任务的结束时间恢复（智能体草稿与 MCP 记录的修改时间无法恢复）：

  ```sh
  docker exec -i dataagent-mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot dataagent' <<'SQL'
  UPDATE da_agent_topic t
  LEFT JOIN (SELECT topic_id, MAX(updated_at) last_at FROM da_agent_message GROUP BY topic_id) m ON m.topic_id = t.topic_id
  SET t.updated_at = GREATEST(t.created_at, COALESCE(m.last_at, t.created_at));
  UPDATE da_agent_task
  SET updated_at = GREATEST(created_at, COALESCE(finished_at, heartbeat_at, started_at, created_at));
  SQL
  ```
- 确认新部署正常后，再自行备份并删除旧 OpenDataWorks MySQL 中的 `dataagent` 库。
