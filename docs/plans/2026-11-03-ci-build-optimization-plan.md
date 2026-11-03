# DataAgent 镜像构建优化计划

日期：2026-11-03。关联：[设计](../design/2026-11-03-ci-build-optimization-design.md)。

1. 核对已有四个文件的差异、最终平台镜像来源及 Pi 依赖；排除本地导入技能和截图。
2. 修正 `.github/workflows/docker-build.yml` 并发取消条件，仅取消 PR；保留一次构建、统一缓存和 job 超时；两个 gh-release Action 升级至 v2，解决 lint 指出的旧运行时。
3. 保留三个 Dockerfile 的 BUILDPLATFORM 优化与前端 npm ci，补充前后端架构边界说明。
4. 执行 actionlint、前端针对性测试及构建、Pi 测试；可用时通过 Podman 检查 pi-cell。记录结果与未覆盖范围。
5. 审核并提交构建相关文件及文档；与聊天历史修复分开提交，推送 main。

发布沿用现有 main 工作流，不另打版本标签；推送后检查远程运行状态。回退使用 revert 本次构建优化提交，恢复原构建配置，无数据库迁移。

## 执行结果

本地工作流检查、49 项前端测试、前端两种构建、Linux arm64 pi-cell 构建及生产依赖导入通过。Pi 全量的两项既有时序失败、依赖审计告警及最终双架构验收边界均已在 TODO 和[验证报告](../reports/2026-11-03-ci-build-optimization-verification.md)记录。
