#!/bin/bash

# DataAgent 启动脚本
# 功能：启动所有服务

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_DIR="$REPO_ROOT/deploy"
LIB_DIR="$SCRIPT_DIR/lib"
COMPOSE_FILE_NAME="docker-compose.yml"
COMPOSE_FILE="$DEPLOY_DIR/$COMPOSE_FILE_NAME"
ENV_FILE="$DEPLOY_DIR/.env"
ENV_EXAMPLE="$DEPLOY_DIR/.env.example"

# shellcheck source=/dev/null
source "$LIB_DIR/container-runtime.sh"

read_env_value() {
    local key="$1"
    local env_file="$2"

    if [ ! -f "$env_file" ]; then
        return 0
    fi

    local line
    line="$(grep -E "^${key}=" "$env_file" | tail -n 1 || true)"
    printf '%s' "${line#*=}"
}

normalize_path() {
    local path="$1"
    local is_absolute=false
    local -a parts=()
    local -a normalized=()
    local part
    local joined=""
    local i

    if [[ "$path" = /* ]]; then
        is_absolute=true
        path="${path#/}"
    fi

    IFS='/' read -r -a parts <<< "$path"
    for part in "${parts[@]}"; do
        case "$part" in
            ""|".")
                continue
                ;;
            "..")
                if [ "${#normalized[@]}" -gt 0 ]; then
                    unset 'normalized[${#normalized[@]}-1]'
                fi
                ;;
            *)
                normalized+=("$part")
                ;;
        esac
    done

    if [ "${#normalized[@]}" -gt 0 ]; then
        joined="${normalized[0]}"
        for ((i = 1; i < ${#normalized[@]}; i++)); do
            joined+="/${normalized[i]}"
        done
    fi

    if [ "$is_absolute" = true ]; then
        printf '/%s\n' "$joined"
        return 0
    fi

    if [ -n "$joined" ]; then
        printf '%s\n' "$joined"
    else
        printf '.\n'
    fi
}

resolve_dataagent_host_root() {
    # 解析 DataAgent 持久化运行时根目录的宿主机绝对路径。
    #
    # 在部署目录解析成绝对路径；backend 从自己的 Docker mount 映射
    # 找到 daemon 可见的 workspace/results/skills 来源。
    local configured
    configured="$(read_env_value "DATAAGENT_HOST_ROOT" "$ENV_FILE")"
    if [ -z "$configured" ]; then
        configured="/dataagent_runtime"
    fi

    if [[ "$configured" = /* ]]; then
        normalize_path "$configured"
        return 0
    fi

    normalize_path "$DEPLOY_DIR/$configured"
}

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "❌ 错误: 未找到 $COMPOSE_FILE"
    exit 1
fi

echo "========================================="
echo "  DataAgent 启动脚本"
echo "========================================="
echo ""

# 检查 .env 文件
if [ ! -f "$ENV_FILE" ]; then
    if [ ! -f "$ENV_EXAMPLE" ]; then
        echo "❌ 错误: 未找到 $ENV_EXAMPLE"
        exit 1
    fi

    echo "⚠️  警告: 未找到部署配置文件 $ENV_FILE"
    echo "正在从模板复制..."
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    echo "✅ 已创建 $ENV_FILE"
    echo ""
    echo "⚠️  请编辑 $ENV_FILE，确认关键配置："
    echo "   - MYSQL_HOST / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DATABASE 数据库连接"
    echo "   - DATAAGENT_HOST_ROOT 运行时目录"
    echo "   - 登录配置见 deploy/docker/dataagent/dataagent_config_docker.py.example"
    echo ""
    read -p "是否继续启动？(y/n) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "已取消启动"
        exit 0
    fi
fi

if ! detect_compose_cmd; then
    echo "❌ 错误: 未找到可用的 compose 命令（docker-compose、docker compose、podman compose、podman-compose）"
    exit 1
fi

# 检查容器运行时是否可用
if ! ensure_container_runtime_ready "$COMPOSE_RUNTIME"; then
    if [ "$COMPOSE_RUNTIME" = "docker" ]; then
        echo "❌ 错误: Docker 未运行，请先启动 Docker"
    else
        echo "❌ 错误: Podman 不可用，请先启动 Podman 或检查当前用户权限"
    fi
    exit 1
fi

echo "🚀 启动 DataAgent 服务..."
echo ""

# 预建日志目录（scripts/dump-logs.sh 把各服务日志导出到这里）。
# 先建好宿主机目录，避免 Docker 以 root 自动创建后产生属主困惑。
mkdir -p "$DEPLOY_DIR/logs" 2>/dev/null || true

# 统一 DataAgent 运行时根目录为宿主机绝对路径，保证 backend 卷挂载与 daemon 使用的
# child bind 源指向同一目录，支持用户在 .env 中自定义（含相对于 deploy/ 的相对路径）。
DATAAGENT_HOST_ROOT="$(resolve_dataagent_host_root)"
export DATAAGENT_HOST_ROOT
echo "📁 DataAgent 运行时根目录(宿主机): $DATAAGENT_HOST_ROOT"

# 启动服务
pushd "$DEPLOY_DIR" >/dev/null
if [ "$COMPOSE_SUPPORTS_ENV_FILE" = true ]; then
    "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE_NAME" --env-file "$ENV_FILE" up -d
else
    "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE_NAME" up -d
fi

echo ""
echo "⏳ 等待服务启动..."
sleep 5

echo ""
echo "========================================="
echo "  服务状态检查"
echo "========================================="
echo ""

# 显示服务状态
if [ "$COMPOSE_SUPPORTS_ENV_FILE" = true ]; then
    "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE_NAME" --env-file "$ENV_FILE" ps
else
    "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE_NAME" ps
fi
popd >/dev/null

ENV_FLAG_TEXT=""
if [ "$COMPOSE_SUPPORTS_ENV_FILE" = true ]; then
    ENV_FLAG_TEXT=" --env-file $ENV_FILE"
fi

echo ""
echo "========================================="
echo "  DataAgent 启动完成！"
echo "========================================="
echo ""
echo "📝 服务访问地址："
echo "  前端: http://localhost:8901/chat"
echo "  后端: http://localhost:8900"
echo ""
echo "📋 常用命令："
echo "  查看日志: ${COMPOSE_CMD[*]} -f $COMPOSE_FILE$ENV_FLAG_TEXT logs -f [service_name]"
echo "  导出日志: scripts/dump-logs.sh"
echo "  停止服务: scripts/stop.sh"
echo "  重启服务: scripts/restart.sh"
echo ""
