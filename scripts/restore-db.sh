#!/bin/bash

# DataAgent 恢复脚本
# 功能：用 backup-db.sh 生成的备份覆盖 DataAgent 数据库（先删除库内全部表再导入）。
# 用法：scripts/restore-db.sh <dataagent-时间.sql.gz> [--yes]
# 回退升级时：先恢复升级前的数据库，再把 deploy/.env 的 ODA_*_IMAGE 改回旧版本镜像并启动；
#             配置文件需要时从同一时间的 config-时间.tar.gz 解压回 deploy/。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_DIR="$REPO_ROOT/deploy"
ENV_FILE="$DEPLOY_DIR/.env"

# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib/container-runtime.sh"

BACKUP_FILE=""
ASSUME_YES=false
while [ $# -gt 0 ]; do
    case "$1" in
        --yes) ASSUME_YES=true; shift ;;
        -h|--help) sed -n '3,7p' "$0"; exit 0 ;;
        *) BACKUP_FILE="$1"; shift ;;
    esac
done
[ -n "$BACKUP_FILE" ] && [ -f "$BACKUP_FILE" ] || { echo "❌ 请指定存在的备份文件（*.sql.gz）"; exit 1; }
BACKUP_FILE="$(cd "$(dirname "$BACKUP_FILE")" && pwd)/$(basename "$BACKUP_FILE")"
gzip -dc "$BACKUP_FILE" | tail -n 1 | grep -q "Dump completed" || { echo "❌ 备份文件不完整：$BACKUP_FILE"; exit 1; }

detect_compose_cmd || { echo "❌ 未找到可用的 compose 命令"; exit 1; }
ENV_FLAG_ARGS=()
if [ "$COMPOSE_SUPPORTS_ENV_FILE" = true ] && [ -f "$ENV_FILE" ]; then
    ENV_FLAG_ARGS=(--env-file "$ENV_FILE")
fi
compose() { "${COMPOSE_CMD[@]}" -f docker-compose.yml "${ENV_FLAG_ARGS[@]}" "$@"; }

if [ "$ASSUME_YES" != true ]; then
    echo "⚠️  将删除 DataAgent 数据库中的全部表，并用 $BACKUP_FILE 覆盖。"
    read -r -p "确认请输入 restore：" answer
    [ "$answer" = "restore" ] || { echo "已取消"; exit 1; }
fi

pushd "$DEPLOY_DIR" >/dev/null
echo "🛑 停止 dataagent-backend"
compose stop dataagent-backend

echo "♻️  清空并导入数据库"
gzip -dc "$BACKUP_FILE" | compose run --rm -T --no-deps --entrypoint sh dataagent-backend -c '
    set -e
    export MYSQL_PWD="$MYSQL_PASSWORD"
    conn="-h $MYSQL_HOST -P $MYSQL_PORT -u $MYSQL_USER"
    drops=$(mysql $conn -N -e "SELECT CONCAT(\"DROP TABLE IF EXISTS \`\", table_name, \"\`;\") FROM information_schema.tables WHERE table_schema = \"$SESSION_MYSQL_DATABASE\"")
    { echo "SET FOREIGN_KEY_CHECKS=0;"; echo "$drops"; cat; echo "SET FOREIGN_KEY_CHECKS=1;"; } | mysql $conn "$SESSION_MYSQL_DATABASE"
'
popd >/dev/null

echo "✅ 恢复完成。确认 deploy/.env 中的镜像版本与该备份匹配后，执行 scripts/start.sh 启动。"
