#!/usr/bin/env bash
# 第二批文本源（Dan Luu / Elad Gil / First Round / Brad Feld）→ 双语书。
#
# 抓取全并行（不吃 M3，越快越好）；翻译走**单流水线**：谁先抓完先翻（小→大），
# 只额外加 1 条 M3 翻译流（叠加 avc + farnamstreet），避免多条流互相 429 空转。
# Gwern 结构特殊（按主题非时间、单篇超长），暂不在此批。
# 注：避开 bash 3.2 不支持的关联数组——模块名恒为 scrape_<key>。
#
# 用法：  bash scripts/text_grind2.sh   （建议后台：日志 logs/text_grind2.log）
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=.venv/bin/python
ORDER="danluu eladgil firstround feld"

echo "==================== text grind2 开始 $(date) ===================="
echo "########## 4 站并行抓取 ##########"
for k in $ORDER; do
  if ! pgrep -f "scrape\.scrape_$k\b" >/dev/null 2>&1; then
    $PY -u -m "scrape.scrape_$k" > "logs/scrape_$k.log" 2>&1 &
  fi
done

echo "########## 单流水线翻译：谁先抓完先翻（小→大）##########"
for KEY in $ORDER; do
  while pgrep -f "scrape\.scrape_$KEY\b" >/dev/null 2>&1; do sleep 15; done
  echo "==================== [$KEY] 抓完，翻译/出书 $(date) ===================="
  $PY -u -m translate.translate --source "$KEY"
  $PY -u -m translate.summarize --source "$KEY"
  $PY -u -m book.build_book --source "$KEY" --lang en --formats epub,pdf,azw3,mobi
  $PY -u -m book.build_book --source "$KEY" --lang zh --formats epub,pdf,azw3,mobi
  echo "==================== [$KEY] 完成 $(date) ===================="
done
echo "==================== text grind2 全部完成 $(date) ===================="
