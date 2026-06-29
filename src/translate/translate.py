"""翻译流水线：把英文文章译成信达雅的简体中文。

设计：
  - 文章级并发（TRANSLATE_CONCURRENCY 个 worker，3~5），每篇内部按段落分块顺序翻译，
    既能并行又保证单篇风格连贯。
  - 分块：按 HTML 顶层块元素累积到字符预算，避免超长输出被截断。
  - 增量/续传：translations.src_hash == articles.content_hash 且 status=done 则跳过；
    原文更新(hash 变化)或失败则重译。
  - 每次模型调用全量写入 api_call 审计表（见 client.py）。

用法：
  python -m translate.translate --source paulgraham
  python -m translate.translate --source naval --limit 3
  python -m translate.translate --source all --redo        # 强制重译
"""
from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import config, db                       # noqa: E402
from translate.client import call_messages          # noqa: E402

TARGET_LANG = "zh"
# 模型上下文大、token 充足，尽量大块翻译以保证上下文连贯与术语一致
CHUNK_CHARS = 9000   # 每块源文本字符预算（中文输出约 1.5~2x，受 TRANSLATE_MAX_TOKENS 兜底）

SYSTEM_PROMPT = (
    "你是一位殿堂级的中英文翻译大家。你的译笔融汇严复「信、达、雅」之准则、傅雷「神似」之追求、"
    "余光中之文采、王佐良之雅正、思果之地道——既有学者的严谨考据，又有文人的从容笔致。"
    "你尤其擅长翻译 Paul Graham、Naval Ravikant 这类思想随笔：逻辑缜密、洞见锋利、行文简练。\n\n"
    "【翻译准则】\n"
    "1. 信（准确严谨）——彻底理解原文的论证逻辑与言外之意，忠实传达，不增不删不曲解；"
    "概念、数据、专有名词必须精确，宁可反复推敲也不臆测。\n"
    "2. 达（通顺地道）——彻底摆脱翻译腔：调整语序、拆分长句、转换词性，写出地道流畅的当代书面中文，"
    "让读者浑然不觉是译文。\n"
    "3. 雅（文采气韵）——再现原作的节奏、语气、幽默与思想锋芒；用词凝练考究，行文有韵律，"
    "该犀利时犀利，该隽永时隽永。\n\n"
    "【一致性】全篇术语、人名、概念译法保持统一；保留原作的段落与强调结构。\n\n"
    "【格式要求】\n"
    "- 输入是 HTML 片段，必须原样保留所有 HTML 标签结构（如 <p> <h1> <h2> <h3> <ul> <ol> <li> "
    "<blockquote> <a> <i> <b> <em> <strong> <code> <pre> <img> 等），只翻译标签内的自然语言文字；\n"
    "- <a> 的 href、<img> 的 src、代码(<code>/<pre>)内容、URL 一律原样保留，绝不翻译或改写；\n"
    "- 人名、公司名、产品名等专有名词：有通行中文译名的用通行译名（如「严复」「余光中」），"
    "技术/品牌名可保留英文（如 Y Combinator、Lisp、Bitcoin）；首次出现的关键人物可用「中文名（English）」形式；\n"
    "- 只输出翻译后的 HTML 片段本身，不要输出任何解释、前言、标题、Markdown 代码围栏(```）或额外说明。"
)


def split_blocks(content_html: str) -> list[str]:
    """把正文 HTML 切成「顶层块元素」字符串列表。"""
    soup = BeautifulSoup(content_html or "", "lxml")
    root = soup.body or soup
    blocks = []
    for el in root.children:
        s = str(el).strip()
        if s and not (getattr(el, "name", None) is None and not s):
            if s:
                blocks.append(s)
    # 退化情况：无块级结构，整体作为一块
    if not blocks and (content_html or "").strip():
        blocks = [content_html.strip()]
    return blocks


def make_chunks(blocks: list[str], budget: int = CHUNK_CHARS) -> list[str]:
    chunks, cur, cur_len = [], [], 0
    for b in blocks:
        if cur and cur_len + len(b) > budget:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(b)
        cur_len += len(b)
        # 单块就超预算：自成一块
        if cur_len > budget:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
    if cur:
        chunks.append("\n".join(cur))
    return chunks


_FENCE = re.compile(r"^\s*```(?:html)?\s*|\s*```\s*$", re.IGNORECASE)


def _strip_fences(text: str) -> str:
    return _FENCE.sub("", text).strip()


def translate_article(article: dict, redo: bool = False) -> dict:
    aid = article["id"]
    title = article["title"]
    content_html = article["content_html"] or ""

    # 标题翻译
    title_zh = call_messages(
        "你是顶尖中英翻译家。把给定英文标题译成简洁优雅的简体中文，只输出译文本身。",
        title, article_id=aid, target_lang=TARGET_LANG, max_tokens=200)["text"].strip()
    title_zh = _strip_fences(title_zh).strip().strip('"').strip("「」")

    blocks = split_blocks(content_html)
    chunks = make_chunks(blocks)
    out_parts, in_tok, out_tok = [], 0, 0
    for ci, chunk in enumerate(chunks, 1):
        res = call_messages(SYSTEM_PROMPT, chunk, article_id=aid,
                            target_lang=TARGET_LANG)
        out_parts.append(_strip_fences(res["text"]))
        in_tok += res.get("input_tokens") or 0
        out_tok += res.get("output_tokens") or 0
    content_zh = "\n".join(out_parts)

    return {"title_zh": title_zh, "content_zh": content_zh,
            "in_tok": in_tok, "out_tok": out_tok, "chunks": len(chunks)}


def _save(article, result):
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO translations
                   (article_id,target_lang,model,title_translated,content_translated,
                    src_hash,status,prompt_tokens,completion_tokens)
               VALUES (%s,%s,%s,%s,%s,%s,'done',%s,%s)
               ON DUPLICATE KEY UPDATE
                   model=VALUES(model),title_translated=VALUES(title_translated),
                   content_translated=VALUES(content_translated),src_hash=VALUES(src_hash),
                   status='done',prompt_tokens=VALUES(prompt_tokens),
                   completion_tokens=VALUES(completion_tokens),error=NULL""",
            (article["id"], TARGET_LANG, config.TRANSLATE_MODEL, result["title_zh"],
             result["content_zh"], article["content_hash"],
             result["in_tok"], result["out_tok"]))


def _mark_failed(article_id, err):
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO translations (article_id,target_lang,model,status,error)
               VALUES (%s,%s,%s,'failed',%s)
               ON DUPLICATE KEY UPDATE status='failed',error=VALUES(error)""",
            (article_id, TARGET_LANG, config.TRANSLATE_MODEL, str(err)[:2000]))


def pending_articles(source_key: str | None, redo: bool, limit: int | None) -> list[dict]:
    where = ["a.is_external=0", "a.content_text IS NOT NULL", "a.content_text<>''"]
    params: list = []
    if source_key and source_key != "all":
        where.append("s.source_key=%s")
        params.append(source_key)
    sql = f"""
        SELECT a.id,a.title,a.content_html,a.content_hash,a.chrono_index,s.source_key
        FROM articles a JOIN sources s ON s.id=a.source_id
        LEFT JOIN translations t ON t.article_id=a.id AND t.target_lang='{TARGET_LANG}'
        WHERE {' AND '.join(where)}
        { '' if redo else "AND (t.id IS NULL OR t.status<>'done' OR t.src_hash<>a.content_hash OR t.src_hash IS NULL)" }
        ORDER BY s.source_key, a.chrono_index
    """
    with db.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return rows[:limit] if limit else rows


def run(source_key=None, redo=False, limit=None):
    articles = pending_articles(source_key, redo, limit)
    if not articles:
        print("没有待翻译文章（全部已是最新译文）。")
        return
    print(f"待翻译 {len(articles)} 篇，并发 {config.TRANSLATE_CONCURRENCY}，模型 {config.TRANSLATE_MODEL}")
    done = failed = 0
    tot_in = tot_out = 0
    with ThreadPoolExecutor(max_workers=config.TRANSLATE_CONCURRENCY) as ex:
        futs = {ex.submit(translate_article, a, redo): a for a in articles}
        for fut in as_completed(futs):
            a = futs[fut]
            try:
                res = fut.result()
                _save(a, res)
                done += 1
                tot_in += res["in_tok"]; tot_out += res["out_tok"]
                print(f"  ✓ [{done+failed}/{len(articles)}] {a['source_key']}/{a['title'][:40]} "
                      f"（{res['chunks']}块, in={res['in_tok']} out={res['out_tok']}）")
            except Exception as e:
                failed += 1
                _mark_failed(a["id"], e)
                print(f"  ✗ [{done+failed}/{len(articles)}] {a['source_key']}/{a['title'][:40]}: {e}")
    print(f"\n完成：成功 {done}，失败 {failed}；累计 token in={tot_in} out={tot_out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="all", help="paulgraham / naval / all")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--redo", action="store_true", help="强制重译")
    args = ap.parse_args()
    run(source_key=args.source, redo=args.redo, limit=args.limit)
