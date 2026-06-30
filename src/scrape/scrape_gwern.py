"""Gwern Branwen（gwern.net，Hakyll+Pandoc 静态）文章抓取器。

Gwern 是研究型长文档库，按主题而非时间组织、单篇极长、HTML 带大量附件
（侧注/弹注/链接书目/反向链接）。策略：
  - sitemap.xml 共 2 万+ 条，绝大多数是 /doc/ 文档存档；只取**顶层随笔**
    （单段路径、无扩展名、非 /doc/）。
  - 正文取 <div id="markdownBody">，剔除导航/书目/反链/侧注等附件，保留正文散文。
  - 日期取 <meta name="dc.date.issued">；按此排 chrono（近似，Gwern 本身非时间序）。
  - 过短/索引页由 is_external 过滤。

用法：
  python -m scrape.scrape_gwern
  python -m scrape.scrape_gwern --limit 5
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

BASE = "https://gwern.net"
SITEMAP = f"{BASE}/sitemap.xml"
_ESSAY_RE = re.compile(r"^https://gwern\.net/[a-z0-9][a-z0-9-]*$")
# markdownBody 内要剔除的附件容器（按 id / class）
_DROP_IDS = ["link-bibliography", "link-bibliography-section", "backlinks",
             "backlinks-section", "similars", "similars-section", "footer",
             "navigation", "page-metadata", "TOC", "noscript-warning",
             "footnotes-section"]
_DROP_CLASSES = ["link-bibliography", "backlinks", "similars", "page-metadata",
                 "aux-links", "collapse-toggle", "footnote-self-link"]


def essay_urls(xml: bytes) -> list[str]:
    root = ET.fromstring(xml)
    urls, seen = [], set()
    for loc in root.iter():
        if loc.tag.endswith("}loc") and loc.text:
            u = loc.text.strip()
            if _ESSAY_RE.match(u) and "/doc/" not in u and u not in seen:
                seen.add(u)
                urls.append(u)
    return urls


def extract(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else (soup.title.get_text(strip=True) if soup.title else "")

    published_at = published_text = None
    meta = soup.select_one('meta[name="dc.date.issued"]') or soup.select_one('meta[name="citation_publication_date"]')
    if meta and meta.get("content"):
        try:
            published_at = datetime.fromisoformat(meta["content"][:10])
            published_text = published_at.strftime("%B %d, %Y")
        except Exception:
            pass

    body = soup.select_one("#markdownBody") or soup.select_one("article")
    content_html = content_text = ""
    if body:
        for t in body.find_all(["script", "style", "nav", "aside", "noscript"]):
            t.decompose()
        for did in _DROP_IDS:
            for el in body.find_all(id=did):
                el.decompose()
        for cls in _DROP_CLASSES:
            for el in body.find_all(class_=cls):
                el.decompose()
        # 顶部的第一个 <h1>（与标题重复）去掉
        first_h1 = body.find("h1")
        if first_h1:
            first_h1.decompose()
        content_html = body.decode_contents().strip()
        content_text = body.get_text("\n", strip=True)
    return dict(title=title, published_at=published_at, published_text=published_text,
                content_html=content_html, content_text=content_text,
                is_external=len(content_text) < 400)   # Gwern 随笔都长，阈值放高滤索引/桩页


def run(limit: int | None = None):
    source_id = db.get_source_id("gwern")
    fetcher = PoliteFetcher(source_id=source_id)
    print(f"抓取 sitemap {SITEMAP} ...")
    urls = essay_urls(fetcher.get(SITEMAP, note="sitemap").content)
    print(f"发现顶层随笔 {len(urls)} 篇")
    if limit:
        urls = urls[:limit]

    out_dir = config.DATA_DIR / "gwern"
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for i, url in enumerate(urls, 1):
        slug = url.rstrip("/").split("/")[-1]
        try:
            resp = fetcher.get(url, note=f"essay {slug}")
        except Exception as e:
            print(f"[{i}/{len(urls)}] !! {slug}: {e}")
            continue
        raw = resp.content.decode("utf-8", "replace")
        p = extract(raw)
        _, changed = db.upsert_article(
            source_id, slug=slug, url=url, title=p["title"], author="Gwern Branwen",
            published_at=p["published_at"], published_text=p["published_text"],
            raw_html=raw[:5_000_000], content_html=p["content_html"],
            content_text=p["content_text"], meta={"slug": slug},
            http_status=resp.status_code, is_external=p["is_external"])
        if changed and not p["is_external"]:
            n += 1
        if i % 25 == 0 or i == len(urls):
            flag = "EXT" if p["is_external"] else "OK"
            print(f"[{i}/{len(urls)}] {flag} {p['published_text'] or '?':16} {p['title'][:42]}")

    total = db.renumber_chrono(source_id)
    print(f"\n完成。可制书文章 {total} 篇；本轮更新 {n}。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(limit=args.limit)
