"""通用播客抓取 + 本地 ASR 转写器（RSS 驱动，支持任意 Simplecast/标准 RSS feed）。

把 scrape_a16z.py 的「下载→ffmpeg→ASR→入库」流程一般化：
  - 来源不再硬编码集列表，而是解析 RSS feed 拿到**全部历史集**（音频直链 + 标题 + 日期）。
  - 一个 feed = 一个 source_key = 一本独立书；多系列共用本脚本。
  - 下载可并行（CDN 友好，限并发），ASR 串行（单卡瓶颈）。
  - **转写完成即删除 16k wav**（每个约 90MB，否则爆盘）；mp3 按需保留在本地。
  - 断点续传：已有 .asr.json 的集跳过 ASR；DB 按 (source_id, slug) 幂等 upsert。

流程（每集独立、可中断重跑）：
  1. 解析 feed → 集列表（newest-first，可 cap 截断取近期 N 集）
  2. 并行下载 mp3 → data/<key>/audio/<eid>.mp3（断点续传）
  3. ffmpeg 转 16kHz 单声道 wav → ASR（Qwen3-ASR，Apple MLX）→ 文字稿 → 删 wav
  4. 文字稿按句分段作为「文章」入库，之后走通用翻译/制书流程

用法：
  python -m scrape.scrape_podcast --series a16z_ai                 # 单系列全量
  python -m scrape.scrape_podcast --series a16z --cap 150          # a16z Show 取近期 150 集
  python -m scrape.scrape_podcast --all                            # 所有系列（小→大）
  python -m scrape.scrape_podcast --series a16z_ai --download-only # 只并行下载音频，不 ASR
  python -m scrape.scrape_podcast --series a16z_ai --limit 3       # 调试：只处理前 3 集
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import config, db   # noqa: E402

# 本地 ASR 解释器（含 mlx_qwen3_asr）。由 .env 的 ASR_PYTHON 指定，避免写死本机路径。
ASR_PYTHON = os.getenv("ASR_PYTHON", "python3")
ASR_MODEL = os.getenv("ASR_MODEL", "Qwen/Qwen3-ASR-1.7B")

UA = "Mozilla/5.0 (lumina podcast archiver; personal bilingual book project)"

# ── 系列注册表：source_key → 抓取所需信息 ──────────────────────────────
# title/author/theme 等「制书侧」配置另见 book/build_book.py:BOOK_META 与 book/cover.py:THEMES。
# cap：a16z Show 体量过大（~1000 集），先取近期 150；其余系列全量（None）。
SERIES = {
    "a16z": dict(
        name="The a16z Show", kind="podcast", cap=None,   # 磁盘已扩容，拉全量 ~1000 集
        feed="https://feeds.simplecast.com/JGE3yC0V",
        site="https://a16z.com/podcasts/a16z-show/"),
    "a16z_raising_health": dict(
        name="Raising Health", kind="podcast", cap=None,
        feed="https://feeds.simplecast.com/BXDamaKF",
        site="https://a16z.com/podcasts/raising-health/"),
    "a16z_live": dict(
        name="a16z Live", kind="podcast", cap=None,
        feed="https://feeds.simplecast.com/AuWJKpna",
        site="https://a16z.com/podcasts/a16z-live/"),
    "a16z_crypto": dict(
        name="web3 with a16z crypto", kind="podcast", cap=None,
        feed="https://feeds.simplecast.com/XPOpH7r4",
        site="https://a16zcrypto.com/podcast/"),
    "a16z_ai": dict(
        name="AI + a16z", kind="podcast", cap=None,
        feed="https://feeds.simplecast.com/Hb_IuXOo",
        site="https://a16z.com/podcasts/ai-a16z/"),
    "a16z_16min": dict(
        name="16 Minutes News by a16z", kind="podcast", cap=None,
        feed="https://feeds.simplecast.com/j9kKMsfH",
        site="https://a16z.com/podcasts/16-minutes/"),
    "a16z_benmarc": dict(
        name="The Ben & Marc Show", kind="podcast", cap=None,
        feed="https://feeds.simplecast.com/mAT9rqvu",
        site="https://a16z.com/podcasts/ben-marc/"),
    "a16z_hotline": dict(
        name="a16z Startup Hotline", kind="podcast", cap=None,
        feed="https://feeds.simplecast.com/kBzSlpXS",
        site="https://a16z.com/podcasts/startup-hotline/"),
}

# ASR/出书的默认处理顺序：小→大，快速出书，1000 集的 a16z Show 放最后。
DEFAULT_ORDER = ["a16z_hotline", "a16z_benmarc", "a16z_16min", "a16z_ai",
                 "a16z_crypto", "a16z_live", "a16z_raising_health", "a16z"]


def _run(cmd, timeout=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ── feed 解析 ────────────────────────────────────────────────────────
def _episode_id(audio_url: str, guid: str) -> str:
    """从 enclosure URL 取 Simplecast 稳定集 id（awEpisodeId）；缺失则回退 guid 末段。"""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(audio_url).query)
    eid = (q.get("awEpisodeId") or q.get("awEpisodeID") or [None])[0]
    if eid:
        return eid
    tail = re.sub(r"[^a-zA-Z0-9]+", "-", (guid or audio_url).split("/")[-1]).strip("-")
    return tail[:64] or "ep"


def parse_feed(feed_url: str, cap: int | None = None) -> list[dict]:
    """解析 RSS feed → 集列表（feed 原序，通常 newest-first）。cap 截断取前 N 集。"""
    req = urllib.request.Request(feed_url, headers={"User-Agent": UA})
    xml = urllib.request.urlopen(req, timeout=config.FETCH_TIMEOUT).read()
    root = ET.fromstring(xml)
    eps = []
    for it in root.findall(".//item"):
        enc = it.find("enclosure")
        if enc is None or not enc.get("url"):
            continue
        audio_url = enc.get("url")
        guid = it.findtext("guid") or ""
        title = (it.findtext("title") or "").strip()
        pub = it.findtext("pubDate")
        try:
            published_at = parsedate_to_datetime(pub).replace(tzinfo=None) if pub else None
        except Exception:
            published_at = None
        # 简介：优先 itunes:summary / description（纯文本留痕，不入正文）
        desc = it.findtext("description") or ""
        eps.append(dict(
            eid=_episode_id(audio_url, guid), title=title, audio_url=audio_url,
            guid=guid, published_at=published_at, desc=desc.strip()))
    if cap:
        eps = eps[:cap]
    return eps


# ── 下载 / 转写 ──────────────────────────────────────────────────────
def _dirs(key: str) -> tuple[Path, Path]:
    audio = config.DATA_DIR / key / "audio"
    asr = config.DATA_DIR / key
    audio.mkdir(parents=True, exist_ok=True)
    return audio, asr


def download(key: str, ep: dict) -> Path | None:
    audio_dir, _ = _dirs(key)
    mp3 = audio_dir / f"{ep['eid']}.mp3"
    if mp3.exists() and mp3.stat().st_size > 500_000:
        return mp3
    r = _run(["curl", "-sL", "-C", "-", "--max-time", "900", "-A", UA,
              ep["audio_url"], "-o", str(mp3)], timeout=950)
    if not mp3.exists() or mp3.stat().st_size < 500_000:
        print(f"  ✗ 下载失败 {ep['eid']}: {r.stderr[:120]}")
        return None
    return mp3


def to_wav(mp3: Path) -> Path:
    wav = mp3.with_suffix(".16k.wav")
    if wav.exists():
        return wav
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3),
          "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], timeout=900)
    return wav


def transcribe(key: str, ep: dict, wav: Path) -> dict:
    """ASR 转写；结果缓存为 <eid>.asr.json。转写后 wav 由调用方删除。"""
    _, asr_dir = _dirs(key)
    out_json = asr_dir / f"{ep['eid']}.asr.json"
    if out_json.exists():
        return json.loads(out_json.read_text())
    tmpd = asr_dir / f"_asr_{ep['eid']}"
    tmpd.mkdir(parents=True, exist_ok=True)
    r = _run([ASR_PYTHON, "-m", "mlx_qwen3_asr", str(wav),
              "--model", ASR_MODEL, "--language", "en",
              "--output-dir", str(tmpd), "--output-format", "json",
              "--timestamps", "--no-progress", "--quiet"], timeout=7200)
    jfs = sorted(tmpd.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not jfs:
        raise RuntimeError(f"转写无输出: {r.stderr[-300:]}")
    data = json.loads(jfs[-1].read_text())
    out_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    # 清理临时目录
    for f in tmpd.glob("*"):
        f.unlink()
    tmpd.rmdir()
    return data


def transcript_to_html(text: str) -> tuple[str, str]:
    """把整段文字稿按句子分组成段落，便于阅读与后续翻译。"""
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    paras, buf = [], []
    for s in sentences:
        buf.append(s)
        if len(buf) >= 4:
            paras.append(" ".join(buf))
            buf = []
    if buf:
        paras.append(" ".join(buf))
    html = "\n".join(f"<p>{p}</p>" for p in paras if p.strip())
    plain = "\n".join(paras)
    return html, plain


# ── 来源注册 ─────────────────────────────────────────────────────────
def ensure_source(key: str) -> int:
    s = SERIES[key]
    with db.cursor() as cur:
        cur.execute("SELECT id FROM sources WHERE source_key=%s", (key,))
        row = cur.fetchone()
        if row:
            return row["id"]
        cur.execute(
            "INSERT INTO sources (source_key,name,base_url,kind,lang) VALUES (%s,%s,%s,%s,'en')",
            (key, s["name"], s["site"], s["kind"]))
        return cur.lastrowid


# ── 主流程 ───────────────────────────────────────────────────────────
def run_series(key: str, cap: int | None = None, workers: int = 4,
               limit: int | None = None, download_only: bool = False,
               keep_wav: bool = False, no_download: bool = False):
    s = SERIES[key]
    cap = cap if cap is not None else s.get("cap")
    source_id = ensure_source(key)
    print(f"\n===== {key} · {s['name']} =====")
    eps = parse_feed(s["feed"], cap=cap)
    if limit:
        eps = eps[:limit]
    print(f"  feed 共 {len(eps)} 集 待处理（cap={cap}）")

    audio_dir, _ = _dirs(key)

    # 1) 并行下载（CDN 友好，限并发）。no_download：只处理已下载的，绝不再拉新音频（省流量）。
    if no_download:
        have = sum(1 for ep in eps if (audio_dir / f"{ep['eid']}.mp3").exists())
        print(f"  [no-download] 仅处理已下载的 {have}/{len(eps)} 集，跳过未下载的")
    else:
        print(f"  并行下载音频（{workers} 并发）...")
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(download, key, ep): ep for ep in eps}
            for fut in as_completed(futs):
                done += 1
                if done % 20 == 0 or done == len(eps):
                    print(f"    下载 {done}/{len(eps)}")
        if download_only:
            print("  [download-only] 跳过 ASR")
            return

    # 2) 串行 ASR + 入库（断点续传：已有 asr.json 直接复用）
    for i, ep in enumerate(eps, 1):
        mp3 = audio_dir / f"{ep['eid']}.mp3"
        if not mp3.exists():
            if no_download:
                continue                      # 未下载的直接跳过（留待将来增量处理）
            mp3 = download(key, ep)
            if not mp3:
                continue
        try:
            cached = (config.DATA_DIR / key / f"{ep['eid']}.asr.json").exists()
            print(f"  [{i}/{len(eps)}] {'复用' if cached else 'ASR'} · {ep['title'][:60]}")
            if cached:
                data = transcribe(key, ep, None)   # 断点续传：已转写直接读缓存，免 ffmpeg
            else:
                wav = to_wav(mp3)
                data = transcribe(key, ep, wav)
                if not keep_wav and wav.exists():
                    wav.unlink()                   # 转完即删，省盘
            text = data.get("text") or ""
            content_html, content_text = transcript_to_html(text)
            meta = {"episode_id": ep["eid"], "audio_url": ep["audio_url"],
                    "asr_model": ASR_MODEL, "words": len(content_text.split()),
                    "series": s["name"]}
            db.upsert_article(
                source_id, slug=ep["eid"], url=s["site"], title=ep["title"],
                author=s["name"], published_at=ep["published_at"],
                published_text=ep["published_at"].strftime("%B %d, %Y") if ep["published_at"] else None,
                raw_html=json.dumps({"feed_desc": ep["desc"], "asr": data}, ensure_ascii=False),
                content_html=content_html, content_text=content_text,
                meta=meta, http_status=200, is_external=(len(content_text) < 200))
        except Exception as e:
            print(f"  ✗ 失败 {ep['eid']}: {e}")

    total = db.renumber_chrono(source_id)
    print(f"  ✓ {key} 完成：可制书 {total} 集")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", default=None, help="单系列 source_key")
    ap.add_argument("--all", action="store_true", help="按 DEFAULT_ORDER 处理全部系列")
    ap.add_argument("--cap", type=int, default=None, help="只取 feed 前 N 集（覆盖系列默认）")
    ap.add_argument("--workers", type=int, default=4, help="下载并发数")
    ap.add_argument("--limit", type=int, default=None, help="调试：只处理前 N 集")
    ap.add_argument("--download-only", action="store_true", help="只并行下载音频，不 ASR")
    ap.add_argument("--no-download", action="store_true",
                    help="只处理已下载的音频，绝不拉新音频（省流量；未下载的留待将来增量）")
    ap.add_argument("--keep-wav", action="store_true", help="保留 16k wav（默认转完删）")
    args = ap.parse_args()

    if args.all:
        keys = DEFAULT_ORDER
    elif args.series:
        keys = [args.series]
    else:
        ap.error("需指定 --series KEY 或 --all")

    for key in keys:
        if key not in SERIES:
            print(f"未知系列: {key}（可选: {', '.join(SERIES)}）")
            continue
        run_series(key, cap=args.cap, workers=args.workers, limit=args.limit,
                   download_only=args.download_only, keep_wav=args.keep_wav,
                   no_download=args.no_download)


if __name__ == "__main__":
    main()
