"""Elad Gil（blog.eladgil.com，Substack）文章抓取器。

走 Substack 公开 API：
  - 列表：/api/v1/archive?sort=new&limit=50&offset=N （分页拿全部 post 元数据）
  - 单篇：/api/v1/posts/<slug> （拿 body_html 全文）
全文免费、不截断。付费专属/纯播客篇 body 可能为空，由 is_external 过滤。
（Substack 通用逻辑，未来加别的 Substack 源时可抽成 substack_common。）

用法：
  python -m scrape.scrape_eladgil
  python -m scrape.scrape_eladgil --limit 5
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import config, db                  # noqa: E402
from common.http import PoliteFetcher           # noqa: E402

BASE = "https://blog.eladgil.com"
AUTHOR = "Elad Gil"
SOURCE_KEY = "eladgil"


def list_slugs(fetcher) -> list[str]:
    """从 sitemap.xml 取全部 /p/<slug>（权威全量；archive API 分页不可靠会漏）。"""
    xml = fetcher.get(f"{BASE}/sitemap.xml", note="sitemap").content.decode("utf-8", "replace")
    seen, out = set(), []
    for s in re.findall(r"/p/([a-z0-9][a-z0-9-]*)", xml):
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def clean(raw_html: str) -> tuple[str, str]:
    soup = BeautifulSoup(raw_html or "", "lxml")
    for t in soup.find_all(["script", "style"]):
        t.decompose()
    body = soup.body or soup
    return body.decode_contents().strip(), body.get_text("\n", strip=True)


def run(limit: int | None = None):
    source_id = db.get_source_id(SOURCE_KEY)
    fetcher = PoliteFetcher(source_id=source_id)
    print(f"列出 Substack 文章 {BASE} ...")
    slugs = list_slugs(fetcher)
    print(f"发现 {len(slugs)} 篇")
    if limit:
        slugs = slugs[:limit]

    out_dir = config.DATA_DIR / SOURCE_KEY
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for i, slug in enumerate(slugs, 1):
        try:
            full = fetcher.get(f"{BASE}/api/v1/posts/{slug}", as_json=True,
                               note=f"post {slug}").json()
        except Exception as e:
            print(f"[{i}/{len(slugs)}] !! {slug}: {e}")
            continue
        body_html = full.get("body_html") or ""
        content_html, content_text = clean(body_html)
        title = full.get("title") or slug
        pd = full.get("post_date")
        try:
            published_at = datetime.fromisoformat(pd.replace("Z", "+00:00")).replace(tzinfo=None) if pd else None
        except Exception:
            published_at = None
        published_text = published_at.strftime("%B %d, %Y") if published_at else None
        url = full.get("canonical_url") or f"{BASE}/p/{slug}"
        _, changed = db.upsert_article(
            source_id, slug=slug, url=url, title=title, author=AUTHOR,
            published_at=published_at, published_text=published_text,
            raw_html=json.dumps(full, ensure_ascii=False)[:5_000_000],
            content_html=content_html, content_text=content_text,
            meta={"slug": slug, "audience": full.get("audience")},
            http_status=200, is_external=len(content_text) < 200)
        if changed and len(content_text) >= 200:
            n += 1
        flag = "EXT" if len(content_text) < 200 else ("NEW" if changed else "=")
        print(f"[{i}/{len(slugs)}] {flag:3} {published_text or '?':16} {title[:48]}")

    total = db.renumber_chrono(source_id)
    print(f"\n完成。可制书文章 {total} 篇；本轮更新 {n}。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(limit=args.limit)
