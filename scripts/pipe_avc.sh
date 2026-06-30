#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=.venv/bin/python
echo "==================== avc 独立管道 开始 $(date) ===================="
$PY -u -m translate.translate --source avc
$PY -u -m translate.summarize --source avc
$PY -u -m book.build_book --source avc --lang en --formats epub,pdf,azw3,mobi
$PY -u -m book.build_book --source avc --lang zh --formats epub,pdf,azw3,mobi
echo "==================== avc 管道 完成 $(date) ===================="
