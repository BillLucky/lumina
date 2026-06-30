"""Brad Feld（feld.com，Hugo 静态）文章抓取器。

posts-sitemap.xml 列出全部文章 URL（~5500，多为短文/链接帖，过短的由 is_external 过滤）。
正文 <div class="post-content">，标题 <h1 class="post-title">；日期从 URL /archives/YYYY/MM/ 提取
（缺省日=1，足够按月排序），兜底找页面 <time>。

用法：
  python -m scrape.scrape_feld
  python -m scrape.scrape_feld --limit 5
"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import config, db                  # noqa: E402
from common.http import PoliteFetcher           # noqa: E402

BASE = "https://feld.com"
SITEMAP = f"{BASE}/posts-sitemap.xml"
_DATE_RE = re.compile(r"/archives/(\d{4})/(\d{2})/")


def sitemap_urls(xml: bytes) -> list[str]:
    root = ET.fromstring(xml)
    return [loc.text.strip() for loc in root.iter() if loc.tag.endswith("}loc") and loc.text]


def extract(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    art = soup.select_one("article.post-single") or soup.select_one("article") or soup.body
    h1 = (art.select_one("h1.post-title") if art else None) or soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else (soup.title.get_text(strip=True) if soup.title else "")

    published_at = published_text = None
    m = _DATE_RE.search(url)
    if m:
        try:
            published_at = datetime(int(m.group(1)), int(m.group(2)), 1)
            published_text = published_at.strftime("%B %Y")
        except Exception:
            pass

    body = (art.select_one("div.post-content") if art else None)
    if body:
        for t in body.find_all(["script", "style", "nav", "footer"]):
            t.decompose()
        content_html = body.decode_contents().strip()
        content_text = body.get_text("\n", strip=True)
    else:
        content_html = content_text = ""
    return dict(title=title, published_at=published_at, published_text=published_text,
                content_html=content_html, content_text=content_text,
                is_external=len(content_text) < 200)


def run(limit: int | None = None):
    source_id = db.get_source_id("feld")
    fetcher = PoliteFetcher(source_id=source_id)
    print(f"抓取 sitemap {SITEMAP} ...")
    urls = sitemap_urls(fetcher.get(SITEMAP, note="sitemap").content)
    urls = [u for u in urls if "/archives/" in u]
    print(f"发现 {len(urls)} 篇")
    if limit:
        urls = urls[:limit]

    out_dir = config.DATA_DIR / "feld"
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for i, url in enumerate(urls, 1):
        slug = url.rstrip("/").split("/")[-1]
        try:
            resp = fetcher.get(url, note=f"article {slug}")
        except Exception as e:
            print(f"[{i}/{len(urls)}] !! {slug}: {e}")
            continue
        raw = resp.content.decode("utf-8", "replace")
        p = extract(url, raw)
        _, changed = db.upsert_article(
            source_id, slug=slug, url=url, title=p["title"], author="Brad Feld",
            published_at=p["published_at"], published_text=p["published_text"],
            raw_html=raw, content_html=p["content_html"], content_text=p["content_text"],
            meta={"slug": slug}, http_status=resp.status_code, is_external=p["is_external"])
        if changed and not p["is_external"]:
            n += 1
        if i % 50 == 0 or i == len(urls):
            print(f"[{i}/{len(urls)}] {p['published_text'] or '?':12} {p['title'][:42]}")

    total = db.renumber_chrono(source_id)
    print(f"\n完成。可制书文章 {total} 篇；本轮更新 {n}。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(limit=args.limit)
