#!/usr/bin/env bash
# 看门狗：无人值守时保活各 grind/管道 + 自愈 Docker/MySQL。
# 本进程跑在 host（非 Docker），Docker 崩了它仍存活并能把 Docker/MySQL 拉回来。
# 每 ~3 分钟自检；各驱动脚本均可断点续传，重新拉起安全。
#
# 用法：  bash scripts/watchdog.sh   （建议后台：日志 logs/watchdog.log）
cd "$(dirname "$0")/.."
export PYTHONPATH=src
LOG=logs/watchdog.log
PY=.venv/bin/python

running(){ pgrep -f "$1" >/dev/null 2>&1; }
log(){ echo "$(date '+%F %T') $*" >> "$LOG"; }

ensure_db(){
  if ! docker info >/dev/null 2>&1; then
    log "docker daemon down → 拉起 Docker Desktop"
    open -a Docker
    for i in $(seq 1 75); do docker info >/dev/null 2>&1 && break; sleep 4; done
  fi
  if ! $PY -c "from common import db; db.connect()" >/dev/null 2>&1; then
    log "mysql 不可达 → docker compose up -d"
    docker compose up -d >> "$LOG" 2>&1
    for i in $(seq 1 20); do $PY -c "from common import db; db.connect()" >/dev/null 2>&1 && break; sleep 3; done
  fi
}

# 驱动名 → 完成标记（在各自日志里出现即视为完成，不再重拉）
guard(){  # $1=脚本名  $2=日志  $3=完成标记
  if ! running "$1" && ! grep -q "$3" "$2" 2>/dev/null; then
    log "$1 不在跑且未完成 → 重新拉起"
    nohup bash "scripts/$1" >> "$2" 2>&1 &
  fi
}
done_marker(){ grep -q "$2" "$1" 2>/dev/null; }

log "===== watchdog 启动 ====="
while true; do
  ensure_db
  guard "a16z_grind.sh"  "logs/a16z_grind.log"  "全部完成"
  guard "text_grind.sh"  "logs/text_grind.log"  "全部完成"
  guard "text_grind2.sh" "logs/text_grind2.log" "全部完成"
  guard "pipe_avc.sh"    "logs/pipe_avc.log"    "avc 管道 完成"
  guard "pipe_gwern.sh"  "logs/pipe_gwern.log"  "gwern 管道 完成"
  guard "pipe_cleanup.sh" "logs/pipe_cleanup.log" "cleanup 管道 完成"
  # 顺手整理 output/（归类新书到 books/<源>/）+ 刷新 INDEX.md 仪表盘
  $PY scripts/build_index.py >/dev/null 2>&1 || true
  if done_marker "logs/a16z_grind.log" "全部完成" \
     && done_marker "logs/text_grind.log" "全部完成" \
     && done_marker "logs/text_grind2.log" "全部完成" \
     && done_marker "logs/pipe_avc.log" "avc 管道 完成" \
     && done_marker "logs/pipe_gwern.log" "gwern 管道 完成" \
     && done_marker "logs/pipe_cleanup.log" "cleanup 管道 完成"; then
    log "全部 grind/管道 完成 → watchdog 退出"
    break
  fi
  sleep 180
done
