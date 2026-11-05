# dataagent-runtime-pi

DataAgent 的 Pi 控制面子进程，基于 `@earendil-works/pi-coding-agent@1.4.0`。
Python backend 负责调度、凭据、事件和会话持久化；Cell 使用内存 SDK 设置和显式资源加载器，不读取宿主/global skills、扩展、会话或认证文件。

## 工具与会话

固定注册 `bash/read/write/edit/ls/find/grep`，以同名自定义实现覆盖 SDK 默认宿主工具。所有模型路径在 `oda-sandbox` 内解析；工作区读写，当前 topic 结果与启用 skills 只读，无网络、凭据或 socket。MCP 仅使用数据库下发配置，由控制面连接。

同段懒启动一个容器；正常结束、卡片、取消和命令超时均销毁。卡片结束 SDK 段并保存合法 session header/entries/leaf，回答后同一任务导入并追加新输入，不重放批次。写批准绑定具体 MCP 服务/工具/参数，实际结果落库确认后才继续推理；未知结果不重试。

大结果保存原始 JSON/文本，返回 `/mnt/results/<name>` 及有界预览；普通沙箱工具和 Python 直接消费完整文件。UI 结构化结果独立保存，避免模型摘要破坏图表。

## 构建与验证

```bash
. "$HOME/.nvm/nvm.sh" && nvm use
npm ci
npm run typecheck
# 在仓库根构建，然后回到本目录
# docker build -f dataagent-backend/Dockerfile.sandbox -t oda-sandbox:test .
DATAAGENT_SANDBOX_TEST_IMAGE=oda-sandbox:test npm test
```

`npm run build` 清理并生成 `dist/src/main.js`。backend 镜像的多阶段构建吸收此产物，本包不产出独立服务镜像。开发环境必须先构建，Python 的 `DATAAGENT_RUNTIME_PI_DIR` 指向本目录。

协议 v2 定义见 `src/protocol/frames.ts` 与 `dataagent-backend/core/pi_runtime.py`：`session.entries` 在终态或 `run.parked` 前输出，`operation.result` 通过 `operation.saved` 确认持久化。Python 定向协议测试覆盖超时、取消、排空和进程退出。

完整设计、迁移和本地 HTTP/部署验证见 [设计](../docs/design/2026-11-03-agent-runtime-sandbox-architecture-design.md)、[计划](../docs/plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)、[报告](../docs/reports/2026-11-03-agent-runtime-sandbox-architecture-review.md)。
