"""整理 output/ 目录 + 生成统一仪表盘 output/INDEX.md。

做两件事（可反复运行；watchdog 每轮会自动调用，保持整洁+最新）：
  1. 归类：把平铺在 output/ 的书按来源移进 output/books/<source>/；
     把文稿（.md/.pptx/.docx 等）移进 output/docs/。
     （只移动 20 秒内未改动的文件，避免动到正在写的成品。）
  2. 仪表盘：扫描 books/ + 查 DB，生成 output/INDEX.md：
     每个来源的中文书名、文章数、译文数、导读数、已出格式（en/zh），分「文集 / 播客」两组。

用法：
  PYTHONPATH=src .venv/bin/python scripts/build_index.py
"""
from __future__ import annotations

import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]) + "/src")
from common import config, db                       # noqa: E402
from book.build_book import BOOK_META               # noqa: E402

OUT = config.OUTPUT_DIR
BOOK_FMTS = ("epub", "pdf", "azw3", "mobi")
DOC_EXTS = (".md", ".pptx", ".docx", ".key", ".pdf")   # 顶层散落的文稿（.pdf 仅顶层，书的 pdf 在 books/ 里）
MIN_AGE = 20    # 秒；更年轻的文件可能正在写，跳过移动


def _src_lang(stem: str):
    """'a16z_hotline_zh' -> ('a16z_hotline','zh')；非法返回 None。"""
    if "_" not in stem:
        return None
    src, lang = stem.rsplit("_", 1)
    return (src, lang) if lang in ("en", "zh") and src else None


def organize():
    books_dir = OUT / "books"
    docs_dir = OUT / "docs"
    now = time.time()
    moved_b = moved_d = 0
    for f in OUT.iterdir():
        if not f.is_file():
            continue
        if now - f.stat().st_mtime < MIN_AGE:
            continue                                 # 可能正在写，下轮再说
        ext = f.suffix.lower().lstrip(".")
        if ext in BOOK_FMTS:                          # 书 → books/<source>/
            sl = _src_lang(f.stem)
            if not sl:
                continue
            dst = books_dir / sl[0]
            dst.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dst / f.name))
            moved_b += 1
        elif f.suffix.lower() in DOC_EXTS and f.name != "INDEX.md":
            docs_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(docs_dir / f.name))
            moved_d += 1
    return moved_b, moved_d


def _counts():
    """source_key -> dict(arts, trans, summ)。"""
    out = {}
    with db.cursor() as c:
        c.execute("""SELECT s.source_key,
               COUNT(DISTINCT CASE WHEN a.is_external=0 THEN a.id END) arts,
               SUM(CASE WHEN t.status='done' THEN 1 ELSE 0 END) trans,
               COUNT(DISTINCT su.id) summ
           FROM sources s
           LEFT JOIN articles a ON a.source_id=s.id
           LEFT JOIN translations t ON t.article_id=a.id AND t.target_lang='zh'
           LEFT JOIN summaries su ON su.article_id=a.id
           GROUP BY s.id""")
        for r in c.fetchall():
            out[r["source_key"]] = dict(arts=r["arts"] or 0, trans=r["trans"] or 0,
                                        summ=r["summ"] or 0)
    return out


def _fmts(source_key: str, lang: str) -> str:
    d = OUT / "books" / source_key
    have = [fmt for fmt in BOOK_FMTS if (d / f"{source_key}_{lang}.{fmt}").exists()]
    return "·".join(have) if have else "—"


def build_index():
    counts = _counts()
    # 所有出现在 BOOK_META 里的来源
    keys = sorted({k for (k, _) in BOOK_META})
    podcasts = [k for k in keys if k == "a16z" or k.startswith("a16z_")]
    essays = [k for k in keys if k not in podcasts]

    def row(k):
        zh_title = BOOK_META.get((k, "zh"), ("", ""))[0]
        c = counts.get(k, dict(arts=0, trans=0, summ=0))
        return (f"| `{k}` | {zh_title} | {c['arts']} | {c['trans']} | {c['summ']} "
                f"| {_fmts(k,'en')} | {_fmts(k,'zh')} |")

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"# lumina 书库总览 · INDEX",
         "",
         f"> 自动生成于 {ts}（watchdog 每数分钟刷新一次）。书在 `books/<来源>/`，文稿在 `docs/`。",
         "",
         "## 📚 博客 / 文集",
         "",
         "| 来源 | 中文书名 | 文章 | 译文(zh) | 导读 | EN 格式 | ZH 格式 |",
         "|---|---|--:|--:|--:|---|---|"]
    L += [row(k) for k in essays]
    L += ["", "## 🎧 播客（a16z 全系列）", "",
          "| 来源 | 中文书名 | 集数 | 译文(zh) | 导读 | EN 格式 | ZH 格式 |",
          "|---|---|--:|--:|--:|---|---|"]
    L += [row(k) for k in podcasts]

    # 文稿清单
    docs_dir = OUT / "docs"
    L += ["", "## 📝 文稿 / 分享材料（`docs/`）", ""]
    if docs_dir.exists():
        for f in sorted(docs_dir.iterdir()):
            if f.is_file():
                L.append(f"- [{f.name}](docs/{f.name})")
    else:
        L.append("（暂无）")

    # 汇总
    tot_arts = sum(c["arts"] for c in counts.values())
    tot_trans = sum(c["trans"] for c in counts.values())
    built = sum(1 for k in keys for lang in ("en", "zh") if _fmts(k, lang) != "—")
    L += ["", "---",
          f"**汇总**：{len(keys)} 个来源 · 文章/集 {tot_arts} · 中文译文 {tot_trans} · 已出书 {built} 本（en+zh 计为 2 本）。"]

    (OUT / "INDEX.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    OUT.mkdir(exist_ok=True)
    mb, md = organize()
    build_index()
    print(f"整理完成：移动书 {mb}、文稿 {md}；已生成 output/INDEX.md")


if __name__ == "__main__":
    main()
