"""把服务器翻译好的 <eid>.zh.json 导入本地 translations 表（播客专用，免本地再调 M3 译正文）。

配合服务器端 scripts/translate_asr.py：服务器 ASR→asr.json、翻译→zh.json，rsync 回本地后，
先 scrape_podcast --no-download 把 asr.json 入库成 article（英文正文），再跑本脚本把对应
zh.json 的中文正文 upsert 进 translations（status=done、src_hash 对齐）。标题较短，本地就地
译（失败则退回英文标题，不阻塞）。幂等：src_hash 与原文一致的已 done 篇目跳过。

用法：
  PYTHONPATH=src .venv/bin/python scripts/import_asr_zh.py            # 全部播客系列
  PYTHONPATH=src .venv/bin/python scripts/import_asr_zh.py a16z       # 指定系列
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]) + "/src")
from common import config, db                       # noqa: E402
from scrape.scrape_podcast import SERIES            # noqa: E402
from translate.client import call_messages          # noqa: E402

TITLE_SYS = "你是顶尖中英翻译家。把给定英文标题译成简洁优雅的简体中文，只输出译文本身。"


def _title_zh(title: str) -> str:
    try:
        t = call_messages(TITLE_SYS, title, target_lang="zh", max_tokens=200)["text"]
        return t.strip().strip('"').strip("「」")[:700]
    except Exception:
        return title            # 标题译失败不阻塞，退回英文


def run(series_filter=None):
    keys = [series_filter] if series_filter else list(SERIES.keys())
    total_done = total_skip = total_missing = 0
    for key in keys:
        with db.cursor() as c:
            c.execute("""SELECT a.id, a.slug, a.title, a.content_hash
                FROM articles a JOIN sources s ON s.id=a.source_id
                WHERE s.source_key=%s AND a.is_external=0""", (key,))
            arts = c.fetchall()
        if not arts:
            continue
        done = skip = missing = 0
        for a in arts:
            zhf = config.DATA_DIR / key / f"{a['slug']}.zh.json"
            if not zhf.exists():
                missing += 1
                continue
            # 已有对齐的 done 译文则跳过（幂等）
            with db.cursor() as c:
                c.execute("""SELECT status, src_hash FROM translations
                    WHERE article_id=%s AND target_lang='zh'""", (a["id"],))
                ex = c.fetchone()
            if ex and ex["status"] == "done" and ex["src_hash"] == a["content_hash"]:
                skip += 1
                continue
            content_zh = json.loads(zhf.read_text()).get("content_zh") or ""
            if not content_zh.strip():
                missing += 1
                continue
            title_zh = _title_zh(a["title"])
            with db.cursor() as c:
                c.execute("""INSERT INTO translations
                        (article_id,target_lang,model,title_translated,content_translated,
                         src_hash,status)
                     VALUES (%s,'zh',%s,%s,%s,%s,'done')
                     ON DUPLICATE KEY UPDATE model=VALUES(model),
                        title_translated=VALUES(title_translated),
                        content_translated=VALUES(content_translated),
                        src_hash=VALUES(src_hash),status='done',error=NULL""",
                    (a["id"], config.TRANSLATE_MODEL, title_zh, content_zh, a["content_hash"]))
            done += 1
            if done % 20 == 0:
                print(f"  {key}: 已导入 {done}…", flush=True)
        print(f"{key:22} 导入 {done} · 跳过(已对齐) {skip} · 缺 zh.json {missing}")
        total_done += done; total_skip += skip; total_missing += missing
    print(f"\n合计：导入 {total_done} · 跳过 {total_skip} · 缺 {total_missing}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
