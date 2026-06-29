[English](./README.md) · **简体中文**

# lumina

> 把名人思想家的博客与播客，做成精美的**双语**（英文 + 中文）开源电子书 —— 每一篇都带封面、AI 提炼的**核心导读 + 思维导图**、按时间正序编排、富目录。

`lumina` 是一条端到端流水线：**抓取 → 存入 MySQL → 翻译（信达雅）→ 提炼导读与思维导图 → 排版成 EPUB / PDF / MOBI / AZW3。** 架构按多来源设计——新增一位作者只需写一个抓取器 + 数据表加一行。

> **关于电子书本身：** 生成的电子书**不**提交到本仓库。原文版权归原作者所有，本项目是非商业、面向学习的双语呈现。如果你想要某本书来阅读，欢迎在评论区留言，我们会分享给你。

## 产出

每个来源出两本书（英文原版 + 中文译版），每本四种格式：

| 来源 | 站点 | 篇数 |
|---|---|---|
| Paul Graham | paulgraham.com | 231 |
| Naval Ravikant | nav.al | 233 |
| Marc Andreessen（pmarca） | pmarchive.com | 31 |
| Michael Seibel | michaelseibel.com | 18 |
| Sean Ellis | startup-marketing.com | 148 |
| The a16z Show（播客） | a16z.com | 5 |

每章开头是一张**核心导读 / In Brief** 卡片（一句话论点 + 3–6 个要点）和一张渲染出的**思维导图**，随后是充分占满版心、不再拥挤的正文。

## 流水线

```
抓取 → MySQL（唯一事实源）→ 翻译(MiniMax‑M3) → 导读 → 制书(封面+导图) → output/*.epub|pdf|mobi|azw3
```

- **MySQL** 保存一切：原始网页/音频转写、解析正文、译文、导读，以及每次抓取与每次模型调用的完整留痕。
- **增量**：靠内容 hash —— 重跑只处理变更，复用已有译文与导读。
- 支持六类来源：静态 HTML、WordPress REST API、Strikingly、播客（下载 → 本地 ASR 转写）。

## 快速开始

```bash
# 1. 数据库（Docker 起 MySQL 9，端口 3307，schema 自动初始化）
docker compose up -d

# 2. Python 环境
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # 填入 MiniMax token（ANTHROPIC_AUTH_TOKEN）

# 3. 一键全流程：抓取 → 翻译 → 导读 → 制书 → 备份（全部增量）
bash scripts/refresh.sh
```

成品在 `output/`。分阶段命令与架构细节见 [CLAUDE.md](./CLAUDE.md)。

## 工作原理（要点）

- **翻译** 用 MiniMax‑M3（Anthropic 兼容接口），system prompt 以严复/傅雷/余光中等为标杆追求「信达雅」；保留 HTML 结构、只译自然语言文字；文章级并发保证每篇风格连贯。
- **导读 + 思维导图**：模型提炼双语论点与要点（用健壮的分隔符格式，而非脆弱的 JSON）；用 Pillow 渲染横向树形思维导图，嵌入每章开头。
- **封面** 用 Pillow 生成——每位作者一套主题色，原作者名醒目，译者/来源/仓库谦逊地署于页脚。
- **播客** 用本地 Qwen3‑ASR（Apple MLX）转写，再走与文本相同的翻译/排版路径。

## 新增一个来源

1. 侦察站点结构，选定策略（静态 HTML / WordPress API / 站建器 DOM / 播客）。
2. `INSERT INTO sources (...)`，写 `src/scrape/scrape_<key>.py`（WordPress 站只需调 `wp_common.scrape_wp`）。
3. 在 `book/build_book.py:BOOK_META` 加书名，在 `book/cover.py` 加一套主题色。
4. 跑 `scripts/refresh.sh`。

## 许可与伦理

代码开源。**原文版权归原作者所有**；本项目为非商业、面向学习的双语版本。我们保留醒目的署名与指向每个来源的链接，并会第一时间配合作者的修改/下架请求。抓取的语料、译文与成书保持私有，**不**纳入本公开仓库。

---

由 [Bill Li（李标）](https://libiao.ai) 与 Claude Opus 4.8、MiniMax‑M3 共同打造。
