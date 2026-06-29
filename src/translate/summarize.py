"""为每篇文章生成「核心导读」：一句话论点 + 3~6 个要点（中英双语），
供制书时在文章开头渲染导读卡片 + 思维导图。

复用 MiniMax-M3，单次调用返回双语 JSON，写入 summaries 表。增量：src_hash 变化才重做。

用法：
  python -m translate.summarize --source naval
  python -m translate.summarize --source all --limit 5
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import config, db                   # noqa: E402
from translate.client import call_messages      # noqa: E402

# 用「分隔符纯文本」而非 JSON 输出，避免引号/换行导致的解析失败（更健壮）
SYSTEM = (
    "你是一位擅长提炼文章精髓的编辑。读完一篇英文文章后，输出它的「核心导读」，"
    "帮助读者在阅读正文前快速把握全文最重要的思想。\n\n"
    "提炼 3 到 6 个关键要点，每个含一个简短小标题与一句话说明，按文章逻辑顺序排列，"
    "覆盖最重要的观点/方法/结论。中文要信达雅，英文简洁地道；中英要点一一对应。\n\n"
    "严格按以下纯文本模板输出，不要任何额外解释、不要 JSON、不要代码围栏。"
    "每个要点一行，小标题与说明之间用 ` :: ` 分隔：\n\n"
    "[EN]\n"
    "THESIS: <one-sentence core thesis>\n"
    "- <label> :: <one sentence detail>\n"
    "- <label> :: <one sentence detail>\n"
    "[ZH]\n"
    "THESIS: <一句话核心论点>\n"
    "- <小标题> :: <一句话说明>\n"
    "- <小标题> :: <一句话说明>"
)


def _plain(article) -> str:
    txt = BeautifulSoup(article["content_html"] or "", "lxml").get_text("\n", strip=True)
    return txt[:12000]  # 截断超长文，足够提炼主旨


def _parse(raw: str) -> dict:
    """解析分隔符模板为 {en:{thesis,points}, zh:{thesis,points}}。"""
    out = {"en": {"thesis": "", "points": []}, "zh": {"thesis": "", "points": []}}
    cur = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        up = line.upper()
        if up.startswith("[EN]"):
            cur = "en"; continue
        if up.startswith("[ZH]"):
            cur = "zh"; continue
        if cur is None:
            continue
        if up.startswith("THESIS:"):
            out[cur]["thesis"] = line.split(":", 1)[1].strip()
        elif line.startswith("-") or line.startswith("•"):
            body = line.lstrip("-•").strip()
            if "::" in body:
                label, detail = body.split("::", 1)
            else:
                label, detail = body, ""
            label = label.strip().strip("*").strip()
            if label:
                out[cur]["points"].append({"label": label, "detail": detail.strip()})
    if not out["en"]["points"] and not out["zh"]["points"]:
        raise ValueError("未解析到要点")
    return out


def summarize_one(article) -> dict:
    user = f"标题：{article['title']}\n\n正文：\n{_plain(article)}"
    raw = call_messages(SYSTEM, user, article_id=article["id"],
                        target_lang="summary", max_tokens=2000)["text"]
    return _parse(raw)


def _save(article, data):
    en = data.get("en", {})
    zh = data.get("zh", {})
    points = {"en": en.get("points", []), "zh": zh.get("points", [])}
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO summaries
                   (article_id,model,thesis_en,thesis_zh,points_json,src_hash,status)
               VALUES (%s,%s,%s,%s,%s,%s,'done')
               ON DUPLICATE KEY UPDATE model=VALUES(model),thesis_en=VALUES(thesis_en),
                   thesis_zh=VALUES(thesis_zh),points_json=VALUES(points_json),
                   src_hash=VALUES(src_hash),status='done'""",
            (article["id"], config.TRANSLATE_MODEL,
             (en.get("thesis") or "")[:1024], (zh.get("thesis") or "")[:1024],
             json.dumps(points, ensure_ascii=False), article["content_hash"]))


def pending(source_key, redo, limit):
    where = ["a.is_external=0", "a.content_html IS NOT NULL", "a.content_html<>''"]
    params = []
    if source_key and source_key != "all":
        where.append("s.source_key=%s")
        params.append(source_key)
    sql = f"""SELECT a.id,a.title,a.content_html,a.content_hash,s.source_key
              FROM articles a JOIN sources s ON s.id=a.source_id
              LEFT JOIN summaries m ON m.article_id=a.id
              WHERE {' AND '.join(where)}
              {'' if redo else "AND (m.id IS NULL OR m.status<>'done' OR m.src_hash<>a.content_hash OR m.src_hash IS NULL)"}
              ORDER BY s.source_key,a.chrono_index"""
    with db.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return rows[:limit] if limit else rows


def run(source_key=None, redo=False, limit=None):
    arts = pending(source_key, redo, limit)
    if not arts:
        print("没有待生成导读的文章。")
        return
    print(f"生成导读 {len(arts)} 篇，并发 {config.TRANSLATE_CONCURRENCY}")
    done = failed = 0
    with ThreadPoolExecutor(max_workers=config.TRANSLATE_CONCURRENCY) as ex:
        futs = {ex.submit(summarize_one, a): a for a in arts}
        for fut in as_completed(futs):
            a = futs[fut]
            try:
                _save(a, fut.result())
                done += 1
                print(f"  ✓ [{done+failed}/{len(arts)}] {a['source_key']}/{a['title'][:42]}")
            except Exception as e:
                failed += 1
                print(f"  ✗ [{done+failed}/{len(arts)}] {a['source_key']}/{a['title'][:36]}: {e}")
    print(f"\n完成：成功 {done}，失败 {failed}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="all")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--redo", action="store_true")
    args = ap.parse_args()
    run(source_key=args.source, redo=args.redo, limit=args.limit)
