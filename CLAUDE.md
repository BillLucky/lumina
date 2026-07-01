# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目目标

把知名人物的博客/播客完整抓取下来，存入本地 MySQL，再用大模型翻译 + 提炼导读，最终制作成双语（英文原版 + 中文译版）开源电子书（EPUB/PDF/MOBI/AZW3），含**封面**、每篇**核心导读 + 思维导图**，按时间正序编排、带富目录。

**博客 / 文集类**（`scrape_<key>.py` → 翻译 → 制书）：

| source_key | 人物 | 站点 | kind（抓取方式） |
|---|---|---|---|
| `paulgraham` | Paul Graham | paulgraham.com | `static_html`（`<font>` 正文） |
| `naval` | Naval Ravikant | nav.al | `wordpress_api` |
| `pmarca` | Marc Andreessen | pmarchive.com | `static_html`（`<article>`+`<time>`） |
| `michaelseibel` | Michael Seibel | michaelseibel.com | `strikingly`（`.s-blog-content`） |
| `startupmarketing` | Sean Ellis | startup-marketing.com | `wordpress_api`（复用 `wp_common`） |
| `avc` | Fred Wilson | avc.com | `wordpress_api`（复用 `wp_common`） |
| `abovethecrowd` | Bill Gurley | abovethecrowd.com | `wordpress_api`（复用 `wp_common`） |
| `farnamstreet` | Shane Parrish | fs.blog | `wordpress_api`（复用 `wp_common`） |
| `cdixon` | Chris Dixon | cdixon.org | `static_html`（`/archive` + `article.post`） |
| `samaltman` | Sam Altman | blog.samaltman.com | `static_html`（Posthaven 分页 + atom 日期） |
| `danluu` | Dan Luu | danluu.com | `static_html`（Hugo RSS 全文） |
| `eladgil` | Elad Gil | blog.eladgil.com | `substack`（sitemap + post API） |
| `firstround` | First Round Review | review.firstround.com | `static_html`（Ghost sitemap + SSR） |
| `feld` | Brad Feld | feld.com | `static_html`（sitemap，~5500 篇） |
| `gwern` | Gwern Branwen | gwern.net | `static_html`（sitemap 筛顶层随笔 + `#markdownBody`） |

**播客类**（RSS 驱动，`scrape/scrape_podcast.py`：解析 feed 全集 → 下载 mp3 → 本地 ASR 转写 → 翻译 → 制书）。a16z 全系列，每系列一本独立书、统一 a16z 视觉：

`a16z`（The a16z Show）· `a16z_ai`（AI + a16z）· `a16z_crypto`（web3 with a16z）· `a16z_raising_health` · `a16z_live` · `a16z_16min`（16 Minutes）· `a16z_benmarc`（Ben & Marc）· `a16z_hotline`（Startup Hotline）

架构按「多来源」设计：**新增博客 = `sources` 表加一行（`ensure_source` 或 INSERT）+ 写一个 `scrape/scrape_<key>.py` + 在 `book/build_book.py:BOOK_META`/`SOURCE_URL` 与 `book/cover.py:THEMES`/`SOURCE_SITE` 各加一行**（WP 站直接复用 `scrape/wp_common.py:scrape_wp`；播客只需在 `scrape_podcast.py:SERIES` 加一行 feed）。翻译、导读、制书流程对所有来源通用。

**并行长跑驱动**（均可断点续传、被 `scripts/watchdog.sh` 守护，Docker/MySQL 崩了自愈）：
- `scripts/a16z_grind.sh`：逐系列 ASR→翻译→出书。**`--no-download` 省流量模式**：只处理已下载音频，不再拉新（未下载的留待将来增量；`scripts/download_remaining_audio.sh` 在有流量时一键补下）。
- `scripts/text_grind.sh` / `text_grind2.sh` / `pipe_avc.sh` / `pipe_gwern.sh`：文本源并行抓取 + 流水线翻译出书（抓完即翻，不互等）。
- `scripts/pipe_cleanup.sh`：**收尾管道**——把首轮跑完后残留的 failed/漏译篇目按源重跑（翻译→导读→重制书，纯翻译不再抓取、不占流量）。`status='failed'` 会被 translate 当 pending 捡起，故可反复运行、断点续传。
- `scripts/name_audio.py`：把 UUID 音频整理成「序号-中文（English）」可读软链接 + manifest.tsv（不动原文件）。

**实时看板**：仓库根目录 `./lumina`（启动器，免设 PYTHONPATH/激活 venv）= 类 top/watch 的自刷新面板，一眼看全 ASR/翻译/出书进度、各 lane 存活、每源待重试失败数。`./lumina --once` 打印一次。底层 `scripts/dashboard.py` 数据取自 MySQL + 磁盘扫描；DB 只做 1 次快速探测（`db.cursor(retries=1)`），MySQL 抖动/重启时降级渲染而非阻塞。

完整流水线（`scripts/refresh.sh`）：抓取 → 翻译 → **生成导读** → 制书（封面+导图） → 导出 DB。

## 四阶段流水线

```
抓取(scrape) → MySQL → 翻译(translate) → MySQL → 制书(book) → output/*.epub|pdf|azw3
```

数据全部以 MySQL 为单一事实源（single source of truth）。原始网页、解析后的结构化内容、
译文、以及每一次 HTTP 抓取 / 模型调用都全量留痕，因此各阶段可独立重跑、断点续传、增量更新。

## 常用命令

所有 Python 脚本以 **模块** 方式运行，必须设置 `PYTHONPATH=src`，并使用虚拟环境解释器 `.venv/bin/python`。

```bash
# 环境准备（一次性）
docker compose up -d                       # 启动 MySQL 9（端口 3307，schema 自动初始化）
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                        # 填入 DB 与 MiniMax token

# 1) 抓取（礼貌限速、自动写 fetch_log、增量 upsert）
PYTHONPATH=src .venv/bin/python -m scrape.scrape_paulgraham           # 全量
PYTHONPATH=src .venv/bin/python -m scrape.scrape_paulgraham --limit 5 # 调试
PYTHONPATH=src .venv/bin/python -m scrape.scrape_naval                # WP API
PYTHONPATH=src .venv/bin/python -m scrape.scrape_pmarca               # pmarchive
PYTHONPATH=src .venv/bin/python -m scrape.scrape_michaelseibel        # michaelseibel

# 一键全流程（抓取→翻译→制书→导出DB，全增量）
bash scripts/refresh.sh

# 2) 翻译（英→中，3~5 并发，信达雅，全量写 api_call 审计）
PYTHONPATH=src .venv/bin/python -m translate.translate --source all
PYTHONPATH=src .venv/bin/python -m translate.translate --source naval --limit 3
PYTHONPATH=src .venv/bin/python -m translate.translate --source all --redo   # 强制重译

# 3) 制书（按时间正序，富目录）
PYTHONPATH=src .venv/bin/python -m book.build_book --all
PYTHONPATH=src .venv/bin/python -m book.build_book --source naval --lang en --formats epub,azw3,pdf
```

数据库连接（排查用）：`mysql -h127.0.0.1 -P3307 -ublog -pblogpass blogbook`

## 数据模型（`sql/schema.sql`）

- **`sources`** — 博客来源（`source_key` 如 paulgraham/naval，`kind` 为 static_html/wordpress_api）。
- **`articles`** — 原文。一条 = 一篇。关键列：
  - `raw_html` 存**原始网页 / WP API 原始 JSON**；`content_html` 是清洗后用于制书的正文；`content_text` 纯文本。
  - `content_hash` = `content_text` 的 sha256，**增量判断的核心**：抓取时 hash 变了才算「内容更新」。
  - `published_at`（可排序时间）+ `published_text`（原始文本如 "July 2023"）；`chrono_index` 是按时间升序的全局序号，制书排序用。
  - `is_external=1` 标记非正文条目（PG 索引里混入的外链 / 图片-PDF 老文章），翻译与制书会跳过。
  - `(source_id, slug)` 唯一键 → upsert 幂等。
- **`translations`** — 译文，`(article_id, target_lang)` 唯一。`src_hash` 记录翻译时所依据的原文 hash；
  与 `articles.content_hash` 不一致即表示原文已更新、译文过期，需重译。
- **`fetch_log` / `api_call`** — 审计表，分别记录每次 HTTP 抓取、每次模型调用（含完整请求/响应 JSON、token、延迟）。
- **`book_build`** — 每次制书产物记录。

## 增量更新机制（重跑安全）

整条流水线靠 hash 比对做增量，**重复运行不会重复翻译、不会破坏数据**：

1. 抓取：`upsert_article` 比对 `content_hash`，返回 `content_changed`，原文变了才更新行。
2. 翻译：`translate.translate` 的 `pending_articles` 只挑 `translations` 缺失 / 失败 / `src_hash` 与原文不符的文章。
3. 制书：每次全量重建 EPUB（成本低）。

因此「网站更新了」只需依次重跑 scrape → translate → build 即可补差。

## 架构约定与易踩的点

- **运行方式**：脚本用 `sys.path.insert` 把 `src/` 加进路径，但仍须 `PYTHONPATH=src` + `-m 包.模块` 运行；直接 `python src/scrape/xxx.py` 会因相对包导入失败。
- **礼貌抓取**：所有抓取走 `common/http.py:PoliteFetcher` —— 单线程串行、请求间随机 sleep `[FETCH_MIN_DELAY, FETCH_MAX_DELAY]`、指数退避重试、固定可识别 UA。**新增 scraper 必须复用它，不要自己裸调 requests**，以免对目标站造成压力。
- **PG 正文提取**：靠「文字最多的 `<font>` 容器」定位正文，再按 `<br><br>` 切成 `<p>` 段落；日期是正文首行（`scrape/dates.py` 解析）。站点结构若变，改 `scrape_paulgraham.py:_main_container/_html_to_paragraphs`。
- **Naval 走 WP REST API**（`/wp-json/wp/v2/posts`，`per_page=100` 分页），不要去解析它的 HTML 页面。`raw_html` 列对 Naval 存的是完整 API JSON。
- **pmarca**：HTML5 站，正文在 `<article>`，日期在 `<time>Posted on June 18, 2007</time>`（用 `dates.parse_full_date` 解析含「日」的日期）。站点不在 HTTP 头声明 charset，必须 `resp.content.decode("utf-8")`，否则 `·`/`'` 等会乱码成 `Â·`/`â`。
- **michaelseibel**：Strikingly 托管，文章 URL 取自 `sitemap.xml` 的 `/blog/` 路径；正文统一从**渲染后**的 `.s-blog-content` 容器提取（站点有新旧两种内嵌存储格式，DOM 容器对两者都通用）；日期取内嵌 JSON 的 `publishedAt`。同样需强制 UTF-8 解码。
- **翻译并发**：文章级并发（`TRANSLATE_CONCURRENCY`，3~5），单篇内部按 `CHUNK_CHARS` 分块顺序翻译以保证风格连贯。token 充足，`TRANSLATE_MAX_TOKENS` 已放大以容纳长文整块输出。
- **翻译保结构**：prompt 要求模型保留 HTML 标签、只译标签内文字、`href`/代码/URL 原样保留；响应可能带 ```` ``` ```` 围栏，由 `_strip_fences` 去除。
- **制书排序**：永远按 `chrono_index` 升序（最早在前）。中文书只收 `translations.status='done'` 的篇目。

## 增强功能（封面 / 导读思维导图 / 播客 ASR / PDF 版心）

- **封面**（`book/cover.py`）：纯 PIL 排版式封面，每个来源一套主题色（`THEMES`），中文宋体/英文 Georgia。制书时按首尾年份生成并 `book.set_cover()`，calibre 自动用于 PDF/AZW3/MOBI。图片走 `assets/covers/`（gitignore，可重建）。
- **核心导读 + 思维导图**：
  - `translate/summarize.py`：用 MiniMax-M3 为每篇文章提炼「一句话论点 + 3~6 要点（中英双语）」，存 `summaries` 表。**输出用分隔符纯文本模板（`[EN]/[ZH]` + `label :: detail`）而非 JSON** —— 早期用 JSON 失败率高达 ~46%（引号/换行），换成分隔符后几乎零失败。增量靠 `src_hash`。
  - `book/mindmap.py`：把要点渲染成横向树形思维导图 PNG（中心节点 + 曲线枝干 + 要点卡片，主题色）。
  - `book/build_book.py:_brief_block`：在每章正文前插入「核心导读」卡片（论点）+ 思维导图图片。无导读的文章正常降级渲染。
- **PDF 版心**（`book/print.css` + `convert()`）：PDF 默认页边距是 **72pt**，且必须用 `--pdf-page-margin-*`（不是 `--margin-*`，后者对 PDF 无效）。配合 `--extra-css print.css` 清零 body 边距，正文占满 ~80% 宽。改版心改这两处。
- **播客 ASR**（`scrape/scrape_a16z.py`）：下载 Simplecast 音频 → ffmpeg 转 16k 单声道 → 用一个装有 `mlx-qwen3-asr` 的 Python 环境跑 `mlx_qwen3_asr`（`Qwen3-ASR-1.7B`，Apple MLX，RTF~0.3）转写英文 → 文字稿按句分段作为「文章」入库 → 走通用翻译/制书流程。说话人分离(`--diarize`)需 pyannote+HF token，未启用。ASR 解释器路径由 `.env` 的 `ASR_PYTHON` 指定（勿写死）。

## 数据库备份 / 换机迁移

MySQL 是唯一事实源，整库（含原始网页、译文、审计）可导出为压缩 SQL 备份进版本库：

```bash
bash scripts/export_db.sh   # 导出到 db_backup/blogbook.sql.gz（mysqldump 全量）
bash scripts/import_db.sh   # 换机/重置后恢复（先 docker compose up -d）
```

`data/`、`output/`、`db_backup/*.sql.gz` 这类大文件/可再生产物默认被 `.gitignore` 忽略，
**唯独 `db_backup/blogbook.sql.gz` 用 `git add -f` 强制纳入**，作为换机时一键重建数据的快照。

## 翻译模型（MiniMax-M3）

走 Anthropic 兼容接口：`POST {ANTHROPIC_BASE_URL}/v1/messages`，header 同时带 `x-api-key` 与
`Authorization: Bearer`、`anthropic-version: 2023-06-01`，model = `MiniMax-M3`。响应为标准
Anthropic 格式（`content[].text`、`usage.input_tokens/output_tokens`）。封装在 `translate/client.py`，
每次调用无论成败都写 `api_call` 表。密钥放 `.env`（已 gitignore），不要写进代码或提交。

## 外部依赖

- **MySQL 9** — 经 `docker-compose.yml` 启动，端口映射 `3307:3306`（避开本机 3306）。
- **calibre** 的 `ebook-convert` — EPUB→AZW3/MOBI/PDF 转换用；缺失时制书会自动跳过这些格式、仅产 EPUB。安装：`brew install --cask calibre`。
- EPUB 由纯 Python 的 `ebooklib` 生成，无需外部工具。
