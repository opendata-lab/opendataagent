# DataAgent 部署

`deploy/docker-compose.yml` 是一个独立的 Compose 项目（项目名固定为 `opendataagent`），
自带 MySQL、Redis、backend、sandbox runner 和前端，不依赖任何其他平台。

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
| mysql | 3326 | 会话库 `dataagent` |
| redis | 6389 | 任务协调 |

宿主机端口避开了 OpenDataWorks 默认使用的 3306/3316/6379，可以和它部署在同一台机器上。
backend 容器启动时先执行 `alembic upgrade head`，再启动服务。

## 离线部署

打包机（可访问镜像仓库）：

```sh
scripts/create-offline-package.sh --tag 1.0.0 --output oda-deployment-1.0.0.tar.xz [--platform linux/amd64]
```

包内含 `deploy/`（只有 `.env.example`，不带打包机的 `.env`）、部署脚本和去重后的单个镜像归档
`deploy/docker-images/all-images.tar`（oda-frontend/backend/runner、mysql、redis、docker CLI）。
`.env.example` 中的 `ODA_*_IMAGE` 会被改写为本地镜像名。发版 tag（`v*`）时 CI 自动生成并上传到 GitHub Release。

目标机器（需 `xz`）：

```sh
tar -xJf oda-deployment-1.0.0.tar.xz && cd oda-deployment
scripts/load-package-and-start.sh --package .   # 加载镜像；deploy/.env 不存在时从模板复制；启动
```

升级时把新包内容同步到原部署目录即可，已有的 `deploy/.env` 不会被覆盖。

## 日志

- `log-collector` 把 `dataagent-backend`、`dataagent-sandbox-runner` 的日志实时写到 `deploy/logs/<容器名>.log`；
  在 `.env` 用 `LOG_COLLECTOR_CONTAINERS` 增减目标容器。
- `scripts/dump-logs.sh` 导出全部服务日志到 `deploy/logs/services/`，并把 task child 日志汇总到 `deploy/logs/task-child/<topic>/`；
  打包带走：`tar -czf dataagent-logs.tgz -C deploy logs`。

## 运行配置

默认配置即可运行，只覆盖确有差异的字段。`.env` 支持代码原生字段的大写形式，例如
`TASK_MAX_CONCURRENCY=12`、`SCHEDULE_SCAN_INTERVAL_SECONDS=20`；更完整的运行策略写在
`docker/dataagent/dataagent_config_docker.py` 的 `DATAAGENT_SETTINGS` 中。两处不要重复覆盖同一字段。

- 助手负责能力与权限，以及可选的 `max_turns`：0 继承全局，正数覆盖每次任务的模型轮数。
- 模型负责 provider/model 和 `max_output_tokens`；Pi 未配置输出上限时使用 8192。
- 全局负责轮数默认、工具次数、超时、并发、上下文与沙箱。

| 全局默认 | 交互 | auto / 后台 |
| --- | ---: | ---: |
| `agent_*_max_turns` | 24 | 40 |
| `agent_*_max_tool_calls`（Pi） | 100 | 200 |
| `agent_*_timeout_seconds` | 360 | 1800 |
| `agent_*_bash_timeout_seconds`（Pi） | 300 | 900 |
| `agent_*_sql_read_timeout_seconds` | 300 | 900 |

`*` 为 `interactive` 或 `background`；页面发送 `auto`，走后台档。Pi 无活动超时
`dataagent_run_idle_timeout_seconds` 为 300 秒。SQL 超时不大于 Bash 超时，Bash 小于总超时。
反向代理的读写超时必须大于后台总超时（1800 秒），前端镜像内置 nginx 为 1860 秒。

## 运行时目录与沙箱

- `DATAAGENT_HOST_ROOT`（默认 `/dataagent_runtime`）是宿主机持久化目录，挂到容器内 `/dataagent_runtime`。
  每个 topic 位于 `/dataagent_runtime/<topic_id>/`，agent 工作目录是其中的 `workspace/`。
  runner 会按该路径为 task child 绑定挂载；相对路径由 `scripts/start.sh` 展开。
- `dataagent-home-init` 以 root 运行一次，预建运行时目录和 Skill 卷并修正属主为 `DATAAGENT_RUNTIME_UID/GID`（默认 `1000:1000`）。
- backend 负责 topic/task 协调；`DATAAGENT_SANDBOX_MODE=container` 时把任务交给 runner，
  runner 为每个 task 启动一次性 child 容器（`--rm`），只把当前 topic 目录挂到 child 的 `/mnt/workspace`。
- `DATAAGENT_DOCKER_SOCKET` 只挂到 runner。Podman 宿主挂载其 Docker 兼容 socket。
- Skill 存放在命名卷 `opendataagent_dataagent-skills`，首次创建时从镜像带入通用 Skill；其他 Skill 用管理界面导入。
- task child 日志由 runner 写在 `<DATAAGENT_HOST_ROOT>/<topic_id>/logs/<task_id>.log`（`--rm` 删容器不影响留存）。

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
docker compose -f deploy/docker-compose.yml up dataagent-home-init
docker compose -f deploy/docker-compose.yml run --rm --no-deps -v "$OLD_SKILLS":/from:ro --entrypoint sh dataagent-home-init -c '
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

- 第 4 步只用本部署的 `dataagent-home-init` 服务（即 oda-backend 镜像），离线环境不需要额外镜像。
- 同一台机器且 `DATAAGENT_HOST_ROOT` 不变时，topic 工作区直接沿用；换机器时用 `rsync -a` 把该目录复制到新机器的同一路径，Skill 目录同样先复制过去。
- 新部署的 `dataagent` 用户密码由本目录 `.env` 的 `DATAAGENT_MYSQL_PASSWORD` 决定。
- Redis 只保存任务协调状态，不需要迁移。
- 迁移版本 `20261027_000031` 会把旧的平台 MCP 记录和平台内置智能体转为普通可编辑记录；
  管理员随后更新 MCP 地址、`X-ODW-MCP-Token` 头和提示词中的 `odw_*` 工具名，并按上面第 4 步重新导入平台 Skill。
- 确认新部署正常后，再自行备份并删除旧 OpenDataWorks MySQL 中的 `dataagent` 库。
