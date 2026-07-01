#!/usr/bin/env bash
# 收尾管道：把「文本源里 failed/未译」的残篇重跑一遍 → 补导读 → 重制书。
# 触发背景：各文本 lane 首轮跑完后留下一批 failed（多为 M3 那段时间的 HTTP 500/SSL 瞬时故障，
# 外加已修的 content=null → NoneType、标题超长 → 1406 两个真 bug）。status='failed' 会被
# translate 当 pending 重新捡起，故本脚本只需按源重跑即可，纯翻译/制书、不再抓取、不占流量。
# 断点续传：已 done 的不会重译；本脚本可反复运行。
#
# 顺序：先出快的（eladgil/firstround/farnamstreet/danluu/feld），gwern 超长文靠后，
#       avc 放最末——它正被 pipe_avc 制书，先等其完成标记再动，避免 output/books/avc 撞车。
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=.venv/bin/python
echo "==================== cleanup 收尾管道 开始 $(date) ===================="

one(){  # $1=source_key
  local k="$1"
  echo "-------------------- cleanup: $k $(date) --------------------"
  $PY -u -m translate.translate --source "$k"
  $PY -u -m translate.summarize --source "$k"
  $PY -u -m book.build_book --source "$k" --lang en --formats epub,pdf,azw3,mobi
  $PY -u -m book.build_book --source "$k" --lang zh --formats epub,pdf,azw3,mobi
}

for k in eladgil firstround farnamstreet danluu feld a16z_benmarc gwern; do
  one "$k"
done

# avc：等 pipe_avc 制书完成再收尾其失败篇，避免制书目录并发写
echo "cleanup: 等 pipe_avc 完成后处理 avc …"
for i in $(seq 1 240); do
  grep -q "avc 管道 完成" logs/pipe_avc.log 2>/dev/null && break
  pgrep -f pipe_avc.sh >/dev/null 2>&1 || break   # pipe_avc 已退出也放行
  sleep 30
done
one avc

echo "==================== cleanup 管道 完成 $(date) ===================="
