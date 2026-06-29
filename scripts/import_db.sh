#!/usr/bin/env bash
# 从 db_backup/blogbook.sql.gz 恢复数据库（换机 / 重置后）。
# 前提：docker compose up -d 已把 blogbook-mysql 跑起来。
#
# 用法：bash scripts/import_db.sh
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="db_backup/blogbook.sql.gz"
[ -f "$SRC" ] || { echo "找不到 $SRC"; exit 1; }

echo "==> 等待 MySQL 就绪 ..."
until docker exec blogbook-mysql mysqladmin ping -uroot -prootpass --silent 2>/dev/null; do
  sleep 2
done

echo "==> 导入 $SRC ..."
gunzip -c "$SRC" | docker exec -i blogbook-mysql sh -c \
  'exec mysql -uroot -prootpass --default-character-set=utf8mb4'

echo "==> 完成。"
