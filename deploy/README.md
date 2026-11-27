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

DataAgent 使用自备的 MySQL（云 RDS 或已有实例），在 `.env` 中配置 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`。

- `MYSQL_HOST` 填 backend 容器能访问的地址。backend 不使用 host 网络，`127.0.0.1` 指的是容器自己；
  MySQL 在宿主机上时，Linux 填宿主机 IP，macOS/Windows 可填 `host.docker.internal`。
- 库和用户需要先建好，compose 不再自带 MySQL，也不会执行初始化脚本。用 root 执行
  [`deploy/database/mysql/01-init-database.sql`](database/mysql/01-init-database.sql)（库 `dataagent`、用户 `dataagent`，密码按 `.env` 修改）：

  ```sh
  mysql -h <MySQL 主机> -P <端口> -uroot -p < deploy/database/mysql/01-init-database.sql
  ```

- 表结构不需要手工创建：backend 容器启动时先执行 `alembic upgrade head`，再启动服务。

宿主机端口避开了 OpenDataWorks 默认使用的 3306/3316/6379，可以和它部署在同一台机器上。

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

后端日志写在 `deploy/logs/dataagent-backend/`（[日志统一标准](../docs/design/2026-11-19-logging-standard-design.md)）：

- `app.log` 是服务日志，`runtime.log` 是 Pi 运行时与沙箱的诊断（字段 `source` 为 `runtime` 或 `sandbox`）。每行一条 JSON，时间为 UTC。
- 每天 UTC 零点轮转为 `*.log.YYYY-MM-DD.gz`，保留 30 天。
- 任务期间的日志带 `task_id`、`topic_id`；请求日志带 `request_id`，响应头 `X-Request-ID` 返回同值，OntoFoundry 调用时传入的值原样沿用。
- 查找：`zgrep '"task_id": "<任务ID>"' deploy/logs/dataagent-backend/*.log*`；实时：`tail -f deploy/logs/dataagent-backend/app.log`。级别由 `DATAAGENT_LOG_LEVEL` 控制，默认 `INFO`。
- Redis、前端只用 Docker 日志（每个容器最多约 30 MB）。需要时 `scripts/dump-logs.sh` 导出到 `deploy/logs/services/`；打包带走：`tar -czf dataagent-logs.tgz -C deploy logs`。

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

启动时会校验外部配置中的 `DATAAGENT_SETTINGS` 键名。升级前请从中删除
`app_name`、`claude_max_tokens`、`agent_wait_timeout_seconds`、`mysql_database`、
`max_few_shot_examples`、`max_schema_tables`、`max_business_rules`、`query_result_limit`、
`dataagent_runtime_kind`。会话库继续使用 `session_mysql_database`，环境变量 `MYSQL_DATABASE` 仍可指定会话库。

SQL/MCP 超时独立约束于剩余任务预算，Bash 小于总超时。反向代理的读写超时必须大于总超时（1800 秒）。
`deploy/docker-compose.yml` 将宿主机 `deploy/docker/nginx/default.conf` 挂载到前端容器 `/etc/nginx/conf.d/default.conf:ro`（读写超时默认 1860 秒），方便现场直接按需调整反向代理路由、流式超时、域名或证书，修改后执行 `scripts/restart.sh` 即可生效，无需重新构建前端镜像。这个文件是现场配置：仓库和离线包只带 `default.conf.example`，`scripts/start.sh` 首次启动时复制一份，之后把新包同步到部署目录也不会覆盖（与 `.env` 相同）；升级说明提到模板有变化时，对照 `.example` 手工合并。

## 运行时目录与沙箱

- `DATAAGENT_HOST_ROOT`（默认 `/dataagent_runtime`）是宿主机持久化目录，挂到 backend 的 `/dataagent_runtime`。每个 topic 的 `workspace/` 读写挂为 `/mnt/workspace`，`results/` 只读挂为 `/mnt/results`；只有启用的技能目录只读挂到 `/opt/skills/<folder>`。
- backend 入口以 root 同步内置技能、初始化卷和 socket 附加组，再以 `DATAAGENT_RUNTIME_UID/GID`（默认 1000:1000）运行迁移和服务。沙箱使用相同 UID/GID；不修改宿主 socket 权限。
- 每个执行段的沙箱容器有资源上限：`DATAAGENT_SANDBOX_MEMORY`（默认 `2g`，同时作为 swap 上限）、`DATAAGENT_SANDBOX_CPUS`（`2`）、`DATAAGENT_SANDBOX_PIDS_LIMIT`（`512`）。
- 运行时安装：沙箱镜像把 `PYTHONUSERBASE` 指向 `/mnt/workspace/.dataagent/python`。开启网络后，技能或模型在沙箱里 `pip install` 的包写入本话题工作区，同一会话的后续执行段（含卡片等待后恢复）仍可用，不影响其他话题；系统 Python 保持只读。包随话题工作区保留和删除。常用依赖仍应放进 `requirements-sandbox.txt`。
  - 包索引：镜像构建参数 `PIP_INDEX_URL` 只影响构建。运行中的沙箱用 `DATAAGENT_SANDBOX_PIP_INDEX_URL`（及 `DATAAGENT_SANDBOX_PIP_TRUSTED_HOST`）指定镜像源；不设时用 pip 默认的 PyPI，访问不到时安装失败。
  - 耗时：安装在任务执行中进行，计入 `agent_timeout_seconds`，单条命令受 `agent_bash_timeout_seconds` 限制。常用或较大的依赖放进 `requirements-sandbox.txt`，不要依赖运行时安装。
- 沙箱网络由布尔开关 `DATAAGENT_SANDBOX_NETWORK_ENABLED` 配置，默认 `false`（`--network=none`，断网）。当开启（`true`）时，通过 `DATAAGENT_SANDBOX_NETWORK_MODE` 选择网络模式：默认为 `bridge`（Docker 默认 bridge 网络），可配置为 `host`（直接共享宿主机网络栈，用于 VPN/内网直连或特定网络拓扑）。当 `DATAAGENT_SANDBOX_NETWORK_ENABLED=false` 时，无论 network_mode 为何值均强制为 `none` 断网。`DATAAGENT_SANDBOX_NETWORK_ENABLED` 接受 `true/false/1/0/yes/no`（大小写不敏感），`DATAAGENT_SANDBOX_NETWORK_MODE` 仅接受 `bridge` 或 `host`，其他值阻止启动。旧变量 `DATAAGENT_SANDBOX_NETWORK` 被忽略，启动时仅告警一次并提示新变量。可信环境中技能需要访问内部服务（如调用业务平台接口）时可开启；开启后沙箱内由模型生成的代码能访问该网络可达的所有服务，只在可信部署中使用。
  排查开关是否生效看日志，不要用 `docker exec dataagent-backend python -c ...`：新进程只读环境变量，看不到 `DATAAGENT_SETTINGS` 等启动时覆盖。启动日志 `Sandbox image=... network_enabled=... network_mode=...` 是服务实际生效的值；每次启动沙箱时日志会有 `pi_cell.stderr ... sandbox start name=oda-sandbox-... network=bridge|host|none networkEnabled=... networkMode=...`。正在运行的沙箱也可用 `docker inspect --format '{{.HostConfig.NetworkMode}}' <沙箱容器名>` 核对。
- `DATAAGENT_DOCKER_SOCKET` 只挂到 backend。Pi/MCP/模型编排在控制面；沙箱没有凭据或 socket，默认没有网络。第一次基础工具调用创建容器，同段复用，卡片、结束、取消及命令超时均销毁。不支持宿主工具执行回退。
- `ODA_SANDBOX_IMAGE` 必须预先拉取/加载，`scripts/start.sh` 不会为按需容器拉取镜像。在线升级除 Compose pull 外须执行 `docker pull <配置的 oda-sandbox 镜像>`。
- Skill 存放在命名卷 `opendataagent_dataagent-skills`，入口按内置技能内容版本同步通用技能，管理员导入的其它技能保留。新事件存于 MySQL，旧 home/logs 文件保留供回退。

## 登录配置

DataAgent 统一账号体系始终启用登录认证，`deploy/docker/dataagent/` 整体只读挂载进 backend（`/app/docker/dataagent`），并加入 `sys.path`。用户、密码（Argon2id）、外部身份绑定、角色及助手授权全部存库管理（`da_user` / `da_user_identity` / `da_role` / `da_user_role` / `da_agent_grant`）。

| 文件 | 说明 |
| --- | --- |
| `dataagent_config.py` | 自带基础配置（不要直接改）；末尾加载同目录用户覆盖 |
| `dataagent_config_docker.py.example` | 用户覆盖示例（认证 + `DATAAGENT_SETTINGS`）；复制为 `dataagent_config_docker.py` 后填写 |
| `custom_sso_user_info.py.example` | OAuth UserInfo 归一化钩子示例（仅返回身份与资料字段，不下发角色） |

全新部署：

1. `cd deploy/docker/dataagent && cp dataagent_config_docker.py.example dataagent_config_docker.py`
2. 填写 `SECRET_KEY`（`secrets.token_urlsafe(32)`）、`DEFAULT_USER_PASSWORD`（普通用户的初始密码，用于后台建号、重置密码及外部可信登录首次自动注册；管理员账号不使用），按需配置单项 `OAUTH_PROVIDERS`（`redirect_uri` 为 `https://<dataagent-host>/oauth-authorized/<provider_name>`）及 `OAUTH_USER_INFO`。
3. 运行数据库迁移 `alembic upgrade head` 后，执行 CLI 创建初始管理员账号：
   ```bash
   python -m core.accounts create-user --username admin --password '<强密码>' --admin
   ```
4. 重启 `dataagent-backend`。

平台分管理员和普通用户两级。管理员由上述命令设置，自定义角色只用于助手授权。个人资料显示用户名、显示名、本次登录方式与所属角色，并支持修改密码。OAuth 必须配置 `DEFAULT_USER_PASSWORD`。`OAUTH_USER_INFO` 只返回三个字段：`sub`（身份标识）、`preferred_username`（登录用户名，与 OpenDataWorks 一致）、`display_name`（显示名，可为中文），IdP 字段由钩子自己翻译；按 `preferred_username` 绑定同名账号或登记普通账号，显示名为空或等于用户名时用 `display_name` 补全。

从旧版本升级：

1. 先换镜像、后备份：把新版 `scripts/`、`deploy/` 同步到部署目录（离线包同步不会覆盖 `.env` 和现场 nginx 配置），`deploy/.env` 的 `ODA_*_IMAGE` 指向新版本，再 `docker compose -f deploy/docker-compose.yml pull`（离线包用 `scripts/load-images.sh`）并 `docker pull <ODA_SANDBOX_IMAGE>`。只拉取镜像，不要启动。
   旧版 backend 镜像里没有 `mysqldump`，用旧镜像备份会失败（脚本报「数据库备份不完整」并退出）。
2. 运行 `scripts/backup-db.sh`，在 `deploy/backups/` 生成数据库备份 `dataagent-<时间>.sql.gz` 和配置备份 `config-<时间>.tar.gz`（`.env` 与 `docker/dataagent/*.py`）。数据库备份在新版 backend 镜像的临时容器里用 `mysqldump` 完成，只读数据、不执行迁移，DataAgent 容器停着也能备份，宿主机不需要 MySQL 客户端。
   拉不到新镜像时，可以用 MySQL 官方镜像导出：`docker run --rm mysql:8.0 sh -c 'MYSQL_PWD=<密码> mysqldump -h <MySQL 主机> -P <端口> -u <用户> --single-transaction --no-tablespaces --routines --triggers <库名>' | gzip > deploy/backups/dataagent-<时间>.sql.gz`（`-h` 不能填 `127.0.0.1`）。
3. 在 `dataagent_config_docker.py` 中补充 `DEFAULT_USER_PASSWORD`（`LOCAL_ADMINS` / `ADMIN_USERS` 不再使用，可删除）。
4. 执行 `scripts/start.sh`。backend 启动时 Alembic 迁移 `20261123_000042` 只建表：用户、外部身份、角色、助手授权，重建个人 MCP 访问令牌表（旧令牌作废，升级后重新申请），不改动旧数据。
5. 运行 `scripts/migrate-accounts.sh`（执行 `scripts/migrate-accounts.sql`，可重复执行）：
   - 为旧会话和已发布助手名单中出现的每个旧用户（`local:<用户名>` / `<来源>:<标识>`）建普通账号和外部身份，不设密码。用户名取标识（`SSO:zhangsan` → `zhangsan`），旧会话里记录的名字（常是中文显示名）作为显示名；`local:admin` 直接对应同名的 `admin` 账号。来源和标识不区分大小写，`SSO:u123` 与 `SSO:U123` 是同一用户；
   - 旧 Widget 会话（只记了站点和宿主用户名）按现在的 Widget 登录规则处理：身份为（站点, 用户名），绑定同名账号，没有就以该用户名建账号；用户名不合规（如 OntoFoundry 旧的共享会话标识）的保持无归属并在输出中列出；
   - 按外部身份回填旧会话归属，把已发布的 `allowed_users` 转为助手授权，助手配置和草稿只保留可见模式；
   - 「仅登录用户」改为「全部用户」；
   - 末尾列出账号数、已归属的旧会话、无归属会话和授权数，以及因用户名冲突没能建账号的旧用户。
6. 设置管理员（原来的密码可以继续用，例如 `admin123`）：
   ```bash
   docker compose -f deploy/docker-compose.yml exec dataagent-backend python -m core.accounts create-user --username admin --password '<密码>' --admin
   ```
   第 5、6 步顺序不限。核对第 5 步的结果后再开放访问；在此之前普通用户看不到自己的旧会话，「指定范围」助手也暂时不可用。

该迁移不支持降级。需要回退时运行 `scripts/restore-db.sh deploy/backups/dataagent-<时间>.sql.gz`（会先清空库内全部表再导入），把 `deploy/.env` 的 `ODA_*_IMAGE` 改回旧版本，必要时从 `config-<时间>.tar.gz` 解压回旧配置，再执行 `scripts/start.sh`。

### 升级到「只保留知识」版本（迁移 `20261127_000047`）

迁移 `20261127_000047` 会删除 `da_agent_experience` 的 `kind` 与 `inject_mode` 列（原有 `memory` 行原样保留为知识），并将全部知识改为按需读取（系统提示词仅列最多 100 条目录，模型按需 `grep`/`read`）。原有「始终注入（`inject_mode='always'`）」的条目升级后不再无条件写入系统提示词，可能因超出目录上限或模型跳过读取而不生效。

**升级前**（先执行 `scripts/backup-db.sh` 备份，该迁移同样不支持 `alembic downgrade`），先查出所有生效中的始终注入条目，把其中必须无条件遵守的硬性规则迁入对应助手的**角色设定**（系统提示词）；全局硬性规则按需加入相关助手的角色设定：

```sql
SELECT experience_id, scope, agent_id, title, when_text, content
FROM da_agent_experience
WHERE status = 'active' AND inject_mode = 'always'
ORDER BY scope, agent_id, updated_at DESC;
```

其余条目无需手动处理，升级后自动转为按需读取的知识。

缺合法 `SECRET_KEY`、显式置 `AUTH_ENABLED = False`、文件不可读或语法错误时服务启动失败（fail-closed）。

宿主系统内嵌 Widget 不再使用访问密钥，也不接受匿名访客或同源复用平台登录：在后台「设置 → Widget 接入」登记 `Website ID` 与浏览器允许来源（`allowed_origins`）。当站点已登记，且请求不带 `Origin` 头（例如 OntoFoundry BFF 服务端调用）或 `Origin` 命中该站点的允许来源列表（例如 OpenDataWorks 门户浏览器直连）时，DataAgent 信任请求头 `X-DataAgent-User-Id` 中的纯用户名并对应同名平台账号（含 `admin`）；缺少用户名返回 `401 WIDGET_LOGIN_REQUIRED`，未登记站点或未放行来源返回 `403`。Widget 凭据限于运行及助手目录，平台账号、管理、评测和令牌接口需要平台登录。每个会话只属于发起它的用户，不支持多人共享会话。**风险提示**：无密钥模式下任何能访问 DataAgent API 的调用方都可为已登记站点宣称任意用户名，仅适用于可信内网部署，务必保证 8900 端口不对外暴露。

## 单助手 MCP 发布

1. 启用平台登录（本地登录或现有企业单点登录），升级数据库迁移并重启服务。
2. 入口代理保留完整 `Host`（含非默认端口）并传递 `X-Forwarded-Proto`，在同域下将 `/api/v1/remote-agents/` 和 `/api/v1/dataagent/` 转发给后端，并限制对后端 8900 端口的访问。HTTP 会明文传输令牌，仅用于内网或可信网络。公网部署使用 HTTPS。
3. 在已发布的助手详情页打开「MCP 访问」开关（立即生效）。能查看这个助手的登录用户均可点击「复制配置」。登录用户从用户菜单「访问令牌」创建令牌，趁弹窗显示时复制，并将配置中的 `headers.Authorization` 设为 `Bearer dak_…`。将配置粘贴到客户端的 MCP 设置。

MCP 会话属于令牌所有者，可在网页上看到并续聊。运行采用无人值守模式，跳过需要用户确认的操作。助手默认关闭「MCP 访问」，关闭开关会立即停用该地址。服务在每次调用时检查令牌是否有效及助手是否可见。个人令牌无有效期和数量上限，每位用户只可列出和撤销自己的令牌。MCP 客户端可带令牌下载文件。直接点击下载链接时，使用同一地址的 DataAgent 网页登录态。用目标客户端验收 Bearer 连接、令牌撤销和会话隔离。[MCP 接口与工具](../docs/handbook/api.md#单助手-mcp)

默认 `ask` 使用 Streamable HTTP 的 SSE 响应，等待上限为 15 分钟。代理须逐段转发，默认 MCP location 的读写超时为 1860 秒。外部代理和客户端的长请求上限需覆盖等待时间并预留网络余量。ZCode CLI 的 `timeoutMs` 见其[配置说明](https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/docs/usage/07-mcp.md)，桌面版本长调用以实际验收为准。需要立即返回时用 `background: true`，再以 `get_run` 读取结果。

## 被其他门户内嵌

门户通过 widget 内嵌 DataAgent 时，由门户自己的反向代理把以下路径转发到本部署（可以在另一台机器上）：

| 门户路径 | 转发到 |
| --- | --- |
| `/dataagent/widget/` | `http://<DataAgent 主机>:8901/widget/` |
| `/api/v1/dataagent/` | `http://<DataAgent 主机>:8900/api/v1/dataagent/` |
| `/api/v1/nl2sql/` | `http://<DataAgent 主机>:8900/api/v1/nl2sql/`（关闭缓冲，读写超时大于 1800 秒） |

上传类接口需要 `client_max_body_size 25m`。OpenDataWorks 的 `deploy/docker/nginx/frontend.conf.example` 已带注释示例。

## 从 OpenDataWorks 内置部署迁移

同一台机器上可以用脚本完成下面的配置和复制步骤。先停掉旧的 DataAgent 容器，再加 `--dry-run` 看一遍：

```sh
scripts/migrate-from-opendataworks.sh --old-root <旧 OpenDataWorks 目录> --mysql-host <本机 IP> [--dry-run]
```

它把会话库连接（沿用原来的 `dataagent` 库）写进 `deploy/.env`（先备份），复制登录配置，把自己导入的 Skill 复制进新 Skill 卷，
再把旧运行时目录里的 `topic_*` 复制到新部署的 `DATAAGENT_HOST_ROOT`；最后列出需要人工处理的事项（启动、MCP 地址与 Token、重新导入平台 Skill）。
脚本只读旧部署，新部署也不引用旧目录，迁移确认无误后旧目录可以单独清理。
离线发布包里也带有这个脚本。下面是脚本所做步骤的手工版本。

以前 DataAgent 跟随 OpenDataWorks 的 `deploy/docker-compose.*.yml` 部署，数据在三处：

- OpenDataWorks MySQL 里的 `dataagent` 库（会话、智能体、模型、MCP、Skill 记录）；
- Skill 目录：旧 compose 把宿主机目录 `${DATAAGENT_SKILLS_DIR:-../dataagent/.claude/skills}`（相对旧部署的 `deploy/`，
  即 `<旧 OpenDataWorks 目录>/dataagent/.claude/skills`）绑定挂载到容器，导入的 Skill 也写在这里。
  旧 `.env` 改过 `DATAAGENT_SKILLS_DIR` 的，以它为准；
- 宿主机目录 `DATAAGENT_HOST_ROOT`（默认 `/dataagent_runtime`，topic 工作区）。旧 `.env` 改过它的，以它为准。

升级 OpenDataWorks 不会删除这三处数据，宿主目录也不会被自动清理。
会话库可以直接沿用：`.env` 的 `MYSQL_*` 指向原来那台 MySQL 和 `dataagent` 库即可，不需要导出导入；
要换到另一台 MySQL 时才执行第 2 步。Skill 卷是新的，需要复制一次。
在没有运行中任务时、于本仓库根目录逐条执行，任一步失败就停下处理：

```sh
# 1. 记下旧 Skill 目录的真实位置，再停掉旧的 DataAgent 容器（升级 OpenDataWorks 之前做）
OLD_SKILLS=$(docker inspect opendataworks-dataagent-backend \
  --format '{{range .Mounts}}{{if eq .Destination "/app/.claude/skills"}}{{.Source}}{{end}}{{end}}')
echo "$OLD_SKILLS" && ls "$OLD_SKILLS"            # 必须是非空目录
docker stop opendataworks-dataagent-frontend opendataworks-dataagent-backend opendataworks-dataagent-sandbox-runner

# 2. （仅换 MySQL 时）导出旧会话库，导入新 MySQL；新库的用户按上文“数据库”一节创建
docker exec opendataworks-mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -uroot --single-transaction --routines --triggers --databases dataagent' > dataagent.sql
mysql -h <新 MySQL 主机> -P <端口> -uroot -p < dataagent.sql

# 3. 在 deploy/.env 配置 MYSQL_*，再把旧运行时目录里的 topic 工作区复制到新部署的 DATAAGENT_HOST_ROOT
#    （新部署不要直接指向旧部署目录里的路径，否则清理旧目录会连带删掉数据）
NEW_ROOT=/dataagent_runtime                      # 与 deploy/.env 的 DATAAGENT_HOST_ROOT 一致
OLD_ROOT=<旧部署的 DATAAGENT_HOST_ROOT 绝对路径>
mkdir -p "$NEW_ROOT" && for t in "$OLD_ROOT"/topic_*; do [ -e "$NEW_ROOT/$(basename "$t")" ] || cp -a "$t" "$NEW_ROOT/"; done

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

# 6. 启动全部服务；backend 启动时把沿用或导入的库升级到最新 Alembic 版本
scripts/start.sh
```

- 第 4 步只用本部署的 `dataagent-backend` 服务（覆盖入口执行复制，下一次正常启动初始化权限），离线环境不需要额外镜像。
- 旧部署用的就是默认的 `/dataagent_runtime`、新部署也用它时，第 3 步的复制可以跳过；换机器时用 `rsync -a` 把 `topic_*` 复制到新机器的 `DATAAGENT_HOST_ROOT`，Skill 目录同样先复制过去。
- backend 连接 MySQL 只用 `.env` 的 `MYSQL_*`。
- Redis 只保存任务协调状态，不需要迁移。
- 迁移版本 `20261027_000031` 会把旧的平台 MCP 记录和平台内置智能体转为普通可编辑记录，管理员随后在 MCP 管理页修改：
  - 地址改为 OpenDataWorks 后端的 `http://<OpenDataWorks 主机>:8080/api/mcp/`（宿主机 IP，不能填 `localhost`；门户 Nginx 不代理此入口）；
  - 删除旧的 `X-ODW-MCP-Token` 头，改为 `Authorization: Bearer <服务账号 Token>`，Token 在 OpenDataWorks「管理中心 → 账号管理」生成，问数只需只读角色。OpenDataWorks 只按这个 Token 授权，使用该智能体的所有人看到同一范围；需要不同范围时，为每个范围建一个服务账号，在这里各配置一条 MCP 记录，并让对应智能体选择它；
  - 保存后刷新工具列表；提示词中的工具名使用 `odw_*`。
  
  然后按上面第 4 步的说明重新导入平台 Skill。
- 2026-10-28 之前的镜像在升级到 `20261027_000031` 时会把会话、任务的 `updated_at` 全部改成迁移时刻，会话列表因此都显示同一个时间。
  已经这样迁移过的库，用下面的语句按最后一条消息和任务的结束时间恢复（智能体草稿与 MCP 记录的修改时间无法恢复）：

  ```sh
  mysql -h <MySQL 主机> -P <端口> -uroot -p dataagent <<'SQL'
  UPDATE da_agent_topic t
  LEFT JOIN (SELECT topic_id, MAX(updated_at) last_at FROM da_agent_message GROUP BY topic_id) m ON m.topic_id = t.topic_id
  SET t.updated_at = GREATEST(t.created_at, COALESCE(m.last_at, t.created_at));
  UPDATE da_agent_task
  SET updated_at = GREATEST(created_at, COALESCE(finished_at, heartbeat_at, started_at, created_at));
  SQL
  ```
- 确认新部署正常后，再自行备份并删除旧 OpenDataWorks MySQL 中的 `dataagent` 库。
