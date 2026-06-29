"""Marc Andreessen 文章抓取器（pmarchive.com，pmarca 经典存档，静态 HTML5）。

结构很规整：每篇用 <article> 容器，内含 <h1> 标题、<time>Posted on June 18, 2007</time>
精确日期、以及 <p> 正文。首页 index 列出全部文章链接。

用法：
  python -m scrape.scrape_pmarca
  python -m scrape.scrape_pmarca --limit 3
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import config, db                  # noqa: E402
from common.http import PoliteFetcher           # noqa: E402
from scrape.dates import parse_full_date        # noqa: E402

BASE = "https://pmarchive.com"
INDEX_URL = f"{BASE}/"
TITLE_PREFIX = re.compile(r"^\s*Pmarchive\s*[·:|-]\s*", re.IGNORECASE)


def parse_index(html: str) -> list[str]:
    """返回首页出现的文章 slug 列表（去重保序）。"""
    soup = BeautifulSoup(html, "lxml")
    slugs, seen = [], set()
    for a in soup.find_all("a", href=True):
        m = re.search(r"([a-z0-9_]+)\.html$", a["href"])
        if not m:
            continue
        slug = m.group(1)
        if slug in ("index", "") or slug in seen:
            continue
        seen.add(slug)
        slugs.append(slug)
    return slugs


def extract(slug: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else slug
    title = TITLE_PREFIX.sub("", title).strip()

    art = soup.select_one("article") or soup.select_one("main") or soup.body
    if art is None:
        return dict(title=title, published_at=None, published_text=None,
                    content_html="", content_text="", is_external=True)

    # 日期来自 <time>
    published_at = published_text = None
    tnode = art.find("time")
    if tnode:
        published_at, _ = parse_full_date(tnode.get_text(" ", strip=True))
        if published_at:
            published_text = published_at.strftime("%B %d, %Y")
    # 兜底：正文里找日期
    if published_at is None:
        published_at, m = parse_full_date(art.get_text(" ", strip=True)[:200])
        published_text = m

    # 移除元信息节点（标题/日期/分享/导航），仅保留正文块
    for node in art.find_all(["h1", "time", "nav", "header", "footer", "script", "style"]):
        node.decompose()
    blocks = []
    for el in art.find_all(["h2", "h3", "p", "ul", "ol", "blockquote", "pre"],
                           recursive=True):
        s = str(el).strip()
        if s:
            blocks.append(s)
    content_html = "\n".join(blocks)
    content_text = BeautifulSoup(content_html, "lxml").get_text("\n", strip=True)
    is_external = len(content_text) < 200
    return dict(title=title, published_at=published_at, published_text=published_text,
                content_html=content_html, content_text=content_text,
                is_external=is_external)


def run(limit: int | None = None):
    source_id = db.get_source_id("pmarca")
    fetcher = PoliteFetcher(source_id=source_id)

    print(f"抓取索引 {INDEX_URL} ...")
    # pmarchive.com 不在 HTTP 头声明 charset，requests 会误判为 latin-1，强制按 UTF-8 解码
    slugs = parse_index(fetcher.get(INDEX_URL, note="index").content.decode("utf-8", "replace"))
    print(f"发现 {len(slugs)} 篇文章")
    if limit:
        slugs = slugs[:limit]

    n_changed = 0
    for i, slug in enumerate(slugs, 1):
        url = f"{BASE}/{slug}.html"
        try:
            resp = fetcher.get(url, note=f"article {slug}")
        except Exception as e:
            print(f"[{i}/{len(slugs)}] !! {slug}: {e}")
            continue
        raw_html = resp.content.decode("utf-8", "replace")
        p = extract(slug, raw_html)
        aid, changed = db.upsert_article(
            source_id, slug=slug, url=url, title=p["title"], author="Marc Andreessen",
            published_at=p["published_at"], published_text=p["published_text"],
            raw_html=raw_html, content_html=p["content_html"],
            content_text=p["content_text"], meta={"slug": slug},
            http_status=resp.status_code, is_external=p["is_external"])
        (config.DATA_DIR / "pmarca" / f"{slug}.html").write_text(raw_html, encoding="utf-8")
        if changed and not p["is_external"]:
            n_changed += 1
        flag = "EXT" if p["is_external"] else ("NEW" if changed else "=")
        print(f"[{i}/{len(slugs)}] {flag:3} {slug[:34]:34} {p['published_text'] or '?':16} {p['title'][:36]}")

    total = db.renumber_chrono(source_id)
    print(f"\n完成。可制书文章 {total} 篇；本轮内容更新 {n_changed}。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(limit=args.limit)
