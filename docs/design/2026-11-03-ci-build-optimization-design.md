# DataAgent 镜像构建优化设计

日期：2026-11-03。范围：GitHub Actions、前端及 Pi 构建阶段；不修改运行时 API、数据库或镜像名称。

## 当前状态与问题

发布工作流先 build(push=false)，随后再次 build(push=true)，重复调度同一镜像构建。缓存读写配置分散；前端 Docker 构建使用 npm install。多平台构建时前端静态资源和 Pi 的 TypeScript 编译不需要在目标架构模拟器内执行。

## 方案

- 合并为单个 build-push-action 步骤，通过事件条件选择是否发布；缓存统一为按服务区分的 buildx scope，保留双架构输出与现有标签。
- 工作流采用同分支并发分组，但只取消 PR 检查；main、tag、手动发布不中断，避免多镜像只完成部分发布。构建 job 有界为 60 分钟。
- 前端 builder、后端及 runner 的 pi-cell 阶段使用 BUILDPLATFORM。最终 Python、Node 运行二进制及 Docker CLI 仍按目标平台获取，不复制宿主 Node 二进制。
- 前端 npm install 改为 npm ci，按已提交 lockfile 安装。
- actionlint 发现现有两个 softprops/action-gh-release@v1 入口使用过时的 Action 运行时，统一升级为 @v2，保持原有输入与发布触发规则。

## 边界与取舍

Pi 阶段当前 npm ci 已禁用 install scripts；检查 lockfile 无 cpu/os 限定依赖、无 native addon 构建入口，产物为 JavaScript。将来引入原生依赖时必须重新评估 node_modules 跨架构复制。保留既有源码组织、镜像与部署合同，不引入其他构建路径。

不在本轮修改 GitHub Release 生命周期、平台权限或 OpenDataWorks 仓库；仅更新上述已被 lint 拒绝的发布 Action 运行时。后者已有独立提交 aa6eacfb2，本轮修改的是 DataAgent 自身流程。

## 验证

工作流使用 actionlint 检查；前端回归与构建、Pi 构建测试按仓库 Node 基线运行。有本地容器能力时验证 pi-cell 阶段；完整双架构镜像及 GHA 缓存发布需通过远程工作流确认，未执行的范围明确记入计划和 TODO。
