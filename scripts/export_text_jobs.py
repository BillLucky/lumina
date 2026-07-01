"""导出「本地待译/失败的文本文章」为翻译任务包，供服务器翻译（避开本地不稳网络）。

流程：本地(有 bs4)把文章 content_html 切成块 → 写 data/_texttrans/<source>__<id>.job.json；
rsync 到服务器 → translate_jobs.py 逐块调 M3 译 → 写 <...>.done.json；rsync 回本地 →
import_text_jobs.py upsert 进 translations。分块在本地做，服务器端零 DB/零 bs4 依赖。

只导出文本源（播客走 asr/zh.json 那条链）。幂等：已 done 且 src_hash 对齐的不导出。

用法：
  PYTHONPATH=src .venv/bin/python scripts/export_text_jobs.py              # 全部待译文本
  PYTHONPATH=src .venv/bin/python scripts/export_text_jobs.py gwern        # 指定源
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]) + "/src")
from common import config, db                       # noqa: E402
from translate.translate import split_blocks, make_chunks   # noqa: E402
from scrape.scrape_podcast import SERIES            # noqa: E402

PODCAST = set(SERIES)
OUT = config.DATA_DIR / "_texttrans"


def pending(source_filter=None):
    where = ["a.is_external=0", "a.content_text IS NOT NULL", "a.content_text<>''",
             "(t.id IS NULL OR t.status<>'done' OR t.src_hash<>a.content_hash OR t.src_hash IS NULL)"]
    params = []
    if source_filter:
        where.append("s.source_key=%s"); params.append(source_filter)
    with db.cursor() as c:
        c.execute(f"""SELECT a.id, s.source_key, a.title, a.content_html, a.content_hash
            FROM articles a JOIN sources s ON s.id=a.source_id
            LEFT JOIN translations t ON t.article_id=a.id AND t.target_lang='zh'
            WHERE {' AND '.join(where)} ORDER BY s.source_key, a.chrono_index""", params)
        return [r for r in c.fetchall() if r["source_key"] not in PODCAST]


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else None
    OUT.mkdir(parents=True, exist_ok=True)
    arts = pending(src)
    n = 0
    for a in arts:
        chunks = make_chunks(split_blocks(a["content_html"] or ""))
        if not chunks:
            continue
        job = OUT / f"{a['source_key']}__{a['id']}.job.json"
        job.write_text(json.dumps(
            {"id": a["id"], "source": a["source_key"], "title": a["title"],
             "content_hash": a["content_hash"], "chunks": chunks},
            ensure_ascii=False), encoding="utf-8")
        n += 1
    print(f"导出 {n} 篇待译文本 → {OUT}（rsync 到服务器后跑 translate_jobs.py）")


if __name__ == "__main__":
    main()
