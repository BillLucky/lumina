"""Michael Seibel 文章抓取器（michaelseibel.com，Strikingly 托管）。

站点由 Strikingly 渲染，博客正文以内嵌 JSON 的 `Blog.Text` 组件形式存在：
  - text_type=heading 的为标题
  - text_type=body   的为正文段（每段是一小段 HTML，unicode 转义）
发布日期在 `publishedAt`（ISO）。文章 URL 取自 sitemap.xml 的 /blog/ 路径。

用法：
  python -m scrape.scrape_michaelseibel
  python -m scrape.scrape_michaelseibel --limit 3
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

BASE = "https://www.michaelseibel.com"
SITEMAP = f"{BASE}/sitemap.xml"

_PUBLISHED = re.compile(r'"publishedAt":"(\d{4}-\d{2}-\d{2}T[^"]+)"')
_TITLE_SUFFIX = re.compile(r"\s*[|–-]\s*Michael Seibel\s*$", re.IGNORECASE)


def post_urls_from_sitemap(xml: str) -> list[str]:
    locs = re.findall(r"<loc>([^<]+)</loc>", xml)
    out, seen = [], set()
    for u in locs:
        if "/blog/" not in u:
            continue
        if "/categories" in u or u.rstrip("/").endswith("/blog"):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def extract(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    # 标题：<title> 去掉站名后缀
    title = soup.title.get_text(strip=True) if soup.title else url.rsplit("/", 1)[-1]
    title = _TITLE_SUFFIX.sub("", title).strip()

    # 正文：Strikingly 渲染后的 .s-blog-content 容器（新旧两种存储格式都适用）
    container = soup.select_one(".s-blog-content") or soup.select_one("[class*=blog-content]")
    paras = []
    if container:
        for el in container.find_all(["p", "h2", "h3", "ul", "ol", "blockquote", "pre"]):
            inner = el.decode_contents().strip()
            if inner:
                tag = el.name if el.name in ("h2", "h3", "ul", "ol", "blockquote", "pre") else "p"
                paras.append(f"<{tag}>{inner}</{tag}>")
        if not paras:  # 没有块级标签，退化为整体文本
            t = container.get_text("\n", strip=True)
            paras = [f"<p>{p}</p>" for p in t.split("\n") if p.strip()]
    content_html = "\n".join(paras)
    content_text = BeautifulSoup(content_html, "lxml").get_text("\n", strip=True)

    published_at = published_text = None
    # 取该文发表日期：优先 T00:00:00 的 publishedAt（真正的发布日）
    dates = _PUBLISHED.findall(html)
    if dates:
        pick = next((d for d in dates if "T00:00:00" in d), dates[0])
        try:
            published_at = datetime.fromisoformat(pick)
            published_text = published_at.strftime("%B %d, %Y")
        except Exception:
            pass

    return dict(title=title, published_at=published_at, published_text=published_text,
                content_html=content_html, content_text=content_text,
                is_external=len(content_text) < 150)


def run(limit: int | None = None):
    source_id = db.get_source_id("michaelseibel")
    fetcher = PoliteFetcher(source_id=source_id)

    print(f"抓取 sitemap {SITEMAP} ...")
    xml = fetcher.get(SITEMAP, note="sitemap").content.decode("utf-8", "replace")
    urls = post_urls_from_sitemap(xml)
    print(f"发现 {len(urls)} 篇博客文章")
    if limit:
        urls = urls[:limit]

    n_changed = 0
    for i, url in enumerate(urls, 1):
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        try:
            resp = fetcher.get(url, note=f"post {slug}")
        except Exception as e:
            print(f"[{i}/{len(urls)}] !! {slug}: {e}")
            continue
        raw_html = resp.content.decode("utf-8", "replace")
        p = extract(raw_html, url)
        db.upsert_article(
            source_id, slug=slug, url=url, title=p["title"], author="Michael Seibel",
            published_at=p["published_at"], published_text=p["published_text"],
            raw_html=raw_html, content_html=p["content_html"],
            content_text=p["content_text"], meta={"slug": slug},
            http_status=resp.status_code, is_external=p["is_external"])
        (config.DATA_DIR / "michaelseibel" / f"{slug}.html").write_text(raw_html, encoding="utf-8")
        if not p["is_external"]:
            n_changed += 1
        flag = "EXT" if p["is_external"] else "OK"
        print(f"[{i}/{len(urls)}] {flag:3} {slug[:38]:38} {p['published_text'] or '?':16} {p['title'][:34]}")

    total = db.renumber_chrono(source_id)
    print(f"\n完成。可制书文章 {total} 篇。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(limit=args.limit)
