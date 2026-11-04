# 运行时整合与 Docker 沙箱架构评审及验证

日期：2026-11-03

关联：[设计](../design/2026-11-03-agent-runtime-sandbox-architecture-design.md)、[计划](../plans/2026-11-03-agent-runtime-sandbox-architecture-plan.md)。

## 交付结果

已将实际执行链路改为 backend → Pi SDK Cell → 工具专用 Docker 沙箱，移除 Runner、Claude SDK、宿主工具和正则路径边界。Pi SDK 1.4.0 使用内存配置、凭据、会话与显式资源加载器；SDK 默认宿主实现由同名沙箱工具替换。固定七个基础工具，智能体只配置 Skills/MCP。

问题/计划/权限卡片结束执行段，事务保存会话、卡片及等待状态；用户回答在同一 task_id 下重新调度。累计时间、轮次和工具预算保留，等待不计时。具体 MCP 写批准绑定服务配置身份、工具及原参数；操作结果先落库再确认，未知外部结果不自动重放。

原始大 JSON/文本保存至 topic results，只读挂载 `/mnt/results`；UI 结构化结果和模型预览分离。部署入口初始化卷、同步内置技能、配置 socket 附加组，再以配置 UID/GID 降权。新增结果迁移与回退准备脚本。

## 原始评审问题与处理

| 问题 | 结果与证据 |
| --- | --- |
| R1：会话条目不能恢复等待中的 Promise/批次 | 删除 broker；卡片完成本段，回答作为新输入。SDK 导入、MySQL 事务和 HTTP 重启续聊通过；未执行工具不补跑。 |
| R2：SDK 默认 write/edit 泄漏到宿主 | `tools: []` 经探针证实也会禁用 customTools，因此采用显式工具名清单及同名覆盖。逐一实际执行七工具，工作区可写，宿主文件不可见，只读挂载拒写。 |
| R3：本地 realpath 无法隔离任意 Bash | 本地开发同样使用 Docker；仅在确认缺 Docker CLI 时选择 Podman，不回退宿主执行。 |
| R4：完整结果缺少报表输入通道 | 20,001 字节输出、1,000 行 MCP 原始 JSON 直接由沙箱 Python 读取，核对完整数量及周边元数据。 |
| R5：取消初始化服务后的权限/socket | 实际 backend 镜像在新卷、复用卷运行，PID 1 UID/GID 1000，socket 可用，daemon mount 映射及工具写入通过。显式 `gosu user:group` 会丢附加组，已改为配置主组后 `gosu user`。 |
| R6：迁移/回退不能仅看新增表 | 旧 JSONL 幂等转换、原文件保留、不覆盖；真实旧镜像需要识别 Alembic revision，因此先运行新版回退准备脚本将戳恢复为 31。旧镜像历史、输出及文本追问通过。再次升级丢弃过期 SDK 快照，从文本重建。 |
| R7：parallel 会改变业务顺序 | 保留 sequential，卡片后阻止同批后续工具；计划拒绝仍禁止写，MCP 批准只领取一次。 |
| R8：杀 docker exec 不能停止后代 | 命令超时销毁整段容器，Cell finally 与 backend 清理兜底；真实后代、取消和 idle 测试通过，无遗留沙箱。 |

## 实际环境

- macOS arm64；nvm 0.40.3，Node 22.22.0（仓库）/22.19.0（Pi 子目录）。本轮最终测试使用 22.22.0；两版本均完成过构建。
- pyenv 2.8.14，Python 3.13.18，虚拟环境 `dataagent-backend/.venv-py313`；requirements 已安装。OpenSSL 3.6.5、xz/lzma、TLS 导入及请求校验通过。
- Docker Desktop 4.98.0 / Engine 29.9.2；构建 `oda-sandbox:test`、`oda-backend:test` 及基于改造前 HEAD 的 `oda-backend:rollback-test`。
- MySQL 容器 `dataagent-mysql`：`127.0.0.1:3326/dataagent`；Redis `odw-local-redis`：`127.0.0.1:6379`，以 Docker bootstrap。容器部署演练使用专用临时 schema、network、skills volume、workspace 和 Redis DB 14。
- **没有调用真实模型或使用真实 provider 凭据**。HTTP 使用本地 OpenAI 格式 SSE fixture 和真实 HTTP MCP fixture，生产入口、协调器、Pi SDK、Docker、MySQL、Redis、事件流和消息持久化均实际执行。用户自行配置真实 provider，未修改其设置。
- 官方 PyPI CDN 大包下载中断且极慢，本次本机安装与镜像构建显式使用清华 PyPI 镜像；仓库默认源仍为官方 PyPI。

## 验证记录

| 检查 | 结果 |
| --- | --- |
| Pi 全量 `DATAAGENT_SANDBOX_TEST_IMAGE=oda-sandbox:test npm test` | 92 项通过，无跳过；含 SDK 配置、卡片会话、真实 Docker 七工具、输出消费、取消、预算与持久化失败。 |
| Python 全量（含 MySQL opt-in 与部署脚本） | 690 项通过，无跳过；已包含真实 MySQL 事务、报告 JSON 文件输入和 xz 离线打包测试。 |
| 前端 CI 选择集 | 743 项通过（排除既有 ToolOutputRenderer spec，与 CI 一致）。 |
| 前端完整测试 | 复现既有 ToolOutputRenderer 的 10 项失败；本轮 demo tools 断言已更新，未更改这组既有组件行为。 |
| 前端构建 | `build`、`build:widget`、`build:sdk`、`test:sdk-types` 均通过，React 18/19 类型检查通过。 |
| 真实 HTTP 全链路 | liveness、七工具、大 MCP 结果、完整 1,000 行 Excel、完整 chart_spec、问题卡重启/重复回答、计划允许/拒绝、写批准允许/拒绝/重复、未知外部写、取消、命令与 idle 超时通过。 |
| 实际容器镜像 | fresh/reused volumes、非 root 服务/socket、技能、daemon mount 映射、迁移、旧镜像回退及历史文本追问通过；再次升级重建 SDK 状态并保留结果通过。 |
| 本地真实浏览器 | 新建智能体、Skills 选择、保存后刷新、发布；无基础工具选择。真实任务的问题卡从历史恢复，UI 回答后收到 `resumed-ok`，原工具批次不重放。 |
| 部署资产 | 离线打包/加载/升级脚本测试 5 项通过，Compose 实际解析；代理 SSE 1860s > 后台 1800s。 |

HTTP 冒烟：`dataagent-backend/tests/runtime_http_smoke.py`。先构建 Pi 与 `oda-sandbox:test`，启动本地 MySQL/Redis，释放 8900/8919/8920 端口，再以 `.venv-py313/bin/python` 运行。脚本仅创建并清理自己的 provider/MCP/agent/topics，退出时停止自身进程。

容器演练：`dataagent-backend/tests/runtime_container_smoke.py`，需要上述三个测试镜像及本地 MySQL/Redis 容器；8902/8919 空闲。验证使用真实 `docker run` 的 backend 部署拓扑，Compose 配置另行解析验证；未启动整套 Compose 前端服务。

本地日志保存在 `/tmp/dataagent-runtime-tests.log`、`/tmp/dataagent-backend-tests.log`、`/tmp/dataagent-frontend-tests.log`、`/tmp/dataagent-http-smoke.log`、`/tmp/dataagent-container-smoke.log`。UI 截图 `/tmp/dataagent-agent-ui.png`、`/tmp/dataagent-card-ui.png`。临时会话、配置、进程和容器均清理；MySQL/Redis 保留供重复验证。

## 验证边界与后续

真实 provider 的 liveness、真实业务 MCP 问数“最近 30 天工作流发布次数趋势”、完整图表/报告业务结果验收待用户配置后运行。完整 1,000 行 Excel 与图表契约已经真实 MCP→文件→沙箱技能→事件持久化覆盖，另有前端契约测试，本次浏览器实际验收问题卡及配置，不宣称已完成真实模型 NL2SQL 验证。

全新无外网机器上的完整离线包启动、远程 amd64/arm64 镜像发布、Podman 实机验证未运行。依赖审计、既有 ToolOutputRenderer 失败和 UI 旧图标路径缺失均已记录在 [TODO](../TODO.md)。

## 最终核对

Python 690/690、Pi 92/92、前端 CI 选择集 743/743 通过；HTTP 及容器回退/再升级演练最终重跑通过。报告技能 `--input-file` 与文档不一致、JSON 路径误按 CSV 读取已在技能本地修正，11 项技能回归通过；未增加共享运行时包装。大结构化 Bash 输出单独回归保证 UI 得到完整 payload，模型只得到有界预览和路径。
