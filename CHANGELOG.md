# Changelog

本文件记录对使用者可见的变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

对话组件包 `@opendataagent/agent-conversation` 升至 0.3.0（移除 `planMode`）。

### Changed

- 对话组件：批准计划卡片后，权限模式选择器切换到 `acceptEdits`（与 DataAgent 批准计划后的会话模式一致），宿主未提供该选项时保持不变。
- 对话组件：失败或中断后的“重试”改为发送“继续”且不带附件，由 Agent 接着原运行继续，不再重复发送原请求。

### Removed

- 对话组件：移除独立的“计划”开关（`composerConfig.planMode`），计划模式改由权限模式选择器中的 `plan` 选项提供。

## [0.1.1] - 2026-11-11

补丁发布，支持沙箱网络模式配置。

### Added

- 沙箱网络模式配置 `DATAAGENT_SANDBOX_NETWORK_MODE`（支持 `bridge` 默认与 `host`）：当沙箱网络开启（`DATAAGENT_SANDBOX_NETWORK_ENABLED=true`）且处于特定内网/VPN/NAT 环境时，可配置为 `host` 直接共享宿主机网络栈，解决数据库连接与 `pip install` 无法触达外部网络的问题；断网模式（`false`）下严格保持 `--network=none` 隔离。
- `deploy/docker-compose.yml` 显式声明 `DATAAGENT_SANDBOX_NETWORK_ENABLED` 与 `DATAAGENT_SANDBOX_NETWORK_MODE`。

## [0.1.0] - 2026-11-10

首个独立发布版本，配合 OpenDataWorks 1.6.0 使用。

### Added

- 从 OpenDataWorks 迁入 DataAgent，作为独立仓库维护：后端、前端、Pi 运行时、契约、通用 Skill 与评测器。
- 独立 Docker Compose 部署（项目名 `opendataagent`）、启停与日志脚本、离线部署包 `oda-deployment-*.tar.xz`。
- 镜像统一为 `oda-frontend`、`oda-backend`、`oda-sandbox`、`oda-evals-*`。
- Pi 运行时：每个执行段独立 Pi 进程，工具在按需启动的一次性沙箱容器中执行，只挂当前会话工作区、不含凭据。
- 沙箱网络开关 `DATAAGENT_SANDBOX_NETWORK_ENABLED`（默认关闭）与会话级 `pip install`；启动日志与每次启动沙箱时记录实际生效的网络模式。
- 从 OpenDataWorks 内置部署迁移的脚本 `scripts/migrate-from-opendataworks.sh`（随发布包提供），见[部署指南](deploy/README.md)。
- Widget 后台任务提醒：其他会话完成或需要处理时显示提示、历史列表标签；面板最小化时启动按钮显示红点与提示气泡，点击直达该会话。
- CI：后端、Pi 运行时与前端测试；main 推送刷新 `latest` 离线包，`v*` tag 创建正式 Release。

### Changed

- 不再内置任何数据平台；外部系统只通过智能体显式绑定的 MCP 访问。每次 MCP 调用附加 `X-Agent-User-Id`／`X-Agent-Id` 标识，是否据此授权由 MCP 决定（OpenDataWorks 只按服务账号 Token 授权，这两个头仅记审计）。
- 只保留 Pi 运行时（`pi_agent_core`），Claude Agent SDK 路径与 `DATAAGENT_SANDBOX_MODE` 已删除；所有任务使用同一执行预算（`agent_timeout_seconds`），删除 `execution_mode`。
- 数据库连接只来自环境变量：`da_agent_settings` 不再保存或覆盖 MySQL 连接，并删除无用列（迁移 `20261106_000033`）。
- 智能体设置中的「数据与权限」改为「可见范围」。
- 运行时失败码规范化：只保留已知错误码，其余为 `PI_EXECUTION_ERROR`；`CELL_LOSS` 区分内存不足、被系统强制终止与异常中断，并给出可读提示。

### Removed

- 未被调用的孤儿工作区清理函数（会误删运行时目录下的非话题目录）。

### Fixed

- 沙箱单命令超时从容器就绪后开始计时。
- 达到轮次上限时任务正确失败；nginx 动态重新解析后端地址。
- Pi 发布包内包含 MCP 探测入口。
- Skill 导出按钮恢复；前端图标统一为 DataAgent 品牌图标。
