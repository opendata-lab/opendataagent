# DataAgent 构建优化验证

日期：2026-11-03。关联：[设计](../design/2026-11-03-ci-build-optimization-design.md)、[计划](../plans/2026-11-03-ci-build-optimization-plan.md)。

## 范围与差异

OpenDataWorks 已在 aa6eacfb2（PR #492）提交其自身的同类优化，DataAgent 的四个构建文件仍未提交。两仓库独立维护。DataAgent 本次保留单次 build/push、服务级缓存、BUILDPLATFORM 编译和 npm ci；将并发取消改为仅 PR，避免中断 main/tag 发布。actionlint 指出两个旧 gh-release@v1 的运行时不可用，已改为 v2，未改变 Release 生命周期。

## 验证结果

- 使用仓库 .nvmrc：Node 22.22.0。
- 前端历史引擎与 widget：49 项测试通过。
- 前端完整 build 和 build:widget 均通过。完整构建保留既有大 chunk 提示，不在本轮调整页面拆分。
- 隔离目录内 npm ci --ignore-scripts --dry-run 成功，验证前端 package.json 与 lockfile 一致。
- actionlint v1.7.7：修正 gh-release 版本后通过；调用时禁用外部 shellcheck 集成，因此不将内嵌 shell 内容记为独立 shellcheck 验收。
- Podman 原生 linux/arm64 构建 backend Dockerfile 的 pi-cell 阶段成功，执行了 npm ci --ignore-scripts、tsc 与 npm prune --omit=dev。runner 中该阶段内容一致。随后在构建出的容器内关闭网络，成功导入 Pi core 和 MCP SDK 的生产依赖。
- Pi lockfile 无 cpu/os 限定包，本地 node_modules 未发现 .node addon；本轮未修改 Pi 源码、package.json 或 lockfile。
- Pi 全量测试：184 项，182 通过，2 失败。失败为 cell.init Bash timeout 2s reaches the real command 与 exiting without flushing does lose frames，均已在既有 TODO 记录。定向重跑前者仍失败，后者通过；本轮不改动时序测试以制造通过结果。
- Pi 生产依赖 npm audit：9 项现有告警（5 moderate、3 high、1 critical）。锁文件未改动，已在 TODO 记录后续治理。

## 范围限制

本机验证了 Linux arm64 编译阶段，并未运行完整 amd64/arm64 后端与 runner 最终镜像、GHA 远程缓存和镜像发布；这些需要本次推送后的远程工作流确认。Python 运行镜像、Node 二进制及 Docker CLI 的 COPY 仍按目标平台选择。

## 本地工具说明

本机默认 Go 二进制与 GOROOT 不一致；改用已安装 Go 1.24.13 并显式启用 sum.golang.org 后运行 actionlint 成功，未修改系统 Go 配置。Podman 代替不可用的 Docker CLI；BUILDPLATFORM 为本地检查显式传参，不能将此检查等同 BuildKit 双平台发布。

OpenDataWorks 技能副本和截图只加入 .git/info/exclude，未纳入源码或提交。本次不会发布额外版本标签。
