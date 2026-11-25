# 平台与 Widget 会话统一和续聊

## 2026-11-19 同源 Widget 方案（现状）

> 2026-11-24 起同源平台会话接入已移除（用户决定砍掉），Widget 只经宿主服务端转发接入，见下文「独立登录 Widget 的账号归属」。本节保留作历史。

当前 Chat 将来源同时用作列表和权限开关：平台默认可聊天，Widget 和「全部」进入只读视图。两端虽复用话题与任务接口，普通平台会话按平台登录身份授权，Widget 历史则按站点与宿主提供的用户 ID 或访客 ID 授权。不能直接按字符串把这些身份视为同一人。

Chat 使用同一个会话列表，默认显示当前身份有权打开的会话，来源显示为记录属性并可筛选。管理员同一列表可见其他用户的会话，可按平台用户和来源筛选；点击自己的会话直接续聊，点击其他会话直接进入只读详情，没有模式切换。Widget 继续显示适合嵌入面板宽度的一份历史。两端打开同一话题 ID 并通过原有消息、任务、事件及文件接口续聊。

同源 Widget 可显式设置 `usePlatformSession: true`（HTML 为 `data-use-platform-session="true"`），使用真实的 DataAgent 登录会话。API 仍验证 Widget 站点，平台会话模式拒绝跨来源的浏览器请求及使用站点密钥的服务端接入；认证失败直接提示登录。服务端使用已校验的账号 ID 作为共同的会话所有者，忽略客户端外部用户 ID。来源仍按创建入口记为 `web` 或 `widget`，创建时保留 Widget 站点。平台可读取自己账号的 Widget 会话；Widget 可读取同账号的平台会话和当前站点的新 Widget 会话，不越过站点读取其他 Widget 记录。Widget 不继承管理员跨用户会话权限。启用认证后，管理员的普通会话接口和运行接口也只接受本人会话；管理员本人创建的调试会话仍可使用。

2026-11-19 初期方案复用 `da_agent_topic.auth_user_id` 等字段并在响应中增加 `source`；账号模型、数据库迁移及独立登录接入的最终约定见下文「独立登录 Widget 的账号归属（2026-11-23）」。

管理员可以在同一个列表读取平台与 Widget 的其他会话、预览图片和文本、下载用户上传的文件与生成的产物。读取走同一管理员路由组下的 `/topics/{topic_id}`、`/messages` 和 `/files`；文件复用普通会话的路径约束和呈现规则。历史查看不开放发送、编辑、上传、取消等写权限。管理员列表与详情接口给出 `can_continue`，界面用它选择普通对话或管理只读接口；写权限仍由运行接口强制。管理员列表支持继续加载后续会话，来源筛选不改变当前会话的操作权限。反馈和记忆跳转使用普通会话 URL；旧 `view=audit` 链接仍按会话归属解析。现有 Widget 管理消息路径保留给已有调用方。

## 独立登录 Widget 的账号归属（2026-11-23）

网页、MCP 和个人 Widget 创建的新会话都归属同一个平台账号。`source` 和 `website_id` 记录入口和站点，并用于 Widget 的站点访问范围。用户在宿主登录为 `alice` 后可以直接使用 Widget。同一账号已存在时复用，首次通过可信外部认证时自动注册并分配普通用户角色。

Superset 的安全管理使用 Flask-AppBuilder。其 `ab_user.id` 是整数主键，`username` 独立且唯一，`ab_user_role` 将整数用户 ID 和角色 ID 关联，角色再关联页面、操作和资源权限。`local:alice` 是 DataAgent 现有的身份字符串格式。新设计采用整数账号主键、用户名 `alice` 和独立的外部身份绑定，用户角色与授权也独立维护。[Superset 安全管理器](https://github.com/apache/superset/blob/master/superset/security/manager.py) · [Flask-AppBuilder 用户与角色模型](https://github.com/dpgaspar/Flask-AppBuilder/blob/master/flask_appbuilder/security/sqla/models.py#L98-L181) · [Superset 权限设计](https://superset.apache.org/admin-docs/security/#permissions)

### 已接入站点传值核对

| 接入 | 当前主分支的传值 | 能否对应平台账号 |
| --- | --- | --- |
| OpenDataWorks 门户 `opendataworks-portal` | 悬浮和嵌入问数共用 `portalWidgetIdentity()`，取门户 `/auth/me` 的 `username` 和 `authSource`。本地用户传 `local:<用户名>`，OAuth 用户传 `<registration id>:<sub>`，例如 `local:alice` 和 `sso:u-42`。 | 本地两边采用同一账号命名规则时，`local:alice` 字符串一致。OAuth 两边须核对身份源、实际 `sub` 和 DataAgent provider 名。相同身份源和 `sub` 下，注册名不同时按部署配置映射至 DataAgent provider。当前值来自浏览器，只能作为对应候选。 |
| OntoFoundry `ontofoundry` | 建模页用对话 SDK，BFF 以 Widget 客户端发请求，传 `ontofoundry:<workspace_id>:<session_id>`。访问由 OntoFoundry 工作空间权限控制。 | 该值标识共享建模会话。多人可以操作同一会话，会话归属继续由宿主控制。 |

[门户取值](https://github.com/opendata-lab/opendataworks/blob/8e704f90e2b864c29e451f47825d085b330bcd92/frontend/src/utils/dataagentWidgetLoader.js#L58-L74) · [DataAgent OAuth 账号构造](../../dataagent-backend/api/auth_routes.py#L292) · [OntoFoundry 请求头](https://github.com/opendata-lab/ontofoundry/blob/1984b00dc9041e7feb126f6429c0b2b9d052e13c/apps/api/src/ontofoundry_api/services/dataagent.py#L68-L81) · [OntoFoundry 会话标识构造](https://github.com/opendata-lab/ontofoundry/blob/1984b00dc9041e7feb126f6429c0b2b9d052e13c/apps/api/src/ontofoundry_api/api/agent_conversation.py#L175-L188)

2026-11-23 对本机 MySQL 做了只读核对。`dataagent` 登记了上述两个站点，表内有 35 条网页会话，Widget 会话为 0。`dataagent_eval` 的 19 条 Widget 会话全部属于 OntoFoundry，均为上述共享会话键，且没有平台账号 ID。OpenDataWorks 的 Widget 定向测试 19 项、OntoFoundry 请求头及客户端测试 7 项、DataAgent 现有 Widget 路由和隔离测试 47 项通过。生产请求、真实账号以及生产身份源配置待核对。

### 可信接入与自动注册

1. 独立宿主的 Widget 请求走宿主自己的已登录后端。宿主逐请求检查登录和账号状态，从后端的登录主体取得用户 ID，重写用户头与站点头，再用现有 Widget 服务端接入密钥向 DataAgent 转发。密钥仅位于服务端。DataAgent 开启平台认证，为该站点要求服务端密钥并启用个人账号归属。缺少身份或站点凭据时拒绝请求。OntoFoundry 同样转发已登录用户，不再使用共享会话键（2026-11-24 用户决定：不允许共享会话）。
2. 宿主转发的 `X-DataAgent-User-Id` 就是用户名，等同于 DataAgent 同名账号（2026-11-24 用户决定：Widget 传入的用户就是真实用户）。DataAgent 以 `(website_id, 用户名)` 记录外部身份，首次访问按用户名绑定已有同名账号或注册普通账号。宿主只传用户名，不传显示名和邮箱。不提供来源映射、命名空间开关和手动绑定外部身份。宿主的 `admin` 就是平台 `admin`；Widget 凭据只能访问问答和助手目录接口，管理类接口仍只接受平台登录。
3. 可信 Widget 和主站 OAuth 共用账号登记服务。匹配到的账号复用原用户记录。首次注册在同一事务创建账号、外部绑定、普通用户角色关联，并将管理员配置的默认密码哈希写入该账号。并发访问随后复用该账号。主站通过 OAuth 或密码登录后可以申请该账号的 MCP 个人访问令牌。
4. 内部账号 ID 一律为整数，引用平台账号的列统一为 `user_id BIGINT REFERENCES da_user(id)`。新会话写入新增的 `da_agent_topic.user_id`；升级后手动执行 `scripts/migrate-accounts.sh`，按 `da_user_identity` 匹配旧 `auth_user_id`（`local:<u>` / `<provider>:<sub>`，不区分大小写）回填 `user_id`，匹配不到的旧会话保留 `user_id` 为空且仅管理员可读。代码仅保留按 `user_id` 的一套归属判断，不再保留按 `auth_user_id` 的旧路径。MCP 令牌表的 `user_id` 改为同样的外键，切换时直接删除旧令牌行（或重建表），用户重新申请。Widget 可访问当前账号的网页、MCP 和本站个人会话。

### 用户表与身份绑定

| 表 | 主键和用途 |
| --- | --- |
| `da_user` | `id BIGINT AUTO_INCREMENT` 主键。独立的唯一 `username` 用于密码登录，`display_name` 用于展示。例如 `id=42, username=alice, display_name=王小明`。另存密码哈希、启用状态、创建时间和最近登录时间。不存邮箱（2026-11-24 用户决定）。用户仅支持停用，不物理删除，以保证历史外键与 `da_agent_grant` 引用有效。 |
| `da_user_identity` | `(source, subject)` 联合唯一，`user_id BIGINT` 指向 `da_user.id`。每行把一个已验证的外部身份绑定到一个平台账号。 |
| `da_role` | 整数角色主键，并保存唯一角色标识、名称和说明。内置管理员和普通用户角色的标识由代码预留。 |
| `da_user_role` | `(user_id, role_id)` 唯一，分别为用户和角色的整数外键，支持一个用户分配多个角色。 |
| `da_agent_grant` | `(agent_id, principal_type, principal_id)` 联合主键。`agent_id` 对应工作台的 `da_agent_draft.agent_id`，发布前即可配置授权。`principal_type` 为 `user` 或 `role`，`principal_id BIGINT` 按类型引用用户或角色。授权服务写入时校验对象存在，删除助手或角色时同事务删除授权。 |

例如 `da_user` 第 `42` 行是 `username=alice`、`display_name=王小明`。该用户的两条外部身份记录如下。

| `source` | `subject` | `user_id` |
| --- | --- | --- |
| OpenDataWorks 宿主站点 | `alice` | `42` |
| 公司 SSO 签发方 | `u-8842` | `42` |

Widget 和 OAuth 通过各自的已验证身份查到 `42`。密码登录直接按 `username` 查到 `42`。MCP 令牌关联账号 `42`。请求得到同一个账号 ID 后，共用账号状态、角色、助手权限和会话归属检查。

#### 用户字段命名与外部映射

| 字段 | 示例 | 用途 |
| --- | --- | --- |
| `id` | `42` | 稳定的内部主键。会话和 MCP 令牌通过整数 `user_id` 引用。 |
| `username` | `alice` | 唯一的英文登录名。主站密码登录和管理端搜索使用它。界面标签为「用户名」。 |
| `display_name` | 王小明 | 用于用户菜单、会话归属和个人资料的显示名，支持中文等 Unicode 字符，可重名、可由本人修改。界面标签为「显示名」。 |

Widget 保留现有 `userId` 参数。可信宿主传入的 `alice` 按上述来源规则匹配或创建 `username=alice`。宿主只给出账号名时，初始显示名用 `alice`。

OIDC 将已验证的 `iss` 映射为配置的来源，以 `sub` 查外部身份绑定。自定义 OAuth 继续由现有 `OAUTH_USER_INFO` 钩子从已配置提供方的接口归一化稳定主体。自动注册时，`preferred_username` 可作为用户名候选，只有经共同目录确认属于稳定账号命名空间时才用于匹配已有用户。显示名用接入配置给出的 `display_name` 或 OIDC `name` 补全，用户设置的显示名优先。首次登录总是自动注册普通用户（不提供关闭开关），启用 OAuth 时必须配置 `DEFAULT_USER_PASSWORD`。[SCIM 用户名与显示名](https://www.rfc-editor.org/rfc/rfc7643.html#section-4.1.1) · [OIDC 标准声明](https://openid.net/specs/openid-connect-core-1_0.html#StandardClaims) · [OIDC 主体标识规则](https://openid.net/specs/openid-connect-core-1_0.html#ClaimStability)

### 默认密码与主站登录

管理员在 `DATAAGENT_CONFIG` 指向的服务端认证配置中设置统一的 `DEFAULT_USER_PASSWORD`。此做法仅适用于内网部署，默认密码视同共享凭据。启用自动注册时须配置该值。自动注册和管理员手动新建普通用户时，以同一个默认密码为每个账号分别计算带盐的 Argon2id 哈希。注册后即可用用户名和默认密码登录主站。所有密码登录按唯一用户名查 `da_user` 并验证账号状态和密码哈希。切换时不导入 `LOCAL_ADMINS` / `ADMIN_USERS`：部署只有一个本地 `admin`，升级后用 `python -m core.accounts create-user --username admin --password ... --admin` 创建或设置（可沿用原密码），之后密码和角色均通过数据库维护。密码只用 Argon2id，不再兼容旧 bcrypt 哈希。[现有登录配置](../../deploy/README.md#登录配置) · [OWASP 密码存储指引](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#argon2id)

用户登录后在「个人资料」输入当前密码和新密码，服务端验证当前密码后更新该账号的密码哈希。之后使用自己设定的密码登录。管理员重置普通用户密码时，将该账号恢复为当时配置的默认密码；管理员账号不能被重置。修改默认密码配置只影响之后新建或重置的账号。

### 用户管理、角色管理与个人资料

- **用户管理**。管理员搜索用户，查看用户名、资料、状态、来源和所属角色，手动新增用户、启用或停用账号、重置密码和调整角色。不支持通过界面或接口把用户设为管理员（2026-11-24 用户决定，部署只有 `admin` 一个管理员）：管理员只由命令行 `python -m core.accounts create-user --admin` 创建或在切换时导入，用户创建、用户角色调整和管理员角色成员接口拒绝新增管理员。默认密码因此只会落在普通用户上。

切换迁移 `20261123_000042` 只建表、只能向前（2026-11-24 用户决定）：降级直接报错。旧数据由运维手动执行 `scripts/migrate-accounts.sh`（`scripts/migrate-accounts.sql`，在 backend 镜像内用 MariaDB 客户端运行，可重复执行）迁移，代码中不保留数据迁移逻辑。外部身份的来源和标识不区分大小写（2026-11-24 用户决定）。升级前用离线包的 `scripts/backup-db.sh` 备份数据库与部署配置，回退时用 `scripts/restore-db.sh` 恢复备份并换回旧镜像。管理端用户列表及助手的用户选择器都从 `da_user` 读取。
- **角色管理**。管理员创建和编辑角色、分配角色给用户，并管理角色可使用的助手。角色页展示菜单和管理操作权限。自定义角色可授予助手使用权，菜单和管理操作由相应的内置角色提供。用户拥有各角色授权的并集，自动注册分配普通用户角色。
- **菜单与 API**。服务端以代码中的静态「角色 → 权限键」表计算菜单及管理权限，前端用同一份响应显示菜单并控制路由，后端用对应权限键保护 API。角色页只读展示菜单和管理权限，管理员通过给用户分配角色来授予这些权限（管理员角色除外）。
- **助手访问**。已通过入口认证的请求按「全部用户」「指定范围」两种模式判断（2026-11-24 起「登录用户」并入「全部用户」：所有入口都需登录，两者等价）。「指定范围」查 `da_agent_grant`，命中用户或任一所属角色即通过。工作台沿用从 `da_user` 选人的交互。用户授权和角色授权通过独立授权接口显式保存，即时写入同一张表。草稿保存和助手发布只处理助手配置及可见模式。切换时仅导入已发布的生效名单，草稿差异和无法映射的条目列入切换清单，由管理员通过授权界面处理。助手的数据源权限由下游凭据判定。
- **个人资料**。用户菜单提供「个人资料」，显示用户名、显示名、登录方式和所属角色，可查看自己的可用助手及菜单。用户可自己修改显示名和密码，角色和授权只读。

### 统一请求授权

`core/accounts` 统一处理登录凭据、外部绑定、账号状态、角色与权限。主站 Cookie、MCP 令牌和 Widget 站点凭据只在这里解析。站点凭据只用于登记的 Widget 站点范围，且只能访问 Widget 运行和助手目录接口；账号、管理、评测和 MCP 令牌接口只接受平台登录。模块验证用户请求后，按整数 `user_id` 加载账号和当前角色，停用或撤权对下一次受保护请求生效。MCP 发布状态、会话归属与 Widget 站点范围继续作为各自资源的访问条件。

| 公共入口 | 责任 |
| --- | --- |
| `current_account_context(request)` | 每次请求验证一次凭据，返回可信站点范围和请求中已验证的用户账号。宿主转发的 Widget 凭据只用于 Widget 运行和助手目录接口；`current_user` 及其上的账号、管理、评测和 MCP 令牌接口只接受平台登录。 |
| `current_user(request) -> User` | FastAPI 依赖，从同一上下文取当前账号。 |
| `current_user_id(request) -> int` | 复用当前账号并取整数主键，供所有者或操作者写入。 |
| `require_permission(key)` | 校验菜单及管理操作对应的静态角色权限。 |
| `require_agent_access(user_id, agent_id)` | 校验当前账号及助手可见范围。HTTP 用户请求和用户定时、队列任务共用，后台从已持久化的归属取 `user_id`。 |
| `link_or_register(source, subject, profile)` | 接收已验证的外部身份，统一完成查绑、受控匹配或原子注册。 |

移除运行时 `local_admins` 和 `ADMIN_USERS` 的角色赋予、`resolve_oauth_role`、`OAUTH_USER_INFO` 的角色返回、JWT 角色声明、MCP 令牌角色快照及 `remote_mcp_policy` 的角色判断。各路由使用上述依赖，身份头只在账号模块解析。助手目录、详情和斜杠命令统一接收已验证账号，并移除 `_catalog_identity` 按客户端名选择匿名身份的逻辑。续聊和定时消息按会话上的助手 ID 校验当前权限，消息、事件和文件同时核对会话归属。

#### 用户引用盘点

| 位置 | 已核对的用途 | 本次去向 |
| --- | --- | --- |
| `da_agent_topic.auth_user_id`、`auth_username`、`external_user_id`、`visitor_id` | 平台旧主体及名称、宿主 ID 和访客 ID | 新会话以 `user_id BIGINT NULL` 外键归属并联查显示名。手动迁移脚本先为旧会话和已发布名单中出现的每个旧主体（不区分大小写）登记不设密码的普通账号及同一外部身份（即该用户首次登录时会建立的绑定；本人经同一外部身份登录，或由管理员重置为默认密码），再按 `da_user_identity` 映射回填 `user_id`；没有 `auth_user_id` 的行 `user_id` 为空且仅管理员可读；代码只保留 `user_id` 归属路径。旧字段只读保留历史。旧共享会话键仅作为历史保留在 `external_user_id`，不再作为访问范围。 |
| `da_mcp_access_token.user_id`、`display_name`、`oauth_role` | 令牌的字符串持有人及资料、角色快照 | 切换时直接删除旧令牌行（或重建表），再将 `user_id` 改为整数外键，用户重新申请。移除快照并联查用户资料及权限。 |
| 已发布助手 `visibility_json` 与草稿、发布快照中的 `visibility.allowed_users`、`allowed_groups` | 指定范围用户名单和未使用的组预留字段 | 已核实的授权迁入 `da_agent_grant`。配置保留模式，清除旧名单与组字段。 |
| `da_agent_message.feedback`、`feedback_at` 及用户反馈列表 | 反馈在消息行，用户来源联查话题 | 继续从话题取 `user_id` 及显示名。 |
| `da_agent_experience.updated_by`、经验提炼任务的 `system:experience-digest` | 知识和记忆的审核人或系统操作者，提炼会话按系统字符串识别 | 人工审核和写入用 `user_id BIGINT NULL` 外键，旧操作者文本只读保留。后台提炼由内置助手 `agent_experience_digest` 执行，系统记录的 `user_id` 留空。 |
| `da_agent_message_schedule`、`da_agent_message_queue`、`da_agent_message_schedule_log` 及任务记录 | 通过话题或计划关联所有者 | 继续从话题取账号。执行新任务时查账号状态与助手权限。 |
| `da_skill_document_version.actor` | 当前写入 `ui` 或 `system`，用于区分变更来源 | 保留来源标记。 |
| `da_agent_widget_event.external_user_id`、`visitor_id` | 站点行为事件的旧字符串身份 | 使用已验证的 `user_id BIGINT NULL` 外键，旧记录字段只读保留。 |
| `eval_dataset.created_by`、评测运行的认证上下文 | 数据集旧创建人文本，评测通过 HTTP 发起会话 | 新建或导入数据集写 `user_id BIGINT NULL` 外键，旧创建人文本只读保留。评测运行使用登录测试账号，会话归属走统一入口。 |
| 运行器 `X-Agent-User-Id`、管理 API 的用户筛选和响应 | 下游 MCP 审计头、前端过滤及显示 | 代码和 JSON 使用整数 `user_id`，HTTP 请求头在发送边界编码为文本。管理员读取旧记录时仍可展示原身份。 |

盘点来源包括 [会话与反馈](../../dataagent-backend/core/topic_task_store.py#L1329)、[经验库](../../dataagent-backend/core/experience.py#L29)、[定时与队列](../../dataagent-backend/alembic/versions/20260323_000005_add_message_queue_schedule_tables.py#L47)、[技能审计](../../dataagent-backend/core/skill_admin_service.py#L1318)、[Widget 事件](../../dataagent-backend/alembic/versions/20260529_000014_add_widget_event_log.py#L28)、[评测数据集](../../dataagent-backend/alembic/versions/20260721_000021_add_eval_tables.py#L27)。

### 切换影响

- 平台停用认证关闭时的共享池及仅凭宿主 ID 的匿名 Widget 路径。既有「所有人」「登录用户」助手都按「全部用户」对已登录的调用者可见。旧网页会话经手动迁移脚本登记旧主体并按 `da_user_identity` 映射回填 `user_id` 后继续归本人访问，没有旧主体的话题保持 `user_id` 为空且仅管理员可读；代码不再保留按 `auth_user_id` 的归属回退路径。
- OpenDataWorks 个人 Widget 由门户认证后经站点凭据转发。OntoFoundry 的 BFF 核对工作空间权限，并转发已登录用户和站点凭据。DataAgent 不支持共享会话（2026-11-24 用户决定）：每个建模会话只属于创建人，只有创建人能在其中发问，其他成员在 OntoFoundry 内查看；读时对账使用会话创建人的用户 ID。旧共享会话键话题没有 `user_id`，仅管理员可在审计中查看。
- 本地开发、冒烟及评测在访问业务接口前使用专用账号登录，复用登录 Cookie 或 Bearer 会话凭据。存量评测话题保留历史，验证用登录态新话题。
