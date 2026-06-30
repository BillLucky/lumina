#!/usr/bin/env bash
# 看门狗：无人值守时保活两个 grind + 自愈 Docker/MySQL。
# 本进程跑在 host（非 Docker），所以 Docker 崩了它仍存活并能把 Docker/MySQL 拉回来。
# 每 ~3 分钟自检一次；grind 脚本均可断点续传，重新拉起安全。
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

log "===== watchdog 启动 ====="
while true; do
  ensure_db
  if ! running "a16z_grind.sh" && ! grep -q "全部完成" logs/a16z_grind.log 2>/dev/null; then
    log "a16z grind 不在跑且未完成 → 重新拉起"
    nohup bash scripts/a16z_grind.sh >> logs/a16z_grind.log 2>&1 &
  fi
  if ! running "text_grind.sh" && ! grep -q "全部完成" logs/text_grind.log 2>/dev/null; then
    log "text grind 不在跑且未完成 → 重新拉起"
    nohup bash scripts/text_grind.sh >> logs/text_grind.log 2>&1 &
  fi
  # 两个都完成则退出看门狗
  if grep -q "全部完成" logs/a16z_grind.log 2>/dev/null && grep -q "全部完成" logs/text_grind.log 2>/dev/null; then
    log "两个 grind 均已完成 → watchdog 退出"
    break
  fi
  sleep 180
done
