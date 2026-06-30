#!/usr/bin/env bash
# 晚上有流量时，一键补下载**剩余未下载的播客音频**（约 3GB，主要是 a16z Show 缺的 ~85 集）。
# 已下载的自动跳过（断点续传），只补缺口。下完后 a16z_grind（--no-download）会自动把它们 ASR→出书。
#
# 用法：  bash scripts/download_remaining_audio.sh
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=.venv/bin/python
echo "==================== 补下载剩余音频 $(date) ===================="
# 只补 a16z Show（其余系列已完整）；如将来某系列有新集，也可改这里逐个补
$PY -u -m scrape.scrape_podcast --series a16z --download-only --workers 8
echo "==================== 补下载完成 $(date) ===================="
echo "提示：已下载的会被 a16z_grind 自动 ASR→翻译→出书（无需再操作）。"
