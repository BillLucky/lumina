#!/usr/bin/env bash
# 服务器端看门狗：保活 5 个 lumina 服务（下载/ASR/播客翻译/文本翻译/飞书通知）。
# 每 2 分钟自检，tmux 会话掉了就按对应命令重拉。所有路径走 .env(gitignore)，脚本零硬编码。
#
# 需要的 .env 变量：ASR_PY(解释器) ASR_MODEL(Qwen3-ASR权重) ASR_DATA(数据根) GPU(默认3)
#   ANTHROPIC_*/TRANSLATE_*/FEISHU_WEBHOOK(翻译/通知用)
# 启动脚本从 ASR_DATA 的上一级目录(工作区)找 download_podcasts.py 等；本 watchdog 也放那。
#
# 用法（tmux 内常驻）：ASR_DATA=/path/data bash server_watchdog.sh
set -uo pipefail
cd "$(dirname "$0")"
set -a; [ -f .env ] && . ./.env; set +a
PY="${ASR_PY:-python3}"
DATA="${ASR_DATA:?请在 .env 设 ASR_DATA}"
ROOT="$(pwd)"
GPU="${GPU:-3}"

guard(){ # $1=session  $2=launch-cmd
  tmux has-session -t "$1" 2>/dev/null || {
    tmux new-session -d -s "$1" "$2"
    echo "$(date '+%F %T') 重拉 $1" >> "$ROOT/watchdog.log"
  }
}

echo "$(date '+%F %T') ===== server watchdog 启动 =====" >> "$ROOT/watchdog.log"
while true; do
  guard luminadl     "ASR_DATA=$DATA $PY $ROOT/download_podcasts.py --workers 5 2>&1 | tee -a $ROOT/dl.log"
  guard luminaasr    "ASR_PY=$PY ASR_MODEL=$ASR_MODEL ASR_DATA=$DATA GPU=$GPU bash $ROOT/run_asr.sh 2>&1 | tee -a $ROOT/asr.log"
  guard luminatrans  "set -a;. $ROOT/.env;set +a; ASR_DATA=$DATA $PY $ROOT/translate_asr.py 2>&1 | tee -a $ROOT/trans.log"
  guard luminajobs   "set -a;. $ROOT/.env;set +a; ASR_DATA=$DATA $PY $ROOT/translate_jobs.py 2>&1 | tee -a $ROOT/jobs.log"
  guard luminafeishu "set -a;. $ROOT/.env;set +a; ASR_DATA=$DATA $PY $ROOT/feishu_report.py --interval 3600 2>&1 | tee -a $ROOT/feishu.log"
  sleep 120
done
