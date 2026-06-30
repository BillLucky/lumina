#!/usr/bin/env bash
# a16z 全系列播客 → 双语书 的长跑驱动脚本（可中断重跑，全增量）。
#
# 省流量模式：**不再下载新音频**（--no-download），只把**已下载到本地**的音频
# ASR→翻译→导读→出书。未下载的剧集留待将来有流量时增量补齐。
# （之前的 Phase 0 全量并行下载已移除，避免吃移动热点流量。）
#
# ASR 是单卡串行瓶颈；中断后重跑：已转写的自动跳过。
#
# 用法：  bash scripts/a16z_grind.sh        （建议放后台：日志见 logs/a16z_grind.log）
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=.venv/bin/python
ORDER=(a16z_hotline a16z_benmarc a16z_16min a16z_ai a16z_crypto a16z_live a16z_raising_health a16z)

echo "==================== a16z grind 开始 $(date) ===================="

echo "########## 逐系列 ASR → 翻译 → 导读 → 出书（仅处理已下载，省流量）##########"
for KEY in "${ORDER[@]}"; do
  echo "==================== [$KEY] $(date) ===================="
  echo "--- ($KEY) ASR 转写（--no-download：只处理已下载，省流量）---"
  $PY -u -m scrape.scrape_podcast --series "$KEY" --no-download || { echo "[$KEY] scrape 失败，跳过"; continue; }
  echo "--- ($KEY) 翻译 ---"
  $PY -u -m translate.translate --source "$KEY"
  echo "--- ($KEY) 导读/思维导图 ---"
  $PY -u -m translate.summarize --source "$KEY"
  echo "--- ($KEY) 出书 en+zh (epub,pdf,azw3,mobi) ---"
  $PY -u -m book.build_book --source "$KEY" --lang en --formats epub,pdf,azw3,mobi
  $PY -u -m book.build_book --source "$KEY" --lang zh --formats epub,pdf,azw3,mobi
  echo "==================== [$KEY] 完成 $(date) ===================="
done

echo "==================== a16z grind 全部完成 $(date) ===================="
