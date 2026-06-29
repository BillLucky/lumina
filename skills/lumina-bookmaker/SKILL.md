---
name: lumina-bookmaker
description: 把名人博客/播客抓取→存MySQL→大模型翻译(信达雅)→提炼导读思维导图→制作成带封面的双语开源电子书(EPUB/PDF/MOBI/AZW3)。Use when Bill 说"把某个博客/网站/播客做成书"、"加一个数据源到 lumina"、"再抓一个作者的文章出双语书"、"lumina-bookmaker"，或要复用这套抓取/翻译/制书流水线时。
---

# lumina 双语成书流水线

把公开的博客文章 / 播客音频，做成「英文原版 + 中文信达雅译版」的开源电子书，每篇带**核心导读 + 思维导图**，含**封面**与**版权致谢页**，按时间正序编排、富目录。

参考实现仓库：`~/Documents/Code/miaox/makeOnlineBlogToBook`（GitHub: BillLucky/lumina）。优先复用该仓库的代码，不要另起炉灶。

## 架构（四阶段 + 增强）

```
抓取 → MySQL(blogbook) → 翻译(MiniMax-M3) → 导读/思维导图 → 制书(封面+导图) → output/*.epub|pdf|mobi|azw3
```

- **唯一事实源**：本地 MySQL 9（docker，端口 3307，库 `blogbook`）。原始网页/音频转写、结构化正文、译文、导读、每次抓取/模型调用全量留痕。
- **多来源**：`sources` 表一行一个来源；每源产出 en + zh 两本书。
- **增量**：靠 `content_hash` —— 重跑只处理新增/变更，复用已有译文与导读。
- **运行约定**：所有脚本 `PYTHONPATH=src .venv/bin/python -m <pkg>.<mod>`；密钥在 `.env`（gitignore）。

## 加一个新来源的标准动作（最常见任务）

1. **侦察站点结构**，判定抓取方式：
   - 静态 HTML 老站（如 paulgraham）→ 解析正文容器；注意 `resp.content.decode("utf-8")` 防乱码。
   - **WordPress** → 首选 `wp-json/wp/v2/posts` REST API（`scrape/wp_common.py:scrape_wp` 直接复用，最省事）。先 `curl -I .../wp-json/wp/v2/posts?per_page=1` 看 `x-wp-total`。
   - Strikingly / Squarespace 等站建器 → 取 sitemap.xml 列文章，正文从渲染后的内容容器（如 `.s-blog-content`）提取。
   - **播客** → 找 RSS / 托管平台(Simplecast/Megaphone) 的音频直链；下载 mp3 → ffmpeg 转 16k 单声道 → 本地 ASR 转写（见下）。
2. `INSERT INTO sources (source_key,name,base_url,kind,lang)`。
3. 写 `src/scrape/scrape_<key>.py`（WP 站只需几行调 `scrape_wp`）。礼貌抓取**必须**走 `common/http.py:PoliteFetcher`（限速+退避+审计），勿裸调 requests。
4. 在 `book/build_book.py:BOOK_META` 加 `(<key>,'en')` 与 `(<key>,'zh')` 的中英书名；在 `cover.py:THEMES` 加一套主题色、`SOURCE_SITE`/`build_book.py:SOURCE_URL` 加站点。
5. 跑：`scrape_<key>` → `translate.translate --source <key>` → `translate.summarize --source <key>` → `book.build_book --source <key> --lang en|zh`。
   或直接 `bash scripts/refresh.sh` 全量增量。

## 翻译（信达雅）

- MiniMax-M3，Anthropic 兼容接口（`POST {ANTHROPIC_BASE_URL}/v1/messages`，header 同时带 `x-api-key` 与 `Authorization: Bearer`、`anthropic-version`）。
- system prompt 以严复/傅雷/余光中等为标杆，要求保留 HTML 标签只译文字、`href`/代码原样。
- 文章级并发（`TRANSLATE_CONCURRENCY`，实测 24 稳定），单篇内按 `CHUNK_CHARS` 分块顺序译保证连贯。token 充足时放开 `TRANSLATE_MAX_TOKENS`。

## 核心导读 + 思维导图

- `translate/summarize.py`：每篇提炼「一句话论点 + 3~6 要点（中英双语）」。**输出务必用分隔符纯文本模板（`[EN]/[ZH]` + `label :: detail`）而非 JSON** —— JSON 因引号/换行失败率高达 ~46%，分隔符近零失败。
- `book/mindmap.py`：PIL 渲染横向树形思维导图 PNG（中心节点 + 曲线枝干 + 要点卡片，主题色）。
- `book/build_book.py:_brief_block`：章首插入「核心导读」卡片 + 导图图片。

## 制书与封面

- EPUB 用 `ebooklib`；PDF/MOBI/AZW3 用 calibre `ebook-convert`（缺失则仅出 EPUB）。
- 排序永远按 `chrono_index` 升序（最早在前）；目录按年份分卷。
- **封面**（`book/cover.py`）：PIL 排版式，深色主题色 + 象牙白衬线标题，原作者名醒目、译者/来源/仓库信息谦逊置页脚（variant=2 圆角框）。
- **版权·致谢页**（`_colophon_html`）：含可点击的原文来源、译者(libiao.ai)、开源仓库链接 + 非商用声明。

## 播客 ASR（本地）

- 复用 asr-env 的 venv 跑 `mlx_qwen3_asr`（`Qwen3-ASR-1.7B`，Apple MLX，M4 Pro RTF~0.3，50min 音频约 13min 转完）。解释器路径由 `.env` 的 `ASR_PYTHON` 指定（勿写死进仓库）。
- 流程：下载音频 → `ffmpeg -ac 1 -ar 16000` → ASR 转 JSON → 文字稿按句分段作为「文章」入库 → 走通用翻译/制书。
- 说话人分离 `--diarize` 需 pyannote + HF token，默认不启用（输出单流可读文字稿）。

## 关键避坑（血泪）

- **PDF 版心过窄**：PDF 默认页边距 72pt，且必须用 `--pdf-page-margin-*`（`--margin-*` 对 PDF 无效），配合 `--extra-css print.css` 清零 body 边距，正文才占满 ~80% 宽。
- **编码乱码**：不声明 charset 的静态站要 `resp.content.decode("utf-8")`，否则 `·`→`Â·`。
- **导读 JSON 失败**：改分隔符模板。
- **后台进程被孤儿杀掉**：用工具的 run_in_background，别叠加 shell 的 `nohup &`。
- **字体缺字形**：封面/导图里中文必须用 CJK 字体（Songti/STHeiti），纯数字/URL 用 Georgia。

## 开源与合规

- 这类搬运原文+译文的项目有版权风险（即便非商用）。**策略：代码公开、原文语料+译文+成书私有**——公开仓库不要包含 `db_backup/*.sql.gz`（含全文）与 `output/` 成书。
- 封面与版权页保留醒目署名 + 原文链接 + 非商用声明 + 尊重作者 takedown。
