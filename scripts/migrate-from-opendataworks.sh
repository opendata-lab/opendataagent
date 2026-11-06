#!/usr/bin/env bash
# 从 OpenDataWorks 内置的 DataAgent 部署迁移到本仓库的独立部署（同一台机器）。
#
# 自动完成：
#   1. 把会话库连接（沿用原来的 dataagent 库）、运行时目录写进 deploy/.env（先备份）；
#   2. 复制登录配置 dataagent_config_docker.py / custom_sso_user_info.py（新部署已有的不覆盖）；
#   3. 把旧 Skill 目录里自己导入的 Skill 复制进新的 Skill 卷（跳过镜像自带和需要重新导入的平台 Skill）。
# 不会删除或修改旧部署的任何文件、容器和数据库。需要人工处理的事项在最后列出。
#
# 用法：scripts/migrate-from-opendataworks.sh --old-root <旧 OpenDataWorks 目录> [--mysql-host <IP>] [--dry-run]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_DIR="$REPO_ROOT/deploy"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.yml"
ENV_FILE="$DEPLOY_DIR/.env"
ENV_EXAMPLE="$DEPLOY_DIR/.env.example"
OLD_BACKEND_CONTAINER="opendataworks-dataagent-backend"
# 镜像自带的通用 Skill 以新版为准；平台 Skill 改名为 odw_* 工具后需从 OpenDataWorks 仓库 odw-skills/ 重新导入。
SKIP_BUNDLED="chart-visualization report-generation"
SKIP_PLATFORM_PATTERN='^(opendataworks-.*|ontology-modeling-assistant)$'

# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib/container-runtime.sh"

OLD_ROOT=""
MYSQL_HOST_ARG=""
DRY_RUN=false
HUMAN_TODO=()

usage() {
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --old-root) OLD_ROOT="${2:-}"; shift 2 ;;
        --mysql-host) MYSQL_HOST_ARG="${2:-}"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
    esac
done

die() { echo "❌ $*" >&2; exit 1; }
step() { echo ""; echo "== $*"; }
run() {
    if [ "$DRY_RUN" = true ]; then
        printf '  [dry-run]'; printf ' %q' "$@"; echo
    else
        "$@"
    fi
}

[ -n "$OLD_ROOT" ] || { usage >&2; die "必须提供 --old-root"; }
[ -d "$OLD_ROOT/deploy" ] || die "$OLD_ROOT/deploy 不存在，--old-root 应为旧 OpenDataWorks 仓库根目录"
OLD_ROOT="$(cd "$OLD_ROOT" && pwd)"
OLD_DEPLOY="$OLD_ROOT/deploy"
OLD_ENV="$OLD_DEPLOY/.env"
[ -f "$COMPOSE_FILE" ] || die "未找到 $COMPOSE_FILE"

read_env_value() {
    local key="$1" file="$2" line
    [ -f "$file" ] || return 0
    line="$(grep -E "^${key}=" "$file" | tail -n 1 || true)"
    line="${line#*=}"
    line="${line%\"}"; line="${line#\"}"
    printf '%s' "$line"
}

# 相对路径按旧部署的 deploy/ 解析（与旧 compose 的语义一致）。
resolve_old_path() {
    local path="$1"
    case "$path" in
        /*) ;;
        *) path="$OLD_DEPLOY/$path" ;;
    esac
    if [ -d "$path" ]; then
        (cd "$path" && pwd)
    else
        printf '%s' "$path"
    fi
}

set_env_value() {
    local key="$1" value="$2" current
    current="$(read_env_value "$key" "$ENV_FILE")"
    if grep -qE "^${key}=" "$ENV_FILE" && [ "$current" = "$value" ]; then
        echo "  $key 已是 $value"
        return 0
    fi
    echo "  $key=$value"
    [ "$DRY_RUN" = true ] && return 0
    local tmp
    tmp="$(mktemp)"
    awk -v k="$key" -v v="$value" '
        BEGIN { done = 0 }
        $0 ~ "^" k "=" { if (!done) { print k "=" v; done = 1 }; next }
        { print }
        END { if (!done) print k "=" v }
    ' "$ENV_FILE" > "$tmp"
    cat "$tmp" > "$ENV_FILE"
    rm -f "$tmp"
}

echo "旧部署: $OLD_ROOT"
echo "新部署: $REPO_ROOT"
[ "$DRY_RUN" = true ] && echo "（dry-run：只显示将要做的事，不做任何修改）"
[ -f "$OLD_ENV" ] || echo "⚠️  未找到 ${OLD_ENV}，旧配置按 OpenDataWorks 默认值处理"

# ---------------------------------------------------------------- 1. .env
step "1. 写入 deploy/.env"
if [ ! -f "$ENV_FILE" ]; then
    run cp "$ENV_EXAMPLE" "$ENV_FILE"
    [ "$DRY_RUN" = true ] && ENV_FILE="$ENV_EXAMPLE"
else
    backup="$ENV_FILE.bak.$(date +%Y%m%d%H%M%S)"
    run cp "$ENV_FILE" "$backup"
fi

# 旧 compose 中 DataAgent 连接的是 opendataworks-mysql（宿主机端口 3306），账号来自 DATAAGENT_* 变量。
old_user="$(read_env_value DATAAGENT_MYSQL_USER "$OLD_ENV")"
old_password="$(read_env_value DATAAGENT_MYSQL_PASSWORD "$OLD_ENV")"
old_database="$(read_env_value DATAAGENT_SESSION_MYSQL_DATABASE "$OLD_ENV")"
set_env_value MYSQL_PORT 3306
set_env_value MYSQL_USER "${old_user:-dataagent}"
set_env_value MYSQL_PASSWORD "${old_password:-dataagent123}"
set_env_value MYSQL_DATABASE "${old_database:-dataagent}"
if [ -n "$MYSQL_HOST_ARG" ]; then
    set_env_value MYSQL_HOST "$MYSQL_HOST_ARG"
else
    current_host="$(read_env_value MYSQL_HOST "$ENV_FILE")"
    case "$current_host" in
        ""|127.0.0.1|localhost)
            HUMAN_TODO+=("在 deploy/.env 把 MYSQL_HOST 改成这台机器的 IP（当前为 '${current_host}'，指向的是 backend 容器自己），或带 --mysql-host 重新运行本脚本。")
            echo "  MYSQL_HOST 未改动（当前 '${current_host}'），见最后的人工事项" ;;
        *) echo "  MYSQL_HOST 保持 $current_host" ;;
    esac
fi

old_host_root="$(read_env_value DATAAGENT_HOST_ROOT "$OLD_ENV")"
old_host_root="$(resolve_old_path "${old_host_root:-/dataagent_runtime}")"
if [ -d "$old_host_root" ]; then
    set_env_value DATAAGENT_HOST_ROOT "$old_host_root"
else
    echo "  旧运行时目录 $old_host_root 不存在，DATAAGENT_HOST_ROOT 不改"
fi
for key in DATAAGENT_RUNTIME_UID DATAAGENT_RUNTIME_GID DATAAGENT_DOCKER_SOCKET; do
    value="$(read_env_value "$key" "$OLD_ENV")"
    [ -n "$value" ] && set_env_value "$key" "$value"
done

# ---------------------------------------------------------------- 2. 登录配置
step "2. 复制登录配置"
for name in dataagent_config_docker.py custom_sso_user_info.py; do
    src="$OLD_DEPLOY/docker/dataagent/$name"
    dst="$DEPLOY_DIR/docker/dataagent/$name"
    if [ ! -f "$src" ]; then
        echo "  旧部署没有 ${name}，跳过"
    elif [ -f "$dst" ]; then
        if cmp -s "$src" "$dst"; then
            echo "  $name 已相同"
        else
            echo "  新部署已有不同的 ${name}，未覆盖"
            HUMAN_TODO+=("对比并决定是否用旧的 $src 覆盖 ${dst}。")
        fi
    else
        run cp -p "$src" "$dst"
        echo "  已复制 $name"
    fi
done

# ---------------------------------------------------------------- 3. Skill
step "3. 复制 Skill 到新 Skill 卷"
old_skills=""
if command -v docker >/dev/null 2>&1 && docker inspect "$OLD_BACKEND_CONTAINER" >/dev/null 2>&1; then
    old_skills="$(docker inspect "$OLD_BACKEND_CONTAINER" \
        --format '{{range .Mounts}}{{if eq .Destination "/app/.claude/skills"}}{{.Source}}{{end}}{{end}}')"
    [ -n "$old_skills" ] && echo "  旧容器 $OLD_BACKEND_CONTAINER 的 Skill 挂载: $old_skills"
fi
if [ -z "$old_skills" ]; then
    configured="$(read_env_value DATAAGENT_SKILLS_DIR "$OLD_ENV")"
    old_skills="$(resolve_old_path "${configured:-../dataagent/.claude/skills}")"
    echo "  按旧 .env 的 DATAAGENT_SKILLS_DIR 解析: $old_skills"
fi
[ -d "$old_skills" ] || die "Skill 目录 $old_skills 不存在；确认旧部署位置后重试"

copy_list=()
for dir in "$old_skills"/*/; do
    [ -d "$dir" ] || continue
    name="$(basename "$dir")"
    if [[ " $SKIP_BUNDLED " == *" $name "* ]]; then
        echo "  跳过 ${name}（使用镜像自带的新版）"
    elif [[ "$name" =~ $SKIP_PLATFORM_PATTERN ]]; then
        echo "  跳过 ${name}（平台 Skill，需要重新导入）"
        HUMAN_TODO+=("从 OpenDataWorks 仓库 odw-skills/ 重新导入平台 Skill（旧的 $name 已跳过）。")
    elif [ ! -f "$dir/SKILL.md" ]; then
        echo "  跳过 ${name}（没有 SKILL.md）"
    else
        copy_list+=("$name")
    fi
done

if [ "${#copy_list[@]}" -eq 0 ]; then
    echo "  没有需要复制的 Skill"
else
    echo "  将复制: ${copy_list[*]}"
    detect_compose_cmd || die "未找到可用的 compose 命令"
    uid="$(read_env_value DATAAGENT_RUNTIME_UID "$ENV_FILE")"
    gid="$(read_env_value DATAAGENT_RUNTIME_GID "$ENV_FILE")"
    # 用本部署的 backend 镜像写命名卷（首次挂载时带入镜像里的通用 Skill），离线环境不需要额外镜像。
    run "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" run --rm --no-deps -T \
        -v "$old_skills:/from:ro" --entrypoint sh dataagent-backend -c '
            set -e
            for name in "$@"; do cp -a "/from/$name" /app/.claude/skills/; echo "  已复制 $name"; done
            chown -R "'"${uid:-1000}:${gid:-1000}"'" /app/.claude/skills' \
        sh "${copy_list[@]}"
fi

# ---------------------------------------------------------------- 人工事项
HUMAN_TODO+=(
    "确认 MySQL 上 da_agent_settings 里保存的 mysql_host/mysql_port 与 deploy/.env 一致或已清空（旧版本启动时会用它覆盖 .env，表现为 1049 Unknown database；新版本已修复，见 PR #14）。"
    "停掉旧容器：docker stop opendataworks-dataagent-frontend opendataworks-dataagent-backend opendataworks-dataagent-sandbox-runner"
    "启动新部署：scripts/start.sh，然后检查 docker logs dataagent-backend 无报错、技能管理页能看到复制的 Skill。"
    "在 MCP 管理页把平台 MCP 地址改为 http://<OpenDataWorks 主机 IP>:8080/api/mcp/，删除 X-ODW-MCP-Token 头，改用 Authorization: Bearer <服务账号 Token>（OpenDataWorks「管理中心 → 账号管理」生成），保存后刷新工具列表。"
    "检查智能体提示词中的工具名是否为 odw_*。"
    "新部署运行正常一段时间后，再自行备份并清理旧部署的目录。"
)

step "需要人工处理"
i=1
for item in "${HUMAN_TODO[@]}"; do
    echo "  $i. $item"
    i=$((i + 1))
done
[ -n "${backup:-}" ] && [ "$DRY_RUN" = false ] && echo "" && echo "原 deploy/.env 已备份为 $backup"
exit 0
