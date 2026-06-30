"""Dan Luu（danluu.com，Hugo 静态）文章抓取器。

Hugo RSS（/atom.xml，实为 RSS 2.0）已内嵌全文（<content:encoded> / <description>），
直接解析即可，无需逐页抓取。Patreon 专属篇不在公开 RSS 中，天然排除。

用法：
  python -m scrape.scrape_danluu
  python -m scrape.scrape_danluu --limit 5
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import config, db                  # noqa: E402
from common.http import PoliteFetcher           # noqa: E402

FEED = "https://danluu.com/atom.xml"
BASE = "https://danluu.com"
CE = "{http://purl.org/rss/1.0/modules/content/}encoded"


def parse_feed(xml: bytes) -> list[dict]:
    root = ET.fromstring(xml)
    out = []
    for it in root.findall(".//item"):
        link = (it.findtext("link") or "").strip()
        title = (it.findtext("title") or "").strip()
        pub = it.findtext("pubDate")
        try:
            published_at = parsedate_to_datetime(pub).replace(tzinfo=None) if pub else None
        except Exception:
            published_at = None
        content = it.findtext(CE) or it.findtext("description") or ""
        out.append(dict(link=link, title=title, published_at=published_at, content=content))
    return out


def clean(raw_html: str) -> tuple[str, str]:
    soup = BeautifulSoup(raw_html or "", "lxml")
    for t in soup.find_all(["script", "style"]):
        t.decompose()
    body = soup.body or soup
    return body.decode_contents().strip(), body.get_text("\n", strip=True)


def run(limit: int | None = None):
    source_id = db.get_source_id("danluu")
    fetcher = PoliteFetcher(source_id=source_id)
    print(f"抓取 RSS {FEED} ...")
    items = parse_feed(fetcher.get(FEED, note="feed").content)
    print(f"发现 {len(items)} 篇")
    if limit:
        items = items[:limit]

    out_dir = config.DATA_DIR / "danluu"
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for i, it in enumerate(items, 1):
        slug = (it["link"].rstrip("/").split("/")[-1] or f"post{i}")
        content_html, content_text = clean(it["content"])
        published_text = it["published_at"].strftime("%B %d, %Y") if it["published_at"] else None
        _, changed = db.upsert_article(
            source_id, slug=slug, url=it["link"] or f"{BASE}/{slug}.html",
            title=it["title"], author="Dan Luu",
            published_at=it["published_at"], published_text=published_text,
            raw_html=it["content"], content_html=content_html, content_text=content_text,
            meta={"slug": slug}, http_status=200, is_external=len(content_text) < 200)
        if changed and len(content_text) >= 200:
            n += 1
        flag = "EXT" if len(content_text) < 200 else ("NEW" if changed else "=")
        print(f"[{i}/{len(items)}] {flag:3} {published_text or '?':16} {it['title'][:48]}")

    total = db.renumber_chrono(source_id)
    print(f"\n完成。可制书文章 {total} 篇；本轮更新 {n}。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(limit=args.limit)
