"""Paul Graham 文章抓取器（paulgraham.com，静态 HTML 老站）。

流程：
  1. 抓 articles.html，解析出全部 essay 链接(slug + 标题)
  2. 逐篇抓取（礼貌限速），从 <font> 主容器提取：日期、正文 HTML、纯文本
  3. 原始网页 + 解析结果写入 MySQL（upsert，支持增量）
  4. 重排 chrono_index（最早在前）

用法：
  python -m scrape.scrape_paulgraham            # 全量
  python -m scrape.scrape_paulgraham --limit 5  # 只抓前 5 篇(调试)
  python -m scrape.scrape_paulgraham --only greatwork.html
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import config, db                      # noqa: E402
from common.http import PoliteFetcher               # noqa: E402
from scrape.dates import parse_leading_date         # noqa: E402

BASE = "https://paulgraham.com"
INDEX_URL = f"{BASE}/articles.html"

# 左侧导航/非文章链接，需从候选中剔除
NAV = {
    "index.html", "articles.html", "books.html", "arc.html", "bel.html",
    "lisp.html", "antispam.html", "faq.html", "raq.html", "quo.html",
    "rss.html", "bio.html", "kedrosky.html",
}


def parse_index(html: str) -> list[tuple[str, str]]:
    """返回 [(slug.html, title)]，按页面出现顺序（即官方倒序，最新在前）。"""
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not re.match(r"^[a-zA-Z0-9_]+\.html$", href):
            continue
        if href in NAV or href in seen:
            continue
        title = a.get_text(strip=True)
        if not title:
            continue
        seen.add(href)
        out.append((href, title))
    return out


def _main_container(soup: BeautifulSoup):
    """PG 正文位于文字最多的 <font> 容器。"""
    fonts = soup.find_all("font")
    if not fonts:
        return None
    return max(fonts, key=lambda f: len(f.get_text(" ", strip=True)))


def _html_to_paragraphs(container) -> str:
    """把 <br><br> 分隔的 PG 正文整理成 <p> 段落，保留内联 <a>/<i> 等。"""
    inner = container.decode_contents()
    # 统一 <br> 形态，按双换行切段
    inner = re.sub(r"<br\s*/?>", "<br/>", inner, flags=re.IGNORECASE)
    parts = re.split(r"(?:<br/>\s*){2,}", inner)
    paras = []
    for p in parts:
        p = p.strip()
        # 去掉段内残留的单 <br/>（PG 用它做软换行）
        p = re.sub(r"<br/>", " ", p).strip()
        if p:
            paras.append(f"<p>{p}</p>")
    return "\n".join(paras)


def extract_essay(slug: str, title_hint: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    # 标题：优先 <title>
    title = title_hint
    if soup.title and soup.title.get_text(strip=True):
        title = soup.title.get_text(strip=True)

    container = _main_container(soup)
    if container is None:
        return dict(title=title, published_at=None, published_text=None,
                    content_html="", content_text="", is_external=True)

    content_text = container.get_text("\n", strip=True)
    published_at, matched = parse_leading_date(content_text)
    published_text = matched

    content_html = _html_to_paragraphs(container)
    # 从正文文本/HTML 开头剥离日期行，避免重复出现在书里
    if matched:
        content_html = re.sub(
            r"^<p>\s*" + re.escape(matched) + r"\s*", "<p>", content_html, count=1)
        content_text = content_text.replace(matched, "", 1).lstrip("\n ")
    # 清掉空段落（日期剥离后可能残留 <p></p>）
    content_html = re.sub(r"<p>\s*</p>\s*", "", content_html).strip()

    # 图片/PDF-only 的老文章正文极短，标记为 external 以便制书时跳过
    is_external = len(content_text) < 200
    return dict(title=title, published_at=published_at, published_text=published_text,
                content_html=content_html, content_text=content_text,
                is_external=is_external)


def run(limit: int | None = None, only: str | None = None):
    source_id = db.get_source_id("paulgraham")
    fetcher = PoliteFetcher(source_id=source_id)

    print(f"抓取索引 {INDEX_URL} ...")
    index_html = fetcher.get(INDEX_URL, note="index").text
    essays = parse_index(index_html)
    print(f"发现 {len(essays)} 篇文章候选")

    if only:
        essays = [(h, t) for h, t in essays if h == only]
    if limit:
        essays = essays[:limit]

    n_new = n_changed = n_ext = 0
    for i, (href, title_hint) in enumerate(essays, 1):
        slug = href[:-5]  # 去掉 .html
        url = f"{BASE}/{href}"
        try:
            resp = fetcher.get(url, note=f"essay {slug}")
        except Exception as e:
            print(f"[{i}/{len(essays)}] !! {slug}: {e}")
            continue
        raw_html = resp.text
        parsed = extract_essay(slug, title_hint, raw_html)
        aid, changed = db.upsert_article(
            source_id, slug=slug, url=url, title=parsed["title"], author="Paul Graham",
            published_at=parsed["published_at"], published_text=parsed["published_text"],
            raw_html=raw_html, content_html=parsed["content_html"],
            content_text=parsed["content_text"], meta={"href": href},
            http_status=resp.status_code, is_external=parsed["is_external"])
        # 备份原始网页到磁盘
        (config.DATA_DIR / "paulgraham" / f"{slug}.html").write_text(raw_html, encoding="utf-8")
        flag = "EXT" if parsed["is_external"] else ("NEW" if changed else "=")
        if changed and not parsed["is_external"]:
            n_changed += 1
        if parsed["is_external"]:
            n_ext += 1
        date_s = parsed["published_text"] or "?"
        print(f"[{i}/{len(essays)}] {flag:3} {slug:22} {date_s:14} {parsed['title'][:48]}")

    total = db.renumber_chrono(source_id)
    print(f"\n完成。可制书文章 {total} 篇；本轮内容更新 {n_changed}，跳过(站外/空) {n_ext}。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", type=str, default=None)
    args = ap.parse_args()
    run(limit=args.limit, only=args.only)
