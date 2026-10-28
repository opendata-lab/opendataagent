# OpenDataAgent

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="dataagent-frontend/public/opendataworks-icon-dark.svg">
    <img src="dataagent-frontend/public/opendataworks-icon-light.svg" alt="OpenDataAgent icon" width="160">
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

**A self-hosted data agent platform you can embed in any system.**

[简体中文](README.md) | English

[Quick start](#quick-start) · [Architecture](docs/handbook/architecture.md) · [Conversation API](docs/handbook/api.md) · [Widget](docs/handbook/widget.md) · [Deployment](deploy/README.md) · [Contributing](CONTRIBUTING.md)

</div>

---

## Overview

OpenDataAgent (DataAgent) is a standalone agent platform. Administrators configure models, MCP servers and skills and assemble them into agents. Users ask questions in natural language, either on the chat page or in a widget embedded in a host system. The agent calls tools inside an isolated sandbox and presents the result as charts, tables and reports.

It ships without any built-in data platform. External systems are reached only through MCP servers bound explicitly to an agent, and every call carries the execution identity (user and agent), so the MCP side decides what data is visible. [OpenDataWorks](https://github.com/opendata-lab/opendataworks) is one host integrated this way.

## Features

- **Agent composition**: combine a model, system prompt, MCP servers, skills and run budgets in the admin console, with separate drafts and published versions.
- **Two runtimes**: Pi (`pi_agent_core`) by default, or the Claude Agent SDK; events are persisted and streamed over SSE.
- **Sandboxing**: in container mode each task runs in a one-off child container that mounts only its conversation workspace.
- **MCP with identity**: HTTP MCP servers added by an administrator; the backend attaches `X-Agent-User-Id` / `X-Agent-Id` to every call.
- **Skills**: built-in chart and report skills, plus import, version history and in-browser editing.
- **Embeddable**: one script tag for the widget, or the npm package [`@opendataagent/agent-conversation`](dataagent-frontend/packages/agent-conversation/README.md) to build your own chat UI.
- **Interaction cards**: tool approvals, questions to the user and plan confirmation; the task suspends until the user decides.
- **Evaluation**: builtin, DeepEval and Opik evaluators, with evaluation sets and run results in the admin console.

## Quick start

Requires Docker (or Podman) with Compose.

```bash
git clone https://github.com/opendata-lab/opendataagent.git
cd opendataagent
cp deploy/.env.example deploy/.env   # change the database passwords at least
scripts/start.sh
```

| Service | URL |
| --- | --- |
| Admin console and chat | http://localhost:8901 |
| Backend API | http://localhost:8900/api/v1/nl2sql |
| Widget script | http://localhost:8901/widget/dataagent-widget.bundle.js |

After startup, configure a model under Settings, then add MCP servers and bind them to agents as needed.

### Offline deployment

Every [release](https://github.com/opendata-lab/opendataagent/releases) carries an offline package `oda-deployment-<version>.tar.xz` (`latest` follows the main branch):

```bash
tar -xJf oda-deployment-<version>.tar.xz && cd oda-deployment
scripts/load-package-and-start.sh --package .
```

See the [deployment guide](deploy/README.md) for building your own package, runtime settings, login, reverse proxying for host embedding, and migrating data from the OpenDataWorks bundled deployment.

## Embedding

```html
<script src="http://<dataagent-host>:8901/widget/dataagent-widget.bundle.js"
        data-website-id="<registered site id>"
        data-agent-id="<agent id>"
        data-api-base-url="http://<dataagent-host>:8900"></script>
```

Register the site and its allowed origins under "Widget access" in the admin console first. See [Widget](docs/handbook/widget.md) for all options, programmatic installation and host-reported user identity.

## Repository layout

| Directory | Contents |
| --- | --- |
| `dataagent-backend/` | FastAPI backend, task coordinator, runtime adapters, Alembic migrations |
| `dataagent-frontend/` | Vue 3 admin console and chat, widget, `@opendataagent/agent-conversation` SDK |
| `dataagent-runtime-pi/` | Pi runtime (TypeScript) |
| `contracts/` | JSON Schema contracts shared by frontend and backend |
| `.claude/skills/` | Built-in general-purpose skills (charts, reports) |
| `tools/dataagent-evals/` | Evaluators (builtin / DeepEval / Opik) |
| `deploy/` | Docker Compose and environment templates |
| `scripts/` | Start/stop, log export, offline packaging, evaluation scripts |
| `docs/` | Architecture, API and widget docs, backlog |

## Development

```bash
python3.13 -m venv dataagent-backend/.venv-py313
dataagent-backend/.venv-py313/bin/pip install -r dataagent-backend/requirements.txt
nvm use
npm --prefix dataagent-runtime-pi ci && npm --prefix dataagent-runtime-pi run build
npm --prefix dataagent-frontend ci
```

Provide a MySQL schema and Redis, set `SKILLS_ROOT_DIR` (absolute path of the repository's `.claude/skills`) and `DATAAGENT_HOST_ROOT`, then run `alembic upgrade head` and `uvicorn main:app --port 8900` in `dataagent-backend/`, and `npm --prefix dataagent-frontend run dev`.

Tests: `dataagent-backend/.venv-py313/bin/python -m pytest` from the repository root; `npm test` in the frontend and the Pi runtime.

## Contributing

Issues and pull requests are welcome; please read [CONTRIBUTING](CONTRIBUTING.md) first. See [CHANGELOG](CHANGELOG.md) for release notes.

## License

[GNU General Public License v3.0](LICENSE).
