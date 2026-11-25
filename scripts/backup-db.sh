#!/bin/bash

# DataAgent 备份脚本
# 功能：备份 DataAgent 数据库和部署配置，升级前必须先执行。
# 用法：scripts/backup-db.sh [--output-dir <目录>]（默认 deploy/backups）
# 结果：<目录>/dataagent-<时间>.sql.gz（数据库）和 <目录>/config-<时间>.tar.gz（deploy/.env 与 deploy/docker/dataagent/*.py）
# 数据库连接沿用 deploy/.env，mysqldump 在 dataagent-backend 镜像内执行，宿主机无需安装 MySQL 客户端。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_DIR="$REPO_ROOT/deploy"
ENV_FILE="$DEPLOY_DIR/.env"
OUTPUT_DIR="$DEPLOY_DIR/backups"

# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib/container-runtime.sh"

while [ $# -gt 0 ]; do
    case "$1" in
        --output-dir) OUTPUT_DIR="${2:?--output-dir 需要目录}"; shift 2 ;;
        -h|--help) sed -n '3,6p' "$0"; exit 0 ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

detect_compose_cmd || { echo "❌ 未找到可用的 compose 命令"; exit 1; }
ENV_FLAG_ARGS=()
if [ "$COMPOSE_SUPPORTS_ENV_FILE" = true ] && [ -f "$ENV_FILE" ]; then
    ENV_FLAG_ARGS=(--env-file "$ENV_FILE")
fi

STAMP="$(date '+%Y%m%d-%H%M%S')"
mkdir -p "$OUTPUT_DIR"
DB_FILE="$OUTPUT_DIR/dataagent-$STAMP.sql.gz"
CONFIG_FILE="$OUTPUT_DIR/config-$STAMP.tar.gz"

echo "📦 备份数据库到 $DB_FILE"
pushd "$DEPLOY_DIR" >/dev/null
# --single-transaction: consistent InnoDB snapshot without locking writers.
# --no-tablespaces: the dataagent user lacks the PROCESS privilege MySQL 8 wants for tablespaces.
"${COMPOSE_CMD[@]}" -f docker-compose.yml "${ENV_FLAG_ARGS[@]}" run --rm -T --no-deps --entrypoint sh dataagent-backend -c \
    'MYSQL_PWD="$MYSQL_PASSWORD" mysqldump -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" \
        --single-transaction --no-tablespaces --routines --triggers "$SESSION_MYSQL_DATABASE"' \
    | gzip > "$DB_FILE"
popd >/dev/null
if ! gzip -dc "$DB_FILE" | tail -n 1 | grep -q "Dump completed"; then
    echo "❌ 数据库备份不完整，请检查上面的错误：$DB_FILE"
    exit 1
fi

echo "📦 备份配置到 $CONFIG_FILE"
CONFIG_ITEMS=()
[ -f "$ENV_FILE" ] && CONFIG_ITEMS+=(".env")
for f in "$DEPLOY_DIR"/docker/dataagent/*.py; do
    [ -f "$f" ] && CONFIG_ITEMS+=("docker/dataagent/$(basename "$f")")
done
tar -C "$DEPLOY_DIR" -czf "$CONFIG_FILE" "${CONFIG_ITEMS[@]}"
chmod 600 "$DB_FILE" "$CONFIG_FILE"

echo "✅ 备份完成"
echo "   数据库：$DB_FILE"
echo "   配置：  $CONFIG_FILE"
echo "   恢复：scripts/restore-db.sh $DB_FILE"
