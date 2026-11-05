# DataAgent 发布包与镜像精简计划

日期：2026-11-04。关联：[设计](../design/2026-11-04-release-package-slimming-design.md)。

## 任务拆解与执行步骤

1. **Pi Runtime 单文件打包配置**：
   - 在 `dataagent-runtime-pi/package.json` 添加 `devDependencies: { "esbuild": "^0.25.0" }`。
   - 更新 `scripts.build` 为 `esbuild src/main.ts --bundle --platform=node --target=node22 --format=esm --outfile=dist/main.js`。
   - 本地执行 `npm install` 与 `npm run build`，确认构建输出为单文件 `dist/main.js`。
   - 验证 Pi 单元测试或协议测试 `npm test`。

2. **后端 Dockerfile 精简**：
   - 修改 `dataagent-backend/Dockerfile`：
     - 构建阶段 `pi-cell` 执行 `npm run build` 生成 `dist/main.js`。
     - 移除 `RUN npm prune --omit=dev`。
     - 最终镜像阶段移除 `COPY --from=pi-cell /build/node_modules /opt/dataagent-runtime-pi/node_modules`。
     - 保留 `COPY --from=pi-cell /build/dist /opt/dataagent-runtime-pi/dist`。

3. **解耦 MySQL 与移除 log-collector**：
   - 修改 `deploy/docker-compose.yml`：
     - 移除 `log-collector` 容器服务；为其他服务配置标准 `logging: driver: json-file`。
     - 将 `mysql` 调整为可选（或仅在无外部 DB 时启动），容器环境变量使用标准 `MYSQL_*`。
   - 修改 `deploy/.env.example`：
     - 统一为标准变量：`MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`。
     - 移除 `DATAAGENT_SESSION_MYSQL_DATABASE`，说明自备数据库配置方式。

4. **离线打包脚本更新**：
   - 修改 `scripts/create-offline-package.sh`：
     - 从 `EXTRA_IMAGES` 中移除 `mysql:8.0` 和 `docker:27.5.1-cli`。
     - 仅保留核心业务镜像（`oda-backend`, `oda-frontend`, `oda-sandbox`）及协调组件（`redis:7.2-alpine`）。
   - 修改 `scripts/load-images.sh`：
     - 同步移除对历史 `mysql-8.0.tar`、`docker-27.5.1-cli.tar` 的强制依赖检查。

5. **依赖修剪与运行时动态 Pip**：
   - 修剪 `dataagent-backend/requirements-sandbox.txt`：仅保留 `pandas>=2.1.0` 与 `openpyxl>=3.1.0`，移除 `duckdb`、`matplotlib` 等重型依赖；
   - 修剪 `dataagent-backend/requirements.txt`：移除后端未直接引用的 `jieba`、`numpy`、`pandas`、`openpyxl`；
   - 依赖项通过沙箱运行时的 `PIP_INDEX_URL` 按需下载。
   - 保留 `redis:7.2-alpine` 作为默认协调依赖。

6. **本地验证与回归测试**：
   - 验证 `dataagent-runtime-pi` 构建与测试；
   - 验证 `dataagent-backend` 启动契约测试；
   - 本地构建 `oda-backend` 镜像，运行端到端 Smoke 检查：启动服务、提交任务、验证回复与消息持久化。

7. **上线与回滚**：
   - 提交并通过 CI；推送至 main 分支触发 GitHub Release 构建；
   - 校验远程 Release 的 `oda-deployment-latest.tar.xz` 文件大小，确认缩减至 150MB 左右；
   - 回滚方式：通过 `git revert` 恢复构建与 Compose 配置，无破坏性数据库 Schema 变更。
