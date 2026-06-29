<!-- Language: **English** · [中文](./README.zh.md) -->

# lumina

> Turn the blogs and podcasts of great thinkers into beautiful, **bilingual** (English + Chinese) open e-books — every piece with a cover, an AI-distilled **key‑idea brief + mind map**, chronological ordering, and a rich table of contents.

`lumina` is an end‑to‑end pipeline: **scrape → store in MySQL → translate (faithful, expressive, elegant) → distill a brief + mind map → typeset into EPUB / PDF / MOBI / AZW3.** It is designed to be multi‑source — adding a new author is one scraper plus one row in a table.

> **On the books themselves:** the generated e‑books are **not** committed to this repo. The original texts belong to their authors; this project is a non‑commercial, study‑oriented bilingual rendering. If you'd like a copy of a book to read, leave a comment and we'll share it.

## What it produces

For each source, two books (English original + Chinese translation), each in four formats:

| Source | Site | Pieces |
|---|---|---|
| Paul Graham | paulgraham.com | 231 |
| Naval Ravikant | nav.al | 233 |
| Marc Andreessen (pmarca) | pmarchive.com | 31 |
| Michael Seibel | michaelseibel.com | 18 |
| Sean Ellis | startup-marketing.com | 148 |
| The a16z Show (podcast) | a16z.com | 5 |

Every chapter opens with a **核心导读 / In Brief** card (one‑sentence thesis + 3–6 key points) and a rendered **mind map**, followed by the full text — body set to fill the page, not cramped.

## Pipeline

```
scrape → MySQL (single source of truth) → translate (MiniMax‑M3) → summarize → build (cover + mind map) → output/*.epub|pdf|mobi|azw3
```

- **MySQL** holds everything: raw pages / audio transcripts, parsed text, translations, briefs, and a full audit trail of every fetch and every model call.
- **Incremental** by content hash — re‑running only processes what changed, reusing existing translations and briefs.
- **Six source types** supported: static HTML, WordPress REST API, Strikingly, and podcast (download → local ASR).

## Quick start

```bash
# 1. Database (MySQL 9 via Docker, port 3307, schema auto‑initialized)
docker compose up -d

# 2. Python env
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # add your MiniMax token (ANTHROPIC_AUTH_TOKEN)

# 3. One command: scrape → translate → summarize → build → backup (all incremental)
bash scripts/refresh.sh
```

Outputs land in `output/`. Per‑stage commands and architecture are in [CLAUDE.md](./CLAUDE.md).

## How it works (highlights)

- **Translation** uses MiniMax‑M3 via the Anthropic‑compatible API, with a system prompt that aims for 信达雅 (faithful · expressive · elegant). HTML structure is preserved; only natural‑language text is translated. Article‑level concurrency keeps each piece stylistically coherent.
- **Brief + mind map**: a model distills a bilingual thesis and key points (robust delimiter format, not fragile JSON); a horizontal tree mind map is rendered with Pillow and embedded at the top of each chapter.
- **Covers** are generated with Pillow — a per‑author colour theme, the author's name prominent, translator/source/repo credited modestly in the footer.
- **Podcasts** are transcribed locally with Qwen3‑ASR (Apple MLX) and then flow through the same translation/typesetting path as text.

## Add a new source

1. Scout the site and pick a strategy (static HTML / WordPress API / site‑builder DOM / podcast).
2. `INSERT INTO sources (...)`, write `src/scrape/scrape_<key>.py` (WordPress sites just call `wp_common.scrape_wp`).
3. Add the book titles to `book/build_book.py:BOOK_META` and a colour theme in `book/cover.py`.
4. Run `scripts/refresh.sh`.

## License & ethics

The code is open source. The **original texts remain the copyright of their authors**; this is a non‑commercial, educational bilingual edition. We keep clear attribution and links back to every source, and will promptly honour any author's request to amend or remove. Scraped corpora, translations and finished books are kept private and are **not** part of this public repository.

---

Built by [Bill Li](https://libiao.ai) with Claude Opus 4.8 and MiniMax‑M3.
