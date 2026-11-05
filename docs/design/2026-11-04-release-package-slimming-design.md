# DataAgent 发布包与镜像精简设计

日期：2026-11-04。
范围：Pi Runtime 打包、部署环境配置、离线包生成与 Docker Compose；不破坏 NL2SQL / Task API 协议。

## 当前状态与问题

1. **发布包体积过大（~668 MB）**：
   - `scripts/create-offline-package.sh` 将 6 个 Docker 镜像归档至 `deploy/docker-images/all-images.tar`。
   - 其中 `mysql:8.0`（官方 Oracle Linux 基础，解压后 1.34 GB，归档占约 494 MB）和 `docker:27.5.1-cli`（解压后 342 MB，归档占约 139 MB）合计占了离线包 **70% 以上** 的体积。
   - 绝大多数生产或自建部署环境已具备独立 MySQL 数据库（如云 RDS 或公司已有 MySQL 实例），无须每次版本发布都强制下载全量 MySQL 镜像。
2. **`log-collector` 容器冗余**：
   - 目前 `deploy/docker-compose.yml` 引入了一个单独的 `dataagent-log-collector` 容器，其内部仅运行一个 shell 循环执行 `docker logs --follow`，却额外引入了 340MB+ 的官方 Docker CLI 镜像。
3. **Pi 运行时携带臃肿的 `node_modules`（198 MB）**：
   - `dataagent-runtime-pi` 目前使用标准 `tsc` 编译（输出在 `dist/`），未进行依赖打包（bundle）。
   - Node 运行时必须通过动态模块查找加载 `@earendil-works/pi-ai`、`@earendil-works/pi-agent-core`、`@modelcontextprotocol/sdk` 等库，导致 `dataagent-backend` 生产镜像必须包含完整的 `node_modules` 目录，增加了 198MB 磁盘体积与上万个小文件。
4. **环境变量冗余**：
   - 部署环境存在 `DATAAGENT_SESSION_MYSQL_DATABASE`、`SESSION_MYSQL_DATABASE` 与 `MYSQL_DATABASE` 多套别名，对自备数据库的用户极具误导性。

## 方案设计

### 1. Pi Runtime 使用工业界主流的 `esbuild` 打包为单文件

- 依赖库分析：`@earendil-works/*` 与 `@modelcontextprotocol/sdk` 全为纯 JavaScript/TypeScript 实现，无 C++ 原生 addon（`.node` 文件）。
- 在 `dataagent-runtime-pi/package.json` 的 `devDependencies` 中引入行业主流打包器 `esbuild`。
- 构建命令统一为：
  ```bash
  esbuild src/main.ts --bundle --platform=node --target=node22 --format=esm --outfile=dist/main.js
  ```
- `dataagent-backend/Dockerfile` 多阶段构建调整：
  - `pi-cell` 阶段执行 `npm run build` 生成单个 `dist/main.js`；
  - 最终镜像仅 `COPY --from=pi-cell /build/dist /opt/dataagent-runtime-pi/dist`；
  - **彻底移除** `COPY --from=pi-cell /build/node_modules ...`，镜像体积缩减 ~200MB。

### 2. 用户自备数据库为第一路径，解耦 MySQL

- 统一部署环境配置为标准命名：
  ```bash
  MYSQL_HOST=127.0.0.1
  MYSQL_PORT=3306
  MYSQL_USER=dataagent
  MYSQL_PASSWORD=dataagent123
  MYSQL_DATABASE=dataagent
  ```
- `dataagent-backend/config.py` 保持现有别名读取逻辑（自动映射至 `session_mysql_database`），确保向后兼容。
- `scripts/create-offline-package.sh` 的 `EXTRA_IMAGES` 中默认移除 `mysql:8.0`（将 `redis:7.2-alpine` 作为默认协调依赖保留，或者根据用户需要自备）。
- `deploy/docker-compose.yml` 调整：将 `mysql` 移入可选服务或独立 `docker-compose.infra.yml` / compose profile，生产推荐用户自建并指向外部数据库。

### 3. 移除 `log-collector` 容器，采用 Docker 原生日志轮转

- 移除 `deploy/docker-compose.yml` 中的 `log-collector` 服务定义。
- 移除 `create-offline-package.sh` 中的 `docker:27.5.1-cli` 打包项。
- 各服务统一采用 Docker 原生 `json-file` 日志驱动配置（如 `max-size: "50m"`, `max-file: "5"`），零侵入且不占用额外内存与镜像体积。

### 4. 沙箱镜像与后端依赖精简

- `oda-sandbox` 原先在 `requirements-sandbox.txt` 中预装了大量重型科学计算库：`duckdb` (100MB+)、`matplotlib` (50MB+)、`reportlab`、`pillow`、`pypdf`、`python-docx`。
- `dataagent-backend/requirements.txt` 中也冗余声明了未被后端直接引用的 `jieba`、`numpy`、`pandas`、`openpyxl`。
- 更正（2026-11-05）：沙箱运行时**不能** `pip install`。`PIP_INDEX_URL` 只是 Dockerfile 的构建参数；执行段容器以 `--network=none` 和只读根文件系统运行，按需安装不可行。因此技能运行所需的库必须写进 `requirements-sandbox.txt` 打入镜像；本次移除的 `duckdb`、`matplotlib`、`python-docx`、`pypdf`、`reportlab`、`Pillow`、`jieba` 经核对没有任何技能、脚本或提示词引用，移除本身是安全的。新增技能依赖时同步修改该文件。
- 精简策略：
  - 后端 `requirements.txt` 仅保留服务端核心 Web 与调度依赖，剔除 `jieba`、`numpy`、`pandas`、`openpyxl`。技能脚本的测试用宿主 Python 运行内置技能，因此 CI 的 python 作业与本地开发环境同时安装 `requirements-sandbox.txt`（2026-11-05 修正：只装后端依赖导致 `test_report_generation_scripts.py` 7 项失败）。
  - 沙箱 `requirements-sandbox.txt` 仅保留内置技能 `generate_report.py` 开箱即用的 `pandas` 与 `openpyxl`，移除其余重型库。
  - `Redis:7.2-alpine` 作为异步任务协调的核心依赖持续保留。

## 边界与取舍

- **向后兼容**：保留 `dataagent-backend/config.py` 对 `SESSION_MYSQL_DATABASE` 的别名支持，旧环境升级不受影响。
- **运行性能**：`esbuild` 打包成单文件后，消除了 Node.js 启动时成百上千次 `node_modules` 磁盘 I/O 模块寻址，Pi 子进程冷启动性能反而会有所提升。
- **离线包体积**：预计由 **668 MB 缩减至 100MB ~ 150MB**，体积降幅超过 75%。

## 验证

1. 本地 Node 22 环境测试 `npm run build` 生成 `dist/main.js`，运行 `node dist/test/*.test.js`（或现有测试套件）保证行为一致。
2. Docker 构建测试：验证 `oda-backend` 镜像构建，确认镜像中无 `node_modules`，通过 `python -c "..."` 启动验证 Pi 通道。
3. 端到端 Smoke 测试：连接自备/本地 MySQL 与 Redis，提交 NL2SQL 真实任务，验证状态流转与持久化成功。
