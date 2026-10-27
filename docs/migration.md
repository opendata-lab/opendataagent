# Repository separation — 2026-10-27

Imported tracked DataAgent sources from opendataworks main `828eb2f8`. Python/backend, frontend, Pi runtime, generic skills and eval tooling now belong here. Platform MCP and five platform skills remain upstream. No old-name compatibility is provided.

Alembic `20261027_000031` converts old platform MCP/profile records to editable administrator-owned records and changes topic source to `web`. Existing records retain their IDs, connection contents and history references. Configure the external MCP URL, token header and renamed tools manually before using old agents. Fresh databases do not seed platform agents. Historical seed migrations were adjusted accordingly.

Widget global: `DataAgentWidget`; bundle: `dataagent-widget.bundle.js`; client headers: `X-DataAgent-*`. HTTP MCP always receives execution identity headers. Images retain the required `opendataworks-dataagent-*` names.

Validation evidence is recorded in the source repository at `docs/reports/2026-10-27-dataagent-opendataworks-decoupling-verification.md`.
