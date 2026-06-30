#!/usr/bin/env bash
# 文本类信息源（博客）→ 双语书 驱动（管道版）。不吃 ASR，与 a16z grind 并行。
#
# 抓完一批做一批：5 站已抓完，这里逐源 翻译(M3)→导读→出书（增量，已完成的秒跳过）。
# 注：avc（8909 篇，最大）由 scripts/pipe_avc.sh **独立并行**处理，不在此队列，避免串行干等与撞车。
#
# 用法：  bash scripts/text_grind.sh   （建议后台：日志 logs/text_grind.log）
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=.venv/bin/python

echo "==================== text grind 开始 $(date) ===================="
for KEY in cdixon samaltman abovethecrowd farnamstreet; do
  echo "==================== [$KEY] $(date) ===================="
  $PY -u -m translate.translate --source "$KEY"
  $PY -u -m translate.summarize --source "$KEY"
  $PY -u -m book.build_book --source "$KEY" --lang en --formats epub,pdf,azw3,mobi
  $PY -u -m book.build_book --source "$KEY" --lang zh --formats epub,pdf,azw3,mobi
  echo "==================== [$KEY] 完成 $(date) ===================="
done
echo "==================== text grind 全部完成 $(date) ===================="
