"""Naval Ravikant 文章抓取器（nav.al，WordPress）。

走 WP REST API（/wp-json/wp/v2/posts），一次拿到结构化的标题/正文/日期/分类/标签，
比解析 HTML 稳健得多。分页抓取（per_page=100），礼貌限速。

用法：
  python -m scrape.scrape_naval
  python -m scrape.scrape_naval --limit 5
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import config, db                  # noqa: E402
from common.http import PoliteFetcher           # noqa: E402

BASE = "https://nav.al"
API = f"{BASE}/wp-json/wp/v2/posts"
PER_PAGE = 100


def clean_content(raw_html: str) -> tuple[str, str]:
    """清洗 WP 正文：去掉脚本/分享/嵌入噪声，返回 (content_html, content_text)。"""
    soup = BeautifulSoup(raw_html or "", "lxml")
    for tag in soup.find_all(["script", "style", "ins"]):
        tag.decompose()
    # 去掉 WP / Jetpack 分享、相关文章等容器
    for sel in ["sharedaddy", "jp-relatedposts", "wp-block-buttons",
                "wp-embed", "addtoany"]:
        for el in soup.find_all(class_=re.compile(sel, re.I)):
            el.decompose()
    body = soup.body or soup
    content_html = body.decode_contents().strip()
    content_text = body.get_text("\n", strip=True)
    return content_html, content_text


def fetch_all_posts(fetcher: PoliteFetcher) -> list[dict]:
    posts, page = [], 1
    while True:
        url = f"{API}?per_page={PER_PAGE}&page={page}&orderby=date&order=asc&_embed=0"
        resp = fetcher.get(url, as_json=True, note=f"posts page {page}")
        batch = resp.json()
        if not batch:
            break
        posts.extend(batch)
        total_pages = int(resp.headers.get("X-WP-TotalPages", "1"))
        print(f"  第 {page}/{total_pages} 页：累计 {len(posts)} 篇")
        if page >= total_pages:
            break
        page += 1
    return posts


def run(limit: int | None = None):
    source_id = db.get_source_id("naval")
    fetcher = PoliteFetcher(source_id=source_id)

    print(f"抓取 WP API {API} ...")
    posts = fetch_all_posts(fetcher)
    print(f"共获取 {len(posts)} 篇")
    if limit:
        posts = posts[:limit]

    n_changed = 0
    for i, p in enumerate(posts, 1):
        slug = p.get("slug") or str(p.get("id"))
        url = p.get("link") or f"{BASE}/{slug}"
        title = html_lib.unescape(
            BeautifulSoup(p.get("title", {}).get("rendered", ""), "lxml").get_text())
        raw_content = p.get("content", {}).get("rendered", "")
        content_html, content_text = clean_content(raw_content)
        try:
            published_at = datetime.fromisoformat(p["date"])
        except Exception:
            published_at = None
        published_text = published_at.strftime("%B %Y") if published_at else None
        meta = {
            "wp_id": p.get("id"),
            "categories": p.get("categories"),
            "tags": p.get("tags"),
            "modified": p.get("modified"),
        }
        # raw_html 存完整 WP API JSON，便于将来重新解析
        raw_json = json.dumps(p, ensure_ascii=False)
        aid, changed = db.upsert_article(
            source_id, slug=slug, url=url, title=title, author="Naval Ravikant",
            published_at=published_at, published_text=published_text,
            raw_html=raw_json, content_html=content_html, content_text=content_text,
            meta=meta, http_status=200, is_external=(len(content_text) < 50))
        (config.DATA_DIR / "naval" / f"{slug}.json").write_text(raw_json, encoding="utf-8")
        if changed:
            n_changed += 1
        flag = "NEW" if changed else "="
        print(f"[{i}/{len(posts)}] {flag:3} {slug[:28]:28} {published_text or '?':14} {title[:42]}")

    total = db.renumber_chrono(source_id)
    print(f"\n完成。可制书文章 {total} 篇；本轮内容更新 {n_changed}。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(limit=args.limit)
