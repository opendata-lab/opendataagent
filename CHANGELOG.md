# Changelog

本文件记录对使用者可见的变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

### Added

- 从 OpenDataWorks 迁入 DataAgent，作为独立仓库维护：后端、前端、Pi 运行时、契约、通用 Skill 与评测器。
- 独立 Docker Compose 部署（项目名 `opendataagent`）、启停与日志脚本、离线部署包 `oda-deployment-*.tar.xz`。
- 镜像统一为 `oda-frontend`、`oda-backend`、`oda-runner`、`oda-evals-*`。
- Widget 会话在无 DataAgent 登录时以宿主上报的用户执行。
- CI：后端、Pi 运行时与前端测试；main 推送刷新 `latest` 离线包，`v*` tag 创建正式 Release。

### Changed

- 不再内置任何数据平台；外部系统只通过智能体显式绑定的 MCP 访问。
- 从 OpenDataWorks 内置部署迁移旧数据的步骤见 [部署指南](deploy/README.md)。
