# OpenDataAgent

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="dataagent-frontend/public/opendataworks-icon-dark.svg">
    <img src="dataagent-frontend/public/opendataworks-icon-light.svg" alt="OpenDataAgent 图标" width="160">
  </picture>
</p>

<div align="center">

<p align="center">
  <a href="https://github.com/opendata-lab/opendataagent/actions/workflows/ci.yml"><img src="https://github.com/opendata-lab/opendataagent/actions/workflows/ci.yml/badge.svg" alt="CI"/></a>
  <a href="https://github.com/opendata-lab/opendataagent/stargazers"><img src="https://img.shields.io/github/stars/opendata-lab/opendataagent" alt="Stars Badge"/></a>
  <a href="https://github.com/opendata-lab/opendataagent/network/members"><img src="https://img.shields.io/github/forks/opendata-lab/opendataagent" alt="Forks Badge"/></a>
  <a href="https://github.com/opendata-lab/opendataagent/issues"><img src="https://img.shields.io/github/issues/opendata-lab/opendataagent" alt="Issues Badge"/></a>
  <a href="https://github.com/opendata-lab/opendataagent/blob/main/LICENSE"><img src="https://img.shields.io/github/license/opendata-lab/opendataagent" alt="License Badge"/></a>
  <a href="https://github.com/opendata-lab/opendataagent/releases"><img src="https://img.shields.io/github/downloads/opendata-lab/opendataagent/total" alt="Downloads"></a>
  <a href="https://www.npmjs.com/package/@opendataagent/agent-conversation"><img src="https://img.shields.io/npm/v/@opendataagent/agent-conversation" alt="npm"></a>
  <a href="https://deepwiki.com/opendata-lab/opendataagent"><img src="https://deepwiki.com/badge.svg" alt="Ask DeepWiki"></a>
</p>

**可独立部署、可嵌入任意系统的数据智能体平台。**

简体中文 | [English](README_en.md)

[快速开始](#快速开始) · [架构](docs/handbook/architecture.md) · [会话 API](docs/handbook/api.md) · [Widget 嵌入](docs/handbook/widget.md) · [部署指南](deploy/README.md) · [贡献指南](CONTRIBUTING.md)

</div>

---

## 项目简介

OpenDataAgent（DataAgent）是一个独立的智能体平台：管理员配置模型、MCP 和 Skill，组装成智能体；用户在问答页或宿主系统内嵌的 widget 里用自然语言提问，智能体在隔离的沙箱中调用工具完成分析，并以图表、表格和报告呈现结果。

它不内置任何数据平台。访问外部系统只通过智能体显式绑定的 MCP，每次调用都带上执行身份（用户与智能体），由 MCP 一侧决定能看哪些数据。[OpenDataWorks](https://github.com/opendata-lab/opendataworks) 就是这样接入的一个宿主。

## 核心能力

- **智能体编排**：在管理台组合模型、系统提示、MCP、Skill 与运行预算，草稿与发布分离。
- **双运行时**：默认 Pi（`pi_agent_core`），也可切换到 Claude Agent SDK；事件统一落库、通过 SSE 实时推送。
- **沙箱隔离**：容器模式下每个任务一个一次性 child 容器，只挂当前会话的工作区。
- **MCP 与身份**：管理员显式添加的 HTTP MCP，后端为每次调用附加 `X-Agent-User-Id` / `X-Agent-Id`。
- **Skill 管理**：内置图表与报告 Skill，支持导入、版本记录与在线编辑。
- **可嵌入**：一行脚本嵌入 widget，或用 npm 包 [`@opendataagent/agent-conversation`](dataagent-frontend/packages/agent-conversation/README.md) 自建对话界面。
- **交互卡片**：工具调用审批、向用户提问、计划确认，任务挂起后由用户决定继续。
- **评测**：内置、DeepEval、Opik 三种评测器，在管理台查看评测集与运行结果。

## 快速开始

需要 Docker（或 Podman）与 Compose。

```bash
git clone https://github.com/opendata-lab/opendataagent.git
cd opendataagent
cp deploy/.env.example deploy/.env   # 至少修改数据库密码
scripts/start.sh
```

| 服务 | 地址 |
| --- | --- |
| 管理台与问答页 | http://localhost:8901 |
| 后端 API | http://localhost:8900/api/v1/nl2sql |
| Widget 脚本 | http://localhost:8901/widget/dataagent-widget.bundle.js |

启动后在「设置」中配置模型，按需添加 MCP 并绑定到智能体。

### 离线部署

每个 [Release](https://github.com/opendata-lab/opendataagent/releases) 附带离线部署包 `oda-deployment-<版本>.tar.xz`（`latest` 跟随 main 分支）：

```bash
tar -xJf oda-deployment-<版本>.tar.xz && cd oda-deployment
scripts/load-package-and-start.sh --package .
```

自行打包、运行配置、登录、被宿主内嵌时的反向代理，以及从 OpenDataWorks 内置部署迁移旧数据，见 [部署指南](deploy/README.md)。

## 嵌入到你的系统

```html
<script src="http://<DataAgent 主机>:8901/widget/dataagent-widget.bundle.js"
        data-website-id="<已登记的站点 ID>"
        data-agent-id="<智能体 ID>"
        data-api-base-url="http://<DataAgent 主机>:8900"></script>
```

站点需先在管理台「Widget 接入」中登记并配置允许的来源。完整参数、程序化安装与宿主上报用户身份见 [Widget 嵌入](docs/handbook/widget.md)。

## 架构

```
浏览器（管理台 / 问答页 / 宿主页面中的 widget）
    │  HTTP + SSE
    ▼
dataagent-backend（FastAPI）── MySQL / Redis
    │  任务调度 · 运行时 · Skill · MCP
    ▼
dataagent-sandbox-runner ── 每个任务一个 child 容器
    │
    ▼  HTTP MCP（带执行身份头）
外部系统（如 OpenDataWorks）
```

详见 [架构文档](docs/handbook/architecture.md)。

## 模块结构

| 目录 | 说明 |
| --- | --- |
| `dataagent-backend/` | FastAPI 后端、任务协调、运行时适配、Alembic 迁移 |
| `dataagent-frontend/` | Vue 3 管理台与问答页、widget、`@opendataagent/agent-conversation` SDK |
| `dataagent-runtime-pi/` | Pi 运行时（TypeScript） |
| `contracts/` | 前后端共享的 JSON Schema 契约 |
| `.claude/skills/` | 内置通用 Skill（图表、报告） |
| `tools/dataagent-evals/` | 评测器（builtin / DeepEval / Opik） |
| `deploy/` | Docker Compose 与环境模板 |
| `scripts/` | 启停、日志导出、离线包、评测脚本 |
| `docs/` | 架构、API、Widget 文档与待办 |

## 本地开发

```bash
# 后端
python3.13 -m venv dataagent-backend/.venv-py313
dataagent-backend/.venv-py313/bin/pip install -r dataagent-backend/requirements.txt

# Node（版本见 .nvmrc）
nvm use
npm --prefix dataagent-runtime-pi ci && npm --prefix dataagent-runtime-pi run build
npm --prefix dataagent-frontend ci
```

准备 MySQL 会话库与 Redis，设置 `SKILLS_ROOT_DIR`（仓库 `.claude/skills` 的绝对路径）和 `DATAAGENT_HOST_ROOT`，然后：

```bash
cd dataagent-backend
.venv-py313/bin/alembic upgrade head
.venv-py313/bin/uvicorn main:app --port 8900
# 另开终端
npm --prefix dataagent-frontend run dev
```

测试：仓库根运行 `dataagent-backend/.venv-py313/bin/python -m pytest`；前端与 Pi 运行时分别运行 `npm test`。

## 贡献

欢迎提交 Issue 和 PR，开始前请阅读 [贡献指南](CONTRIBUTING.md)。版本变更见 [CHANGELOG](CHANGELOG.md)，已确认的后续工作见 [docs/TODO.md](docs/TODO.md)。

## 许可证

本项目采用 [GNU General Public License v3.0](LICENSE) 开源协议。

## Star 趋势

[![Star History Chart](https://api.star-history.com/svg?repos=opendata-lab/opendataagent&type=Date)](https://star-history.com/#opendata-lab/opendataagent&Date)
