"""WordPress REST API 通用抓取逻辑，供 nav.al / startup-marketing.com 等 WP 站复用。"""
from __future__ import annotations

import html as html_lib
import json
import re
from datetime import datetime

from bs4 import BeautifulSoup

from common import config, db
from common.http import PoliteFetcher

PER_PAGE = 100


def clean_content(raw_html: str) -> tuple[str, str]:
    soup = BeautifulSoup(raw_html or "", "lxml")
    for tag in soup.find_all(["script", "style", "ins"]):
        tag.decompose()
    for sel in ["sharedaddy", "jp-relatedposts", "wp-block-buttons",
                "wp-embed", "addtoany", "sharing"]:
        for el in soup.find_all(class_=re.compile(sel, re.I)):
            el.decompose()
    body = soup.body or soup
    return body.decode_contents().strip(), body.get_text("\n", strip=True)


def fetch_all_posts(fetcher: PoliteFetcher, api: str) -> list[dict]:
    posts, page = [], 1
    while True:
        url = f"{api}?per_page={PER_PAGE}&page={page}&orderby=date&order=asc"
        resp = fetcher.get(url, as_json=True, note=f"posts page {page}")
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break
        posts.extend(batch)
        total_pages = int(resp.headers.get("X-WP-TotalPages", "1"))
        print(f"  第 {page}/{total_pages} 页：累计 {len(posts)} 篇")
        if page >= total_pages:
            break
        page += 1
    return posts


def scrape_wp(source_key: str, base: str, author: str, data_subdir: str,
              limit: int | None = None):
    source_id = db.get_source_id(source_key)
    fetcher = PoliteFetcher(source_id=source_id)
    api = f"{base}/wp-json/wp/v2/posts"

    print(f"抓取 WP API {api} ...")
    posts = fetch_all_posts(fetcher, api)
    print(f"共获取 {len(posts)} 篇")
    if limit:
        posts = posts[:limit]

    out_dir = config.DATA_DIR / data_subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    n_changed = 0
    for i, p in enumerate(posts, 1):
        slug = p.get("slug") or str(p.get("id"))
        url = p.get("link") or f"{base}/{slug}"
        title = html_lib.unescape(
            BeautifulSoup(p.get("title", {}).get("rendered", ""), "lxml").get_text())
        content_html, content_text = clean_content(p.get("content", {}).get("rendered", ""))
        try:
            published_at = datetime.fromisoformat(p["date"])
        except Exception:
            published_at = None
        published_text = published_at.strftime("%B %Y") if published_at else None
        meta = {"wp_id": p.get("id"), "categories": p.get("categories"),
                "tags": p.get("tags"), "modified": p.get("modified")}
        raw_json = json.dumps(p, ensure_ascii=False)
        db.upsert_article(
            source_id, slug=slug, url=url, title=title, author=author,
            published_at=published_at, published_text=published_text,
            raw_html=raw_json, content_html=content_html, content_text=content_text,
            meta=meta, http_status=200, is_external=(len(content_text) < 80))
        (out_dir / f"{slug}.json").write_text(raw_json, encoding="utf-8")
        if len(content_text) >= 80:
            n_changed += 1
        flag = "EXT" if len(content_text) < 80 else "OK"
        print(f"[{i}/{len(posts)}] {flag:3} {slug[:30]:30} {published_text or '?':14} {title[:40]}")

    total = db.renumber_chrono(source_id)
    print(f"\n完成。可制书文章 {total} 篇。")
