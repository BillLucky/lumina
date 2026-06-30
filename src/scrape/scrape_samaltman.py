"""Sam Altman（blog.samaltman.com，Posthaven）文章抓取器。

Posthaven：首页分页 ?page=N 列出全部文章链接（slug 或数字 id）；正文在
<div class="posthaven-post-body">，标题 <div class="post-title">，日期 <div class="post-date">。
atom feed（/posts.atom，仅近 30 篇）提供权威 ISO 日期，作为日期兜底来源。

用法：
  python -m scrape.scrape_samaltman
  python -m scrape.scrape_samaltman --limit 5
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
from scrape.dates import parse_full_date        # noqa: E402

BASE = "https://blog.samaltman.com"
_URL_RE = re.compile(r"^https?://blog\.samaltman\.com/([a-z0-9][a-z0-9\-]*)/?$", re.I)
_SKIP = {"archive", "about", "feed", "posts", "rss"}


def collect_urls(fetcher) -> list[tuple[str, str]]:
    """分页枚举全部文章 (slug, url)，去重保序，直到某页无新链接。"""
    urls, seen, page = [], set(), 1
    while page <= 40:
        html = fetcher.get(f"{BASE}/?page={page}", note=f"page {page}").content.decode("utf-8", "replace")
        soup = BeautifulSoup(html, "lxml")
        found = 0
        for a in soup.find_all("a", href=True):
            m = _URL_RE.match(a["href"].split("#")[0].split("?")[0])
            if not m:
                continue
            slug = m.group(1).lower()
            if slug in _SKIP or slug in seen:
                continue
            seen.add(slug)
            urls.append((slug, f"{BASE}/{m.group(1)}"))
            found += 1
        if found == 0:
            break
        page += 1
    return urls


def atom_dates(fetcher) -> dict:
    """从 atom feed 取 {slug: datetime}（近 30 篇权威 ISO 日期）。"""
    out = {}
    try:
        xml = fetcher.get(f"{BASE}/posts.atom", note="atom").content
        root = ET.fromstring(xml)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for e in root.findall("a:entry", ns):
            link = e.find("a:link", ns)
            pub = e.findtext("a:published", default="", namespaces=ns)
            href = link.get("href") if link is not None else ""
            m = _URL_RE.match((href or "").split("?")[0])
            if m and pub:
                try:
                    out[m.group(1).lower()] = datetime.fromisoformat(
                        pub.replace("Z", "+00:00")).replace(tzinfo=None)
                except Exception:
                    pass
    except Exception as e:
        print(f"  [warn] atom 取日期失败: {e}")
    return out


def extract(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    art = soup.select_one("article.post") or soup.body or soup
    tnode = art.select_one(".post-title")
    title = tnode.get_text(" ", strip=True) if tnode else (
        soup.title.get_text(strip=True) if soup.title else "")
    body = art.select_one(".posthaven-post-body") or art.select_one(".post-body")
    dnode = art.select_one(".post-date")
    published_at = published_text = None
    if dnode:
        published_at, published_text = parse_full_date(dnode.get_text(" ", strip=True))
    content_html = body.decode_contents().strip() if body else ""
    content_text = body.get_text("\n", strip=True) if body else ""
    return dict(title=title, published_at=published_at, published_text=published_text,
                content_html=content_html, content_text=content_text,
                is_external=len(content_text) < 120)


def run(limit: int | None = None):
    source_id = db.get_source_id("samaltman")
    fetcher = PoliteFetcher(source_id=source_id)

    print(f"枚举文章 {BASE} ...")
    urls = collect_urls(fetcher)
    print(f"发现 {len(urls)} 篇文章")
    dates = atom_dates(fetcher)
    if limit:
        urls = urls[:limit]

    out_dir = config.DATA_DIR / "samaltman"
    out_dir.mkdir(parents=True, exist_ok=True)
    n_changed = 0
    for i, (slug, url) in enumerate(urls, 1):
        try:
            resp = fetcher.get(url, note=f"article {slug}")
        except Exception as e:
            print(f"[{i}/{len(urls)}] !! {slug}: {e}")
            continue
        raw_html = resp.content.decode("utf-8", "replace")
        p = extract(raw_html)
        # 日期：优先正文页解析，缺失则用 atom 权威 ISO 日期兜底
        published_at = p["published_at"] or dates.get(slug)
        published_text = p["published_text"] or (
            published_at.strftime("%B %d, %Y") if published_at else None)
        _, changed = db.upsert_article(
            source_id, slug=slug, url=url, title=p["title"], author="Sam Altman",
            published_at=published_at, published_text=published_text,
            raw_html=raw_html, content_html=p["content_html"],
            content_text=p["content_text"], meta={"slug": slug},
            http_status=resp.status_code, is_external=p["is_external"])
        (out_dir / f"{slug}.html").write_text(raw_html, encoding="utf-8")
        if changed and not p["is_external"]:
            n_changed += 1
        flag = "EXT" if p["is_external"] else ("NEW" if changed else "=")
        print(f"[{i}/{len(urls)}] {flag:3} {published_text or '?':16} {p['title'][:46]}")

    total = db.renumber_chrono(source_id)
    print(f"\n完成。可制书文章 {total} 篇；本轮内容更新 {n_changed}。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(limit=args.limit)
