"""Chris Dixon（cdixon.org，自建静态 HTML5）文章抓取器。

结构规整：/archive 单页列出全部文章链接（相对 /YYYY/MM/DD/slug/）；每篇正文在
<article class="post"> 内，标题 <h1 class="post-title">，日期 <time datetime=ISO8601>。
与 pmarca 同构，思路照搬。

用法：
  python -m scrape.scrape_cdixon
  python -m scrape.scrape_cdixon --limit 5
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import config, db                  # noqa: E402
from common.http import PoliteFetcher           # noqa: E402

BASE = "https://cdixon.org"
INDEX_URL = f"{BASE}/archive"
_LINK_RE = re.compile(r"^/20\d{2}/\d{2}/\d{2}/[^/]+/?$")


def parse_index(html: str) -> list[str]:
    """返回文章相对路径列表（/YYYY/MM/DD/slug/，去重保序）。"""
    soup = BeautifulSoup(html, "lxml")
    paths, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].split("#")[0]
        if _LINK_RE.match(href) and href not in seen:
            seen.add(href)
            paths.append(href)
    return paths


def extract(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    art = soup.select_one("article.post") or soup.select_one("article") or soup.body
    h1 = art.select_one("h1.post-title") or art.find(["h1", "h2"])
    title = h1.get_text(" ", strip=True) if h1 else (
        soup.title.get_text(strip=True) if soup.title else "")

    published_at = published_text = None
    tnode = art.find("time")
    if tnode and tnode.get("datetime"):
        try:
            published_at = datetime.fromisoformat(
                tnode["datetime"].replace("Z", "+00:00")).replace(tzinfo=None)
            published_text = published_at.strftime("%B %d, %Y")
        except Exception:
            pass

    # 移除元信息节点，保留正文块
    for node in art.find_all(["h1", "time", "nav", "header", "footer",
                              "script", "style"]):
        node.decompose()
    blocks = []
    for el in art.find_all(["h2", "h3", "p", "ul", "ol", "blockquote", "pre",
                            "img", "figure"], recursive=True):
        s = str(el).strip()
        if s:
            blocks.append(s)
    content_html = "\n".join(blocks)
    content_text = BeautifulSoup(content_html, "lxml").get_text("\n", strip=True)
    return dict(title=title, published_at=published_at, published_text=published_text,
                content_html=content_html, content_text=content_text,
                is_external=len(content_text) < 120)


def run(limit: int | None = None):
    source_id = db.get_source_id("cdixon")
    fetcher = PoliteFetcher(source_id=source_id)

    print(f"抓取索引 {INDEX_URL} ...")
    paths = parse_index(fetcher.get(INDEX_URL, note="archive").content.decode("utf-8", "replace"))
    print(f"发现 {len(paths)} 篇文章")
    if limit:
        paths = paths[:limit]

    out_dir = config.DATA_DIR / "cdixon"
    out_dir.mkdir(parents=True, exist_ok=True)
    n_changed = 0
    for i, path in enumerate(paths, 1):
        url = BASE + path
        slug = path.strip("/").replace("/", "-")
        try:
            resp = fetcher.get(url, note=f"article {slug}")
        except Exception as e:
            print(f"[{i}/{len(paths)}] !! {slug}: {e}")
            continue
        raw_html = resp.content.decode("utf-8", "replace")
        p = extract(raw_html)
        _, changed = db.upsert_article(
            source_id, slug=slug, url=url, title=p["title"], author="Chris Dixon",
            published_at=p["published_at"], published_text=p["published_text"],
            raw_html=raw_html, content_html=p["content_html"],
            content_text=p["content_text"], meta={"path": path},
            http_status=resp.status_code, is_external=p["is_external"])
        (out_dir / f"{slug}.html").write_text(raw_html, encoding="utf-8")
        if changed and not p["is_external"]:
            n_changed += 1
        flag = "EXT" if p["is_external"] else ("NEW" if changed else "=")
        print(f"[{i}/{len(paths)}] {flag:3} {p['published_text'] or '?':16} {p['title'][:46]}")

    total = db.renumber_chrono(source_id)
    print(f"\n完成。可制书文章 {total} 篇；本轮内容更新 {n_changed}。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(limit=args.limit)
