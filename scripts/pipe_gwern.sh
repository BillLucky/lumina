#!/usr/bin/env bash
# Gwern 独立管道（第 10 个信息源）：抓取 → 翻译 → 导读 → 出书。
# 单独成线，避免打扰其它正在跑的 grind；翻译为单流，叠加在现有 M3 负载上（backoff 自调）。
#
# 用法：  bash scripts/pipe_gwern.sh   （建议后台：日志 logs/pipe_gwern.log）
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=.venv/bin/python
echo "==================== gwern 管道 开始 $(date) ===================="
$PY -u -m scrape.scrape_gwern
$PY -u -m translate.translate --source gwern
$PY -u -m translate.summarize --source gwern
$PY -u -m book.build_book --source gwern --lang en --formats epub,pdf,azw3,mobi
$PY -u -m book.build_book --source gwern --lang zh --formats epub,pdf,azw3,mobi
echo "==================== gwern 管道 完成 $(date) ===================="
