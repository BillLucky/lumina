#!/usr/bin/env bash
# 月度增量刷新：抓取 → 翻译 → 制书。
# 全程增量、复用已有资产：
#   - 已抓且内容未变的文章不重复入库
#   - 已翻译且原文未变的文章不重复翻译（只译新增/更新的）
#   - 每次重建电子书（成本低），自然包含新文章
#
# 用法：bash scripts/refresh.sh
set -euo pipefail
cd "$(dirname "$0")/.."

export PYTHONPATH=src
PY=.venv/bin/python

echo "==> [1/4] 确保 MySQL 运行"
docker compose up -d >/dev/null

echo "==> [2/5] 抓取（增量，所有文本来源）"
$PY -m scrape.scrape_paulgraham
$PY -m scrape.scrape_naval
$PY -m scrape.scrape_pmarca
$PY -m scrape.scrape_michaelseibel
$PY -m scrape.scrape_startupmarketing
# 播客（下载+本地ASR，较慢；如无需更新可注释掉）
$PY -m scrape.scrape_a16z

echo "==> [3/5] 翻译新增/更新文章（英→中）"
$PY -m translate.translate --source all

echo "==> [4/5] 生成核心导读（思维导图数据）"
$PY -m translate.summarize --source all

echo "==> [5/5] 重建全部书籍（封面+导读+全格式）"
$PY -m book.build_book --all --formats epub,azw3,mobi,pdf

echo "==> 导出数据库备份"
bash scripts/export_db.sh

echo "==> 完成。成品见 output/"
