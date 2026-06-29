"""a16z Show 播客抓取 + 本地 ASR 转写器。

流程（每期独立、可断点续传）：
  1. 下载 mp3（Simplecast 直链，断点续传）→ data/a16z/audio/<slug>.mp3
  2. ffmpeg 转 16kHz 单声道 wav
  3. 复用 asr-env 的 mlx_qwen3_asr（Qwen3-ASR-1.7B，本地 MLX）转写英文
  4. 文字稿整理成段落，作为「文章」存入 MySQL（content=transcript）
  之后即可走通用的翻译 / 制书流程，把播客做成书。

说话人分离(diarization)需 pyannote+HF token，未启用；当前输出为单流可读文字稿。

用法：
  python -m scrape.scrape_a16z            # 处理全部 5 期
  python -m scrape.scrape_a16z --only ep4-sinofsky
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import config, db   # noqa: E402

# 本地 ASR 解释器（含 mlx_qwen3_asr）。通过 .env 的 ASR_PYTHON 指定，避免把本地路径写死进仓库。
ASR_PYTHON = os.getenv("ASR_PYTHON", "python3")
ASR_MODEL = os.getenv("ASR_MODEL", "Qwen/Qwen3-ASR-1.7B")
AUDIO_BASE = ("https://afp-848985-injected.calisto.simplecastaudio.com/"
              "3f86df7b-51c6-4101-88a2-550dba782de8/episodes/{eid}/audio/128/default.mp3"
              "?awCollectionId=3f86df7b-51c6-4101-88a2-550dba782de8&awEpisodeId={eid}")

# 最新 5 期（来自 Simplecast）
EPISODES = [
    dict(slug="ep1-exa-search", eid="c48ce585-3138-4686-bb9c-eaf4d247dee4",
         title="Building Search for AI Agents with Exa CEO Will Bryk",
         date="2026-06-06", guests=["Sarah Wang", "Will Bryk"]),
    dict(slug="ep2-customer-data", eid="d887845c-81f2-4dc0-b162-5549e089aafd",
         title="AI Agents and the Fight for Customer Data",
         date="2026-06-05", guests=["Martin Casado", "George Fraser"]),
    dict(slug="ep3-network-states", eid="a3c3971e-e2e4-41a6-bb93-addb227d9187",
         title="Balaji and Steven Glinert on Network States, Supply Chains, and Allied Coalition Strategy",
         date="2026-06-03", guests=["Theo Jaffee", "Sophia Puccini", "Steven Glinert", "Balaji Srinivasan"]),
    dict(slug="ep4-sinofsky", eid="6de42287-1fdf-427f-a5bd-f5ff8936238b",
         title="Steven Sinofsky on AI PCs, NVIDIA, and the Future of Computing",
         date="2026-06-02", guests=["Theo Jaffee", "Steven Sinofsky"]),
    dict(slug="ep5-power-gen", eid="1f58eddc-8ef4-463e-9be7-5fc1f3f7f06f",
         title="How Radiant and Heron Are Rethinking Power Generation and Delivery",
         date="2026-05-31", guests=["Erik Torenberg", "Erin Price-Wright", "Drew Baglino", "Doug Bernauer"]),
]

AUDIO_DIR = config.DATA_DIR / "a16z" / "audio"
ASR_DIR = config.DATA_DIR / "a16z"


def _run(cmd, timeout=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def download(ep) -> Path:
    mp3 = AUDIO_DIR / f"{ep['slug']}.mp3"
    if mp3.exists() and mp3.stat().st_size > 1_000_000:
        print(f"  [skip] 已有音频 {mp3.name}（{mp3.stat().st_size//1024//1024}MB）")
        return mp3
    url = AUDIO_BASE.format(eid=ep["eid"])
    print(f"  下载 {ep['slug']} ...")
    # -C - 断点续传；-L 跟随跳转
    r = _run(["curl", "-sL", "-C", "-", "--max-time", "600",
              "-A", "Mozilla/5.0", url, "-o", str(mp3)], timeout=650)
    if not mp3.exists() or mp3.stat().st_size < 1_000_000:
        raise RuntimeError(f"下载失败: {r.stderr[:200]}")
    print(f"  ✓ {mp3.stat().st_size//1024//1024}MB")
    return mp3


def to_wav(mp3: Path) -> Path:
    wav = mp3.with_suffix(".16k.wav")
    if wav.exists():
        return wav
    print(f"  ffmpeg → 16k 单声道 ...")
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3),
          "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], timeout=600)
    return wav


def transcribe(ep, wav: Path) -> dict:
    out_json = ASR_DIR / f"{ep['slug']}.asr.json"
    if out_json.exists():
        print(f"  [skip] 已有转写 {out_json.name}")
        return json.loads(out_json.read_text())
    print(f"  ASR 转写中（约 10~15 分钟）...")
    tmpd = ASR_DIR / f"_asr_{ep['slug']}"
    tmpd.mkdir(parents=True, exist_ok=True)
    r = _run([ASR_PYTHON, "-m", "mlx_qwen3_asr", str(wav),
              "--model", ASR_MODEL, "--language", "en",
              "--output-dir", str(tmpd), "--output-format", "json",
              "--timestamps", "--no-progress", "--quiet"], timeout=3600)
    jfs = sorted(tmpd.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not jfs:
        raise RuntimeError(f"转写无输出: {r.stderr[-300:]}")
    data = json.loads(jfs[-1].read_text())
    out_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
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


def run(only: str | None = None):
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    source_id = db.get_source_id("a16z")
    eps = [e for e in EPISODES if not only or e["slug"] == only]

    for i, ep in enumerate(eps, 1):
        print(f"\n[{i}/{len(eps)}] {ep['slug']} — {ep['title']}")
        try:
            mp3 = download(ep)
            wav = to_wav(mp3)
            data = transcribe(ep, wav)
            text = data.get("text") or ""
            content_html, content_text = transcript_to_html(text)
            published_at = datetime.fromisoformat(ep["date"])
            author = ", ".join(ep["guests"])
            meta = {"episode_id": ep["eid"], "guests": ep["guests"],
                    "audio_url": AUDIO_BASE.format(eid=ep["eid"]),
                    "asr_model": ASR_MODEL, "words": len(content_text.split())}
            db.upsert_article(
                source_id, slug=ep["slug"], url=f"https://a16z.com/podcasts/a16z-show/",
                title=ep["title"], author=author, published_at=published_at,
                published_text=published_at.strftime("%B %d, %Y"),
                raw_html=json.dumps(data, ensure_ascii=False),
                content_html=content_html, content_text=content_text,
                meta=meta, http_status=200, is_external=(len(content_text) < 200))
            print(f"  ✓ 入库：{len(content_text.split())} 词")
        except Exception as e:
            print(f"  ✗ 失败：{e}")

    total = db.renumber_chrono(source_id)
    print(f"\n完成。可制书播客 {total} 期。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    args = ap.parse_args()
    run(only=args.only)
