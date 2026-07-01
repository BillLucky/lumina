"""播客 ASR 处理链路核对表 —— 保证「不多不少、不重不漏」。

按系列逐段统计流水线各环节的数量，把「断点」暴露出来，用于 A100 服务器转写 ←→ 本地
之间的无缝衔接核对：同步音频过去、转写回灌之后，跑一下就知道有没有漏集/重复/掉章。

链路：feed 集数 ≥ 已下 mp3 ≥ 已转 asr.json ≥ 已入库文章 ≥ 已译 ≥ 可制书(章节)
每一级只会 ≤ 上一级；差额就是「待办」。因 (source_id, slug=eid) 唯一 upsert，永不重复。

用法：
  PYTHONPATH=src .venv/bin/python scripts/asr_status.py           # 全系列
  PYTHONPATH=src .venv/bin/python scripts/asr_status.py a16z_ai   # 单系列
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]) + "/src")
from common import config, db                       # noqa: E402
from scrape.scrape_podcast import SERIES            # noqa: E402


def disk(key: str):
    mp3 = len(glob.glob(f"{config.DATA_DIR}/{key}/audio/*.mp3"))
    asr = len(glob.glob(f"{config.DATA_DIR}/{key}/*.asr.json"))
    return mp3, asr


def dbstats(key: str):
    with db.cursor() as c:
        c.execute("""SELECT
              COUNT(DISTINCT a.id) arts,
              SUM(CASE WHEN a.is_external=0 THEN 1 ELSE 0 END) chapters,
              SUM(CASE WHEN t.status='done' THEN 1 ELSE 0 END) trans,
              SUM(CASE WHEN t.status='failed' THEN 1 ELSE 0 END) failed
          FROM sources s LEFT JOIN articles a ON a.source_id=s.id
          LEFT JOIN translations t ON t.article_id=a.id AND t.target_lang='zh'
          WHERE s.source_key=%s""", (key,))
        r = c.fetchone() or {}
    return (r.get("arts") or 0, r.get("chapters") or 0,
            r.get("trans") or 0, r.get("failed") or 0)


def main():
    keys = sys.argv[1:] or list(SERIES.keys())
    print(f"{'系列':22}{'mp3':>6}{'asr':>6}{'入库':>6}{'章节':>6}{'已译':>6}{'失败':>6}  待办")
    print("─" * 78)
    T = dict(mp3=0, asr=0, arts=0, ch=0, tr=0, fa=0)
    for k in keys:
        if k not in SERIES:
            continue
        mp3, asr = disk(k)
        arts, ch, tr, fa = dbstats(k)
        todo = []
        if mp3 - asr > 0:
            todo.append(f"待转写 {mp3-asr}")
        if asr - arts > 0:
            todo.append(f"待入库 {asr-arts}")
        if ch - tr - fa > 0:
            todo.append(f"待翻译 {ch-tr-fa}")
        if fa > 0:
            todo.append(f"重译 {fa}")
        flag = "" if not todo else "  ← " + " · ".join(todo)
        print(f"{k:22}{mp3:>6}{asr:>6}{arts:>6}{ch:>6}{tr:>6}{fa:>6}{flag}")
        T["mp3"] += mp3; T["asr"] += asr; T["arts"] += arts
        T["ch"] += ch; T["tr"] += tr; T["fa"] += fa
    print("─" * 78)
    print(f"{'合计':22}{T['mp3']:>6}{T['asr']:>6}{T['arts']:>6}{T['ch']:>6}{T['tr']:>6}{T['fa']:>6}")
    # 一致性判定
    gap_asr = T["mp3"] - T["asr"]
    print()
    if gap_asr > 0:
        print(f"⏳ 还有 {gap_asr} 集已下载未转写（这些交给 A100 服务器转写后回灌）。")
    if T["asr"] - T["arts"] > 0:
        print(f"⚠ {T['asr']-T['arts']} 集已转写但未入库 → 跑 scrape_podcast --no-download 回灌。")
    if gap_asr == 0 and T["asr"] == T["arts"] and T["ch"] == T["tr"]:
        print("✅ 全链路对齐：每集恰好一章，不多不少。")


if __name__ == "__main__":
    main()
