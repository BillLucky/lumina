"""First Round Review（review.firstround.com，Ghost）文章抓取器。

sitemap-posts.xml 列出全部文章 URL（~970）；正文在 <article class="ghost-content">，
标题 <h1 data-label="Article Title">，日期 <meta property="article:published_time">。
服务端渲染 HTML（非 SPA），可直接解析。

用法：
  python -m scrape.scrape_firstround
  python -m scrape.scrape_firstround --limit 5
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

BASE = "https://review.firstround.com"
SITEMAP = f"{BASE}/sitemap-posts.xml"


def sitemap_urls(xml: bytes) -> list[str]:
    root = ET.fromstring(xml)
    return [loc.text.strip() for loc in root.iter() if loc.tag.endswith("}loc") and loc.text]


def extract(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.select_one('h1[data-label="Article Title"]') or soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else (soup.title.get_text(strip=True) if soup.title else "")
    body = soup.select_one("article.ghost-content") or soup.select_one("article")
    published_at = published_text = None
    meta = soup.select_one('meta[property="article:published_time"]')
    if meta and meta.get("content"):
        try:
            published_at = datetime.fromisoformat(meta["content"].replace("Z", "+00:00")).replace(tzinfo=None)
            published_text = published_at.strftime("%B %d, %Y")
        except Exception:
            pass
    if body:
        for t in body.find_all(["script", "style"]):
            t.decompose()
        content_html = body.decode_contents().strip()
        content_text = body.get_text("\n", strip=True)
    else:
        content_html = content_text = ""
    return dict(title=title, published_at=published_at, published_text=published_text,
                content_html=content_html, content_text=content_text,
                is_external=len(content_text) < 200)


def run(limit: int | None = None):
    source_id = db.get_source_id("firstround")
    fetcher = PoliteFetcher(source_id=source_id)
    print(f"抓取 sitemap {SITEMAP} ...")
    urls = sitemap_urls(fetcher.get(SITEMAP, note="sitemap").content)
    urls = [u for u in urls if u.rstrip("/") != BASE]
    print(f"发现 {len(urls)} 篇")
    if limit:
        urls = urls[:limit]

    out_dir = config.DATA_DIR / "firstround"
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
        p = extract(raw)
        _, changed = db.upsert_article(
            source_id, slug=slug, url=url, title=p["title"], author="First Round Review",
            published_at=p["published_at"], published_text=p["published_text"],
            raw_html=raw, content_html=p["content_html"], content_text=p["content_text"],
            meta={"slug": slug}, http_status=resp.status_code, is_external=p["is_external"])
        if changed and not p["is_external"]:
            n += 1
        flag = "EXT" if p["is_external"] else ("NEW" if changed else "=")
        print(f"[{i}/{len(urls)}] {flag:3} {p['published_text'] or '?':16} {p['title'][:46]}")

    total = db.renumber_chrono(source_id)
    print(f"\n完成。可制书文章 {total} 篇；本轮更新 {n}。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(limit=args.limit)
