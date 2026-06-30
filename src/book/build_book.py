"""电子书生成器：从 MySQL 生成 EPUB，并（若有 calibre）转 PDF/MOBI/AZW3。

四本书：
  - paulgraham / en、paulgraham / zh
  - naval / en、naval / zh
文章按 chrono_index 升序（最早在前，最近在最后），按年份分卷形成富目录，
便于电子阅读器跳转与索引。

用法：
  python -m book.build_book --source paulgraham --lang en
  python -m book.build_book --all                       # 生成全部四本
  python -m book.build_book --all --formats epub,azw3,pdf,mobi
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import requests
from ebooklib import epub
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import config, db   # noqa: E402
from book.cover import make_cover   # noqa: E402
from book.mindmap import make_mindmap   # noqa: E402

# 书名与作者
BOOK_META = {
    ("paulgraham", "en"): ("The Paul Graham Essays", "Paul Graham"),
    ("paulgraham", "zh"): ("保罗·格雷厄姆文集", "Paul Graham（保罗·格雷厄姆）"),
    ("naval", "en"): ("The Almanack of Naval — Collected Writings", "Naval Ravikant"),
    ("naval", "zh"): ("纳瓦尔文集", "Naval Ravikant（纳瓦尔·拉维肯特）"),
    ("pmarca", "en"): ("The pmarca Blog Archives", "Marc Andreessen"),
    ("pmarca", "zh"): ("马克·安德森博客文集", "Marc Andreessen（马克·安德森）"),
    ("michaelseibel", "en"): ("Michael Seibel — Essays on Startups", "Michael Seibel"),
    ("michaelseibel", "zh"): ("迈克尔·塞贝尔创业文集", "Michael Seibel（迈克尔·塞贝尔）"),
    ("startupmarketing", "en"): ("Startup Marketing — The Sean Ellis Essays", "Sean Ellis"),
    ("startupmarketing", "zh"): ("增长营销文集", "Sean Ellis（肖恩·埃利斯）"),
    ("a16z", "en"): ("The a16z Show — Transcribed Conversations", "Andreessen Horowitz"),
    ("a16z", "zh"): ("a16z 播客对话录", "Andreessen Horowitz（安德森·霍洛维茨）"),
    ("a16z_ai", "en"): ("AI + a16z — Transcribed Conversations", "Andreessen Horowitz"),
    ("a16z_ai", "zh"): ("AI + a16z 对话录", "Andreessen Horowitz（安德森·霍洛维茨）"),
    ("a16z_crypto", "en"): ("web3 with a16z — Transcribed Conversations", "Andreessen Horowitz"),
    ("a16z_crypto", "zh"): ("web3 与 a16z 对话录", "Andreessen Horowitz（安德森·霍洛维茨）"),
    ("a16z_raising_health", "en"): ("Raising Health — Transcribed Conversations", "Andreessen Horowitz"),
    ("a16z_raising_health", "zh"): ("Raising Health：医疗健康对话录", "Andreessen Horowitz（安德森·霍洛维茨）"),
    ("a16z_live", "en"): ("a16z Live — Transcribed Conversations", "Andreessen Horowitz"),
    ("a16z_live", "zh"): ("a16z Live 现场对话录", "Andreessen Horowitz（安德森·霍洛维茨）"),
    ("a16z_16min", "en"): ("16 Minutes — Tech News by a16z", "Andreessen Horowitz"),
    ("a16z_16min", "zh"): ("16 分钟：a16z 科技新闻速览", "Andreessen Horowitz（安德森·霍洛维茨）"),
    ("a16z_benmarc", "en"): ("The Ben & Marc Show — Transcribed Conversations", "Ben Horowitz & Marc Andreessen"),
    ("a16z_benmarc", "zh"): ("Ben & Marc 对话录", "Ben Horowitz & Marc Andreessen"),
    ("a16z_hotline", "en"): ("a16z Startup Hotline — Transcribed Conversations", "Andreessen Horowitz"),
    ("a16z_hotline", "zh"): ("a16z 创业热线对话录", "Andreessen Horowitz（安德森·霍洛维茨）"),
    ("avc", "en"): ("AVC — The Fred Wilson Blog Archives", "Fred Wilson"),
    ("avc", "zh"): ("AVC：弗雷德·威尔逊博客文集", "Fred Wilson（弗雷德·威尔逊）"),
    ("abovethecrowd", "en"): ("Above the Crowd — The Bill Gurley Essays", "Bill Gurley"),
    ("abovethecrowd", "zh"): ("超越人群：比尔·格利文集", "Bill Gurley（比尔·格利）"),
    ("farnamstreet", "en"): ("Farnam Street — Mental Models & Clear Thinking", "Shane Parrish"),
    ("farnamstreet", "zh"): ("Farnam Street：思维模型与清晰思考", "Shane Parrish（谢恩·帕里什）"),
    ("cdixon", "en"): ("cdixon — Essays on Startups, Crypto & Technology", "Chris Dixon"),
    ("cdixon", "zh"): ("克里斯·迪克森文集", "Chris Dixon（克里斯·迪克森）"),
    ("samaltman", "en"): ("The Sam Altman Blog", "Sam Altman"),
    ("samaltman", "zh"): ("萨姆·奥尔特曼文集", "Sam Altman（萨姆·奥尔特曼）"),
    ("danluu", "en"): ("Dan Luu — Essays on Software & Systems", "Dan Luu"),
    ("danluu", "zh"): ("丹·卢：软件与系统随笔", "Dan Luu（丹·卢）"),
    ("feld", "en"): ("Feld Thoughts — The Brad Feld Blog", "Brad Feld"),
    ("feld", "zh"): ("布拉德·菲尔德博客文集", "Brad Feld（布拉德·菲尔德）"),
    ("firstround", "en"): ("The First Round Review", "First Round Review"),
    ("firstround", "zh"): ("First Round Review 创业方法论", "First Round Review"),
    ("eladgil", "en"): ("Elad Gil — High Growth Essays", "Elad Gil"),
    ("eladgil", "zh"): ("埃拉德·吉尔文集", "Elad Gil（埃拉德·吉尔）"),
    ("gwern", "en"): ("Gwern — Essays & Investigations", "Gwern Branwen"),
    ("gwern", "zh"): ("Gwern 文集：研究与思辨", "Gwern Branwen"),
}

LANG_CODE = {"en": "en", "zh": "zh-CN"}

# 开源与署名
REPO_NAME = "github.com/BillLucky/lumina"
REPO_URL = "https://github.com/BillLucky/lumina"
TRANSLATOR_URL = "https://libiao.ai"
COVER_VARIANT = 2

# 各来源原文入口（封面/版权页展示与跳转）
SOURCE_URL = {
    "paulgraham": "https://paulgraham.com/articles.html",
    "naval": "https://nav.al/archive",
    "pmarca": "https://pmarchive.com/",
    "michaelseibel": "https://www.michaelseibel.com/",
    "startupmarketing": "https://www.startup-marketing.com/",
    "a16z": "https://a16z.com/podcasts/a16z-show/",
    "a16z_ai": "https://a16z.com/podcasts/ai-a16z/",
    "a16z_crypto": "https://a16zcrypto.com/podcast/",
    "a16z_raising_health": "https://a16z.com/podcasts/raising-health/",
    "a16z_live": "https://a16z.com/podcasts/a16z-live/",
    "a16z_16min": "https://a16z.com/podcasts/16-minutes/",
    "a16z_benmarc": "https://a16z.com/podcasts/ben-marc/",
    "a16z_hotline": "https://a16z.com/podcasts/startup-hotline/",
    "avc": "https://avc.com",
    "abovethecrowd": "https://abovethecrowd.com",
    "farnamstreet": "https://fs.blog",
    "cdixon": "https://cdixon.org/archive",
    "samaltman": "https://blog.samaltman.com",
    "danluu": "https://danluu.com",
    "feld": "https://feld.com",
    "firstround": "https://review.firstround.com",
    "eladgil": "https://blog.eladgil.com",
    "gwern": "https://gwern.net",
}

CSS = """
body { font-family: Georgia, 'Songti SC', 'Noto Serif CJK SC', serif; line-height: 1.7;
       margin: 5% 6%; }
h1 { font-size: 1.6em; line-height: 1.3; margin: 0 0 0.2em; }
.meta { color: #666; font-style: italic; margin-bottom: 1.5em; font-size: 0.9em; }
p { margin: 0 0 0.9em; text-align: justify; }
blockquote { border-left: 3px solid #ccc; margin: 1em 0; padding-left: 1em; color: #444; }
a { color: #1a4d8f; text-decoration: none; }
pre, code { font-family: 'SF Mono', Menlo, monospace; font-size: 0.9em;
            background: #f5f5f5; white-space: pre-wrap; }
img { max-width: 100%; height: auto; }
.toc-intro { color:#555; }
.brief { background: #faf8f3; border: 1px solid #e3ddcf;
         border-left: 4px solid #b8923f; border-radius: 6px;
         padding: 0.8em 1em; margin: 0 0 1.6em; }
.brief-h { font-size: 0.78em; letter-spacing: 0.12em; text-transform: uppercase;
           color: #b8923f; font-weight: bold; margin-bottom: 0.4em; }
.brief .thesis { font-weight: bold; font-style: normal; margin: 0 0 0.6em;
                 text-align: left; color: #2a2a2a; }
.brief .mm { text-align: center; margin: 0.4em 0 0; }
.brief .mm img { max-width: 100%; }
"""

STRINGS = {
    "en": {"published": "Published", "source": "Source", "undated": "Undated",
           "intro": "An open, chronologically-ordered collection — earliest essays first, "
                    "most recent last. Built from the author's public writings."},
    "zh": {"published": "发表于", "source": "原文", "undated": "未标注日期",
           "intro": "一部开源、按时间正序编排的文集——最早的文章在前，最近的在最后。"
                    "内容取自作者公开发表的文字，由 MiniMax-M3 模型以「信达雅」译就。"},
}


def fetch_articles(source_key: str, lang: str) -> list[dict]:
    """返回按 chrono_index 升序的文章；zh 取译文，仅含已完成翻译的篇目。"""
    with db.cursor() as cur:
        if lang == "en":
            cur.execute(
                """SELECT a.id,a.slug,a.title,a.url,a.published_at,a.published_text,
                          a.content_html,a.chrono_index
                   FROM articles a JOIN sources s ON s.id=a.source_id
                   WHERE s.source_key=%s AND a.is_external=0
                         AND a.content_html IS NOT NULL AND a.content_html<>''
                   ORDER BY a.chrono_index""", (source_key,))
        else:
            cur.execute(
                """SELECT a.id,a.slug,t.title_translated AS title,a.url,a.published_at,
                          a.published_text,t.content_translated AS content_html,
                          a.chrono_index
                   FROM articles a JOIN sources s ON s.id=a.source_id
                   JOIN translations t ON t.article_id=a.id AND t.target_lang='zh'
                   WHERE s.source_key=%s AND a.is_external=0 AND t.status='done'
                         AND t.content_translated IS NOT NULL AND t.content_translated<>''
                   ORDER BY a.chrono_index""", (source_key,))
        return cur.fetchall()


def fetch_summaries(article_ids: list[int]) -> dict:
    """返回 {article_id: {thesis_en, thesis_zh, points(dict)}}。"""
    if not article_ids:
        return {}
    placeholders = ",".join(["%s"] * len(article_ids))
    with db.cursor() as cur:
        cur.execute(
            f"""SELECT article_id,thesis_en,thesis_zh,points_json
                FROM summaries WHERE status='done' AND article_id IN ({placeholders})""",
            article_ids)
        out = {}
        for r in cur.fetchall():
            try:
                pts = json.loads(r["points_json"]) if r["points_json"] else {}
            except Exception:
                pts = {}
            out[r["article_id"]] = {"thesis_en": r["thesis_en"],
                                    "thesis_zh": r["thesis_zh"], "points": pts}
        return out


def _esc(t: str) -> str:
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _colophon_html(source_key, lang, author) -> str:
    """版权·致谢页：原文来源 / 译者 / 开源仓库（均为可点击链接）+ 非商用声明。"""
    src_url = SOURCE_URL.get(source_key, "")
    site = src_url.replace("https://", "").replace("http://", "").rstrip("/")
    if lang == "zh":
        return (
            "<h1>版权 · 致谢</h1>"
            f"<p><strong>原文作者</strong>　{_esc(author)}</p>"
            f"<p><strong>原文来源</strong>　<a href='{src_url}'>{site}</a>"
            "　（点击可访问作者原站，欢迎前往阅读与学习）</p>"
            f"<p><strong>译者</strong>　<a href='{TRANSLATOR_URL}'>Bill Li（李标）</a>"
            "　·　Opus 4.8　·　MiniMax M3</p>"
            f"<p><strong>开源仓库</strong>　<a href='{REPO_URL}'>{REPO_NAME}</a></p>"
            "<hr/>"
            "<p class='meta'>本书为非商业性质的双语对照与学习用途。原文版权归原作者所有，"
            "本项目仅作个人离线阅读与中英对照之用，不主张任何商业权益；"
            "译文与排版由上述译者与模型协作完成，若有疏漏，文责在我们。"
            "如原作者希望调整或下架，我们将第一时间配合。</p>")
    return (
        "<h1>Colophon</h1>"
        f"<p><strong>Author</strong>　{_esc(author)}</p>"
        f"<p><strong>Source</strong>　<a href='{src_url}'>{site}</a>"
        "　(visit the author's original site to read and learn more)</p>"
        f"<p><strong>Translation</strong>　<a href='{TRANSLATOR_URL}'>Bill Li</a>"
        "　·　Opus 4.8　·　MiniMax M3</p>"
        f"<p><strong>Open source</strong>　<a href='{REPO_URL}'>{REPO_NAME}</a></p>"
        "<hr/>"
        "<p class='meta'>This is a non-commercial bilingual edition for personal study. "
        "Copyright of the original texts remains with their authors; this project claims no "
        "commercial rights. Translation and typesetting were produced with the tools above. "
        "We will promptly comply with any author request to amend or remove this edition.</p>")


IMG_CACHE = config.ROOT / "assets" / "img_cache"
_IMG_RE = re.compile(r"<img\b[^>]*>", re.I)
_SRC_RE = re.compile(r"""\bsrc\s*=\s*["']([^"']+)["']""", re.I)
_IMG_MEDIA = {"jpg": "image/jpeg", "png": "image/png",
              "gif": "image/gif", "webp": "image/webp"}


def _fetch_image(url: str):
    """下载并校验图片，返回 (bytes, ext) 或 None（死链/非图）。带本地缓存 + 死链标记，
    避免每次重建都重下、也避免反复请求已失效的第三方图。"""
    IMG_CACHE.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    if (IMG_CACHE / f"{h}.dead").exists():
        return None
    for f in IMG_CACHE.glob(f"{h}.*"):
        if f.suffix != ".dead":
            return f.read_bytes(), f.suffix.lstrip(".")
    full = "https:" + url if url.startswith("//") else url
    if not full.startswith("http"):
        (IMG_CACHE / f"{h}.dead").touch()
        return None
    try:
        r = requests.get(full, timeout=20,
                         headers={"User-Agent": config.HTTP_USER_AGENT})
        if r.status_code != 200 or not r.content:
            raise ValueError(f"HTTP {r.status_code}")
        im = Image.open(io.BytesIO(r.content))
        im.verify()                                   # 真是图片才接受
        ext = {"jpeg": "jpg"}.get((im.format or "png").lower(), (im.format or "png").lower())
        if ext not in _IMG_MEDIA:
            raise ValueError(f"unsupported {ext}")
        (IMG_CACHE / f"{h}.{ext}").write_bytes(r.content)
        return r.content, ext
    except Exception:
        (IMG_CACHE / f"{h}.dead").touch()
        return None


def _embed_images(html: str, book, idx: int) -> str:
    """把正文里的远程 <img> 下载内嵌进 EPUB 并重写 src；死链/非图直接删标签（不留裂图）。"""
    if "<img" not in (html or ""):
        return html
    n = [0]

    def repl(m):
        sm = _SRC_RE.search(m.group(0))
        if not sm:
            return ""
        got = _fetch_image(sm.group(1).replace("&amp;", "&"))
        if not got:
            return ""
        data, ext = got
        n[0] += 1
        name = f"images/img{idx:04d}_{n[0]}.{ext}"
        book.add_item(epub.EpubImage(
            uid=f"img{idx:04d}_{n[0]}", file_name=name,
            media_type=_IMG_MEDIA[ext], content=data))
        return f"<img src='{name}' alt=''/>"

    return _IMG_RE.sub(repl, html)


def _brief_block(source_key, lang, article, summary, mm_dir, book) -> str:
    """生成「核心导读」卡片 + 思维导图图片，并把图片加入 EPUB。无导读则返回空串。"""
    if not summary:
        return ""
    thesis = summary["thesis_zh"] if lang == "zh" else summary["thesis_en"]
    points = (summary.get("points") or {}).get(lang, [])
    if not thesis and not points:
        return ""
    heading = "核心导读 · In Brief" if lang == "zh" else "In Brief"
    parts = [f"<div class='brief'><div class='brief-h'>{heading}</div>"]
    if thesis:
        parts.append(f"<p class='thesis'>{_esc(thesis)}</p>")

    # 思维导图图片
    if points:
        try:
            central = article["title"]
            fname = f"{source_key}_{lang}_a{article['chrono_index']:04d}.png"
            png = mm_dir / fname
            make_mindmap(source_key, lang, central, points, png)
            img_name = f"images/mm{article['chrono_index']:04d}.png"
            book.add_item(epub.EpubImage(
                uid=f"mm{article['chrono_index']:04d}", file_name=img_name,
                media_type="image/png", content=png.read_bytes()))
            alt = "思维导图" if lang == "zh" else "Mind map"
            parts.append(f"<div class='mm'><img src='{img_name}' alt='{alt}'/></div>")
        except Exception as e:
            print(f"  [warn] 导图渲染失败 {article.get('slug')}: {e}")
    parts.append("</div>")
    return "".join(parts)


def build_epub(source_key: str, lang: str) -> Path | None:
    title, author = BOOK_META[(source_key, lang)]
    arts = fetch_articles(source_key, lang)
    if not arts:
        print(f"  [跳过] {source_key}/{lang}：暂无可用文章")
        return None
    s = STRINGS[lang]

    book = epub.EpubBook()
    book.set_identifier(f"blogbook-{source_key}-{lang}")
    book.set_title(title)
    book.set_language(LANG_CODE[lang])
    book.add_author(author)
    book.add_metadata("DC", "description", s["intro"])

    # 封面：从首尾文章年份取跨度
    years = [a["published_at"].year for a in arts if a["published_at"]]
    span_lbl = f"{min(years)} – {max(years)}" if years else ""
    cover_png = config.ROOT / "assets" / "covers" / f"{source_key}_{lang}.png"
    try:
        make_cover(source_key, lang, title, author, span_lbl, cover_png,
                   repo=REPO_NAME, variant=COVER_VARIANT)
        book.set_cover("cover.png", cover_png.read_bytes())
    except Exception as e:
        print(f"  [warn] 封面生成失败: {e}")

    css = epub.EpubItem(uid="style", file_name="style/main.css",
                        media_type="text/css", content=CSS)
    book.add_item(css)

    # 扉页
    intro = epub.EpubHtml(title=("前言" if lang == "zh" else "Introduction"),
                          file_name="intro.xhtml", lang=LANG_CODE[lang])
    intro.add_item(css)
    span = arts[0]["published_text"] or "?"
    span_end = arts[-1]["published_text"] or "?"
    intro.content = (
        f"<h1>{title}</h1><p class='meta'>{author}</p>"
        f"<p class='toc-intro'>{s['intro']}</p>"
        f"<p class='meta'>{span} – {span_end} · {len(arts)} "
        f"{'篇' if lang=='zh' else 'pieces'}</p>")
    book.add_item(intro)

    # 版权·致谢页（含可点击的原文来源 / 译者 / 开源仓库链接）
    colophon = epub.EpubHtml(title=("版权 · 致谢" if lang == "zh" else "Colophon"),
                             file_name="colophon.xhtml", lang=LANG_CODE[lang])
    colophon.add_item(css)
    colophon.content = _colophon_html(source_key, lang, author)
    book.add_item(colophon)

    # 导读/思维导图数据
    summaries = fetch_summaries([a["id"] for a in arts])
    mm_dir = config.ROOT / "assets" / "mindmaps"

    # 章节 + 按年份分卷的目录
    chapters, toc_sections = [], []
    cur_year, cur_group = None, []
    spine = ["nav", intro, colophon]
    for a in arts:
        c = epub.EpubHtml(title=a["title"], file_name=f"a{a['chrono_index']:04d}.xhtml",
                          lang=LANG_CODE[lang])
        c.add_item(css)
        date_s = a["published_text"] or s["undated"]
        brief_html = _brief_block(source_key, lang, a, summaries.get(a["id"]),
                                  mm_dir, book)
        body_html = _embed_images(a["content_html"], book, a["chrono_index"])
        c.content = (
            f"<h1>{a['title']}</h1>"
            f"<p class='meta'>{s['published']} {date_s} · "
            f"<a href='{a['url']}'>{s['source']}</a></p>"
            f"{brief_html}"
            f"{body_html}")
        book.add_item(c)
        chapters.append(c)
        spine.append(c)

        year = a["published_at"].year if a["published_at"] else None
        if year != cur_year and cur_group:
            toc_sections.append((epub.Section(str(cur_year or s["undated"])), tuple(cur_group)))
            cur_group = []
        cur_year = year
        cur_group.append(c)
    if cur_group:
        toc_sections.append((epub.Section(str(cur_year or s["undated"])), tuple(cur_group)))

    book.toc = (intro, colophon, *toc_sections)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine

    config.OUTPUT_DIR.mkdir(exist_ok=True)
    out = config.OUTPUT_DIR / f"{source_key}_{lang}.epub"
    epub.write_epub(str(out), book)
    print(f"  ✓ EPUB {out.name}（{len(arts)} 篇）")
    _record_build(source_key, lang, "epub", out, len(arts))
    return out


def convert(epub_path: Path, fmt: str) -> Path | None:
    """用 calibre ebook-convert 把 EPUB 转成其它格式。"""
    tool = shutil.which("ebook-convert")
    if not tool:
        print(f"  [跳过 {fmt}] 未找到 ebook-convert（请安装 calibre）")
        return None
    out = epub_path.with_suffix("." + fmt)
    cmd = [tool, str(epub_path), str(out)]
    if fmt == "pdf":
        # PDF 版心：A5 纸 + 较窄页边距(42pt)，正文占满 ~80% 宽；
        # 用 --pdf-page-margin-*（真正生效的选项，默认 72pt 太大），并以 print.css 清零 body 边距
        print_css = Path(__file__).resolve().parent / "print.css"
        cmd += ["--pdf-page-numbers", "--paper-size", "a5",
                "--pdf-default-font-size", "15",
                "--pdf-page-margin-left", "42", "--pdf-page-margin-right", "42",
                "--pdf-page-margin-top", "54", "--pdf-page-margin-bottom", "54",
                "--extra-css", str(print_css)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"  ✓ {fmt.upper()} {out.name}")
        src_key, lang = epub_path.stem.rsplit("_", 1)
        _record_build(src_key, lang, fmt, out, None)
        return out
    except subprocess.CalledProcessError as e:
        print(f"  ✗ {fmt} 转换失败: {e.stderr[-300:] if e.stderr else e}")
        return None


def _record_build(source_key, lang, fmt, path: Path, count):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO book_build (source_id,lang,format,file_path,article_count) "
            "VALUES ((SELECT id FROM sources WHERE source_key=%s),%s,%s,%s,%s)",
            (source_key, lang, fmt, str(path), count))


def run(targets: list[tuple[str, str]], formats: list[str]):
    for source_key, lang in targets:
        print(f"== 生成 {source_key} / {lang} ==")
        epub_path = build_epub(source_key, lang)
        if not epub_path:
            continue
        for fmt in formats:
            if fmt == "epub":
                continue
            convert(epub_path, fmt)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=None, help="paulgraham / naval")
    ap.add_argument("--lang", default=None, help="en / zh")
    ap.add_argument("--all", action="store_true", help="生成全部四本")
    ap.add_argument("--formats", default="epub,azw3,pdf",
                    help="逗号分隔：epub,azw3,mobi,pdf")
    args = ap.parse_args()

    if args.all:
        targets = list(BOOK_META.keys())
    elif args.source and args.lang:
        targets = [(args.source, args.lang)]
    else:
        ap.error("需指定 --all 或同时给出 --source 与 --lang")
    run(targets, [f.strip() for f in args.formats.split(",") if f.strip()])
