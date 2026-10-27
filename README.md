# DataAgent

独立智能体平台：FastAPI 后端、Vue 前端、Pi / Claude 运行时，以及通用图表和报告 Skill。

## 本地运行

```sh
python3.13 -m venv dataagent-backend/.venv-py313
dataagent-backend/.venv-py313/bin/pip install -r dataagent-backend/requirements.txt
. "$HOME/.nvm/nvm.sh"
nvm use
npm --prefix dataagent-runtime-pi ci
npm --prefix dataagent-runtime-pi run build
npm --prefix dataagent-frontend ci
```

配置 MySQL 会话 schema、Redis、SKILLS_ROOT_DIR（仓库 .claude/skills 的绝对路径）和 DATAAGENT_HOST_ROOT。在 dataagent-backend 中运行 `.venv-py313/bin/alembic upgrade head`，再运行 `.venv-py313/bin/uvicorn main:app --port 8900`；前端运行 `npm --prefix dataagent-frontend run dev`。
模型在设置中配置；MCP 服务需管理员显式添加并绑定到智能体。没有选择 MCP 时不挂载任何 MCP。

## 部署

复制 `deploy/.env.example` 为 `deploy/.env`，按需配置 `deploy/docker/dataagent/`，运行 `scripts/start.sh`（离线环境用 `scripts/create-offline-package.sh` 打包、`scripts/load-package-and-start.sh` 安装）。只部署本产品的 MySQL、Redis、后端、runner 和前端。运行配置、登录、被门户内嵌时的转发，以及从 OpenDataWorks 内置部署迁移旧数据的步骤见 [deploy/README.md](deploy/README.md)。

## 验证

后端在 dataagent-backend 下运行 `.venv-py313/bin/pytest`；仓库根运行 `.venv-py313` 对应 Python 的 `-m pytest tests`。Node 命令前先 `nvm use`，前端运行 test、build、build:widget，Pi 运行 test、build。

架构、会话 API 与 Widget 嵌入见 [docs/handbook/](docs/handbook/architecture.md)。迁移契约见 [docs/migration.md](docs/migration.md)，后续事项见 [docs/TODO.md](docs/TODO.md)。
