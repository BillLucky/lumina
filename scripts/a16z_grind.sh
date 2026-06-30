#!/usr/bin/env bash
# a16z 全系列播客 → 双语书 的长跑驱动脚本（可中断重跑，全增量）。
#
# 两阶段：
#   Phase 0  并行下载全部系列音频到本地（secure 原始文件，~tens of GB，按 .env 限并发）
#   Phase 1  逐系列 ASR→翻译(M3)→导读→出书（小→大，a16z Show 取近期 150 集）
#
# ASR 是单卡串行瓶颈（全量约 5-6 天）；下载并行很快。中断后重跑：已下载/已转写的自动跳过。
#
# 用法：  bash scripts/a16z_grind.sh        （建议放后台：日志见 logs/a16z_grind.log）
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=.venv/bin/python
ORDER=(a16z_hotline a16z_benmarc a16z_16min a16z_ai a16z_crypto a16z_live a16z_raising_health a16z)

echo "==================== a16z grind 开始 $(date) ===================="

echo "########## Phase 0：并行下载全部系列音频 ##########"
$PY -u -m scrape.scrape_podcast --all --download-only --workers 6

echo "########## Phase 1：逐系列 ASR → 翻译 → 导读 → 出书 ##########"
for KEY in "${ORDER[@]}"; do
  echo "==================== [$KEY] $(date) ===================="
  echo "--- ($KEY) ASR 转写 ---"
  $PY -u -m scrape.scrape_podcast --series "$KEY"          || { echo "[$KEY] scrape 失败，跳过"; continue; }
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
