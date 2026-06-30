#!/usr/bin/env bash
# 文本类信息源（博客）→ 双语书 的驱动脚本。不吃 ASR，可与 a16z grind 并行。
#
# Phase 0  5 个站点并行抓取（不同站点互不施压，受限于本地带宽与各站 PoliteFetcher 限速）
# Phase 1  逐源 翻译(M3)→导读→出书，小→大，快速出书
#
# 用法：  bash scripts/text_grind.sh   （建议后台：日志 logs/text_grind.log）
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=.venv/bin/python

echo "==================== text grind 开始 $(date) ===================="

echo "########## Phase 0：5 站并行抓取 ##########"
$PY -u -m scrape.scrape_cdixon        > logs/scrape_cdixon.log 2>&1 &
$PY -u -m scrape.scrape_samaltman     > logs/scrape_samaltman.log 2>&1 &
$PY -u -m scrape.scrape_abovethecrowd > logs/scrape_abovethecrowd.log 2>&1 &
$PY -u -m scrape.scrape_farnamstreet  > logs/scrape_farnamstreet.log 2>&1 &
$PY -u -m scrape.scrape_avc           > logs/scrape_avc.log 2>&1 &
wait
echo "  抓取完成。各站文章数："
$PY -c "
from common import db
with db.cursor() as c:
    c.execute(\"SELECT s.source_key,COUNT(a.id) n FROM sources s LEFT JOIN articles a ON a.source_id=s.id WHERE s.source_key IN ('cdixon','samaltman','abovethecrowd','farnamstreet','avc') GROUP BY s.id\")
    [print('   ',r) for r in c.fetchall()]
"

echo "########## Phase 1：逐源 翻译→导读→出书（小→大）##########"
for KEY in cdixon samaltman abovethecrowd farnamstreet avc; do
  echo "==================== [$KEY] $(date) ===================="
  $PY -u -m translate.translate --source "$KEY"
  $PY -u -m translate.summarize --source "$KEY"
  $PY -u -m book.build_book --source "$KEY" --lang en --formats epub,pdf,azw3,mobi
  $PY -u -m book.build_book --source "$KEY" --lang zh --formats epub,pdf,azw3,mobi
  echo "==================== [$KEY] 完成 $(date) ===================="
done

echo "==================== text grind 全部完成 $(date) ===================="
