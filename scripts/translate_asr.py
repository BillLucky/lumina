#!/usr/bin/env python3
"""服务器端播客转写翻译器 —— 把 ASR 产出的 <eid>.asr.json 就地译成 <eid>.zh.json（英→中，信达雅）。

与本地 translate 流水线同一套 system prompt / 分块策略 / 加固重试，但**自包含、不依赖 DB**
（服务器上没有 MySQL），只需 `requests` + M3 token。产出的 zh.json rsync 回本地后，由
scripts/import_asr_zh.py 直接 upsert 进 translations 表（status=done），本地无需再调 M3。

放在服务器上与 ASR 并行跑（翻译是 API/CPU 活、不占 GPU）：ASR 出一集 → 本脚本立刻翻一集。

断点续传 / 增量安全：已存在 <eid>.zh.json 的直接跳过；循环扫描，新出现的 asr.json 自动接上。
原子落盘（.tmp→rename）。M3 无限量、只控并发（TRANSLATE_CONCURRENCY）。

env（放 .env 或直接 export）：
  ANTHROPIC_BASE_URL   如 https://api.minimaxi.com/anthropic
  ANTHROPIC_AUTH_TOKEN M3 token
  TRANSLATE_MODEL      默认 MiniMax-M3
  TRANSLATE_MAX_TOKENS 默认 32000
  TRANSLATE_CONCURRENCY 默认 6
  ASR_DATA             音频/转写根目录（默认 ./data）

用法（tmux 内，循环模式）：
  set -a; . .env; set +a
  python translate_asr.py                 # 循环：翻完 pending 后每 60s 重扫，接住新 asr.json
  python translate_asr.py --once          # 只翻当前 pending 一轮就退出
"""
import argparse
import glob
import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

BASE = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/")
TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
MODEL = os.environ.get("TRANSLATE_MODEL", "MiniMax-M3")
MAX_TOKENS = int(os.environ.get("TRANSLATE_MAX_TOKENS", "32000"))
CONCURRENCY = int(os.environ.get("TRANSLATE_CONCURRENCY", "6"))
CHUNK_CHARS = 9000

SYSTEM_PROMPT = (
    "你是一位殿堂级的中英文翻译大家。你的译笔融汇严复「信、达、雅」之准则、傅雷「神似」之追求、"
    "余光中之文采、王佐良之雅正、思果之地道——既有学者的严谨考据，又有文人的从容笔致。"
    "你尤其擅长翻译 Paul Graham、Naval Ravikant 这类思想随笔：逻辑缜密、洞见锋利、行文简练。\n\n"
    "【翻译准则】\n"
    "1. 信（准确严谨）——彻底理解原文的论证逻辑与言外之意，忠实传达，不增不删不曲解；"
    "概念、数据、专有名词必须精确，宁可反复推敲也不臆测。\n"
    "2. 达（通顺地道）——彻底摆脱翻译腔：调整语序、拆分长句、转换词性，写出地道流畅的当代书面中文，"
    "让读者浑然不觉是译文。\n"
    "3. 雅（文采气韵）——再现原作的节奏、语气、幽默与思想锋芒；用词凝练考究，行文有韵律，"
    "该犀利时犀利，该隽永时隽永。\n\n"
    "【一致性】全篇术语、人名、概念译法保持统一；保留原作的段落与强调结构。\n\n"
    "【格式要求】\n"
    "- 输入是 HTML 片段，必须原样保留所有 HTML 标签结构（如 <p> 等），只翻译标签内的自然语言文字；\n"
    "- URL、代码原样保留，绝不翻译或改写；\n"
    "- 人名、公司名、产品名等专有名词：有通行中文译名的用通行译名，技术/品牌名可保留英文；\n"
    "- 只输出翻译后的 HTML 片段本身，不要输出任何解释、前言、Markdown 代码围栏(```）或额外说明。"
)

_FENCE = re.compile(r"^\s*```(?:html)?\s*|\s*```\s*$", re.IGNORECASE)
_SESSION = requests.Session()
_SESSION.mount("https://", HTTPAdapter(pool_connections=8, pool_maxsize=32))
_RETRYABLE = {408, 425, 429, 500, 502, 503, 504, 529}


def _strip_fences(t):
    return _FENCE.sub("", t).strip()


def transcript_to_paras(text):
    """整段文字稿 → 段落列表（每 4 句一段，与本地 transcript_to_html 一致）。"""
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    paras, buf = [], []
    for s in sentences:
        buf.append(s)
        if len(buf) >= 4:
            paras.append(" ".join(buf)); buf = []
    if buf:
        paras.append(" ".join(buf))
    return [p for p in paras if p.strip()]


def make_chunks(paras, budget=CHUNK_CHARS):
    """段落 → HTML 块字符串列表（每块 <p>…</p> 累计到字符预算）。"""
    chunks, cur, cur_len = [], [], 0
    for p in paras:
        block = f"<p>{p}</p>"
        if cur and cur_len + len(block) > budget:
            chunks.append("\n".join(cur)); cur, cur_len = [], 0
        cur.append(block); cur_len += len(block)
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def _backoff(attempt, cap=60.0):
    base = min(cap, 2.0 ** attempt)
    return base * 0.5 + random.uniform(0, base * 0.5)


def call_m3(user, max_retries=8):
    body = {"model": MODEL, "max_tokens": MAX_TOKENS, "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user}]}
    headers = {"x-api-key": TOKEN, "authorization": "Bearer " + TOKEN,
               "anthropic-version": "2023-06-01", "content-type": "application/json"}
    last = None
    for attempt in range(1, max_retries + 1):
        last_attempt = attempt == max_retries
        try:
            r = _SESSION.post(BASE + "/v1/messages", headers=headers, json=body, timeout=(15, 210))
            if r.status_code == 200:
                data = r.json()
                text = "".join(b.get("text", "") for b in (data.get("content") or [])
                               if b.get("type") == "text")
                if text and text.strip():
                    return text
                last = RuntimeError("空响应")
            else:
                last = RuntimeError(f"HTTP {r.status_code}")
                if r.status_code not in _RETRYABLE:
                    raise last
        except requests.RequestException as e:
            last = e
        if not last_attempt:
            time.sleep(_backoff(attempt))
    raise RuntimeError(f"翻译失败(重试{max_retries}次): {last}")


def translate_transcript(text):
    paras = transcript_to_paras(text)
    chunks = make_chunks(paras)
    out = [_strip_fences(call_m3(c)) for c in chunks]
    return "\n".join(out), len(chunks)


def collect(data, series):
    todo = []
    for asr in sorted(glob.glob(f"{data}/*/*.asr.json")):
        if series and Path(asr).parent.name != series:
            continue
        zh = asr[:-len(".asr.json")] + ".zh.json"
        if not os.path.exists(zh):
            todo.append((asr, zh))
    return todo


def do_one(asr, zh):
    text = (json.loads(Path(asr).read_text()).get("text") or "").strip()
    if not text:
        raise RuntimeError("asr.json 无 text")
    content_zh, n = translate_transcript(text)
    tmp = zh + ".tmp"
    Path(tmp).write_text(json.dumps(
        {"content_zh": content_zh, "src_chars": len(text), "n_chunks": n,
         "model": MODEL}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, zh)
    return len(content_zh)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("ASR_DATA", "./data"))
    ap.add_argument("--series", default=None)
    ap.add_argument("--once", action="store_true", help="翻完一轮就退出（否则循环接新集）")
    ap.add_argument("--idle-sleep", type=int, default=60)
    args = ap.parse_args()
    if not BASE or not TOKEN:
        raise SystemExit("缺 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN（先 source .env）")

    print(f"[trans] M3={MODEL} 并发={CONCURRENCY} data={args.data}", flush=True)
    idle = 0
    while True:
        todo = collect(args.data, args.series)
        if todo:
            idle = 0
            print(f"[trans] 待翻译 {len(todo)} 集", flush=True)
            done = fail = 0
            with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
                futs = {ex.submit(do_one, a, z): (a, z) for a, z in todo}
                for fut in as_completed(futs):
                    a, z = futs[fut]
                    eid = Path(a).stem.replace(".asr", "")
                    try:
                        n = fut.result(); done += 1
                        print(f"  ✓ [{done+fail}/{len(todo)}] {Path(a).parent.name}/{eid[:10]} {n}字", flush=True)
                    except Exception as e:
                        fail += 1
                        print(f"  ✗ {Path(a).parent.name}/{eid[:10]}: {str(e)[:120]}", flush=True)
            print(f"[trans] 本轮 成功 {done} 失败 {fail}", flush=True)
        if args.once:
            break
        idle += 1
        time.sleep(args.idle_sleep)


if __name__ == "__main__":
    main()
