#!/usr/bin/env bash
# lumina server —— 从本地一键看服务器上的下载/ASR/翻译进度与日志（免记 IP/端口）。
# 服务器连接走本地 .env（gitignore，不入库）：
#   ASR_SERVER=user@host   ASR_SERVER_PORT=<ssh端口>   ASR_SERVER_DATA=/远端/data
#
# 用法：
#   lumina server            状态快照（会话/计数/GPU/近期完成）
#   lumina server asr        跟踪 ASR 日志（tail -f）
#   lumina server trans      跟踪翻译日志
#   lumina server dl         跟踪下载日志
#   lumina server attach     进 tmux 看实时（asr/trans/dl 三选一提示）
set -euo pipefail
cd "$(dirname "$0")/.."
# 只精确取这几个变量，不整体 source .env（.env 里可能有 bash 不认的值）
getenv(){ grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"'"'; }
SRV="${ASR_SERVER:-$(getenv ASR_SERVER)}"
PORT="${ASR_SERVER_PORT:-$(getenv ASR_SERVER_PORT)}"; PORT="${PORT:-22}"
DATA="${ASR_SERVER_DATA:-$(getenv ASR_SERVER_DATA)}"; DATA="${DATA:-REMOTE_DATA}"
[ -n "$SRV" ] || { echo "请在 .env 设 ASR_SERVER=user@host"; exit 1; }
ROOT="$(dirname "$DATA")"
SSH=(ssh -p "$PORT" -o ConnectTimeout=20 -o ServerAliveInterval=10 "$SRV")

sub="${1:-status}"
case "$sub" in
  asr)   exec "${SSH[@]}" "tail -f $ROOT/asr.log" ;;
  trans) exec "${SSH[@]}" "tail -f $ROOT/trans.log" ;;
  dl)    exec "${SSH[@]}" "tail -f $ROOT/dl.log" ;;
  attach) exec "${SSH[@]}" -t "tmux attach -t ${2:-luminaasr}" ;;
esac

"${SSH[@]}" "
  echo '── lumina 服务器状态 ──'
  echo '会话:'; tmux ls 2>/dev/null | grep lumina | sed 's/^/  /' || echo '  (无 lumina 会话)'
  mp3=\$(find $DATA -name '*.mp3' 2>/dev/null | wc -l)
  asr=\$(find $DATA -name '*.asr.json' 2>/dev/null | wc -l)
  zh=\$(find $DATA -name '*.zh.json' 2>/dev/null | wc -l)
  echo \"进度: 下载 \$mp3 mp3 · 转写 \$asr · 翻译 \$zh  (待转 \$((mp3-asr)) · 待译 \$((asr-zh)))\"
  echo 'GPU3:'; nvidia-smi --query-gpu=utilization.gpu,power.draw,memory.used,temperature.gpu --format=csv,noheader 2>/dev/null | sed -n '4p' | sed 's/^/  /'
  echo '下载近况:'; grep -E '完成|待下载' $ROOT/dl.log 2>/dev/null | tail -2 | sed 's/^/  /'
  echo 'ASR 近况:'; grep '✓' $ROOT/asr.log 2>/dev/null | tail -2 | sed 's/^/  /'
  echo '翻译近况:'; grep '✓' $ROOT/trans.log 2>/dev/null | tail -2 | sed 's/^/  /'
  echo '磁盘:'; df -h $DATA 2>/dev/null | tail -1 | awk '{print \"  剩 \"\$4\" (\"\$5\" 已用)\"}'
"
