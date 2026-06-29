#!/usr/bin/env bash
# 把 blogbook 全量数据（结构 + 数据，含原始网页/译文/审计）导出为压缩 SQL，
# 备份进 db_backup/，方便换机或重置后用 import_db.sh 一键恢复。
#
# 用法：bash scripts/export_db.sh
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p db_backup
OUT="db_backup/blogbook.sql.gz"

echo "==> 从容器 blogbook-mysql 导出 blogbook ..."
docker exec blogbook-mysql sh -c \
  'exec mysqldump -uroot -prootpass --databases blogbook \
     --single-transaction --default-character-set=utf8mb4 \
     --add-drop-table --routines --events 2>/dev/null' \
  | gzip > "$OUT"

echo "==> 完成：$OUT （$(du -h "$OUT" | cut -f1)）"
