#!/usr/bin/env python3
"""服务器端文本翻译器 —— 把本地导出的 <source>__<id>.job.json 逐块译成 <...>.done.json。

配合 export_text_jobs.py(本地导出/分块) + import_text_jobs.py(本地导入)。本脚本零 DB/零 bs4，
只需 requests + M3 token（机房稳网，避开本地切流量断连）。断点续传：已有 .done.json 跳过；
循环扫描接住新导入的 job。原子落盘。

env（同 translate_asr.py，可复用同一个 .env）：ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN /
  TRANSLATE_MODEL / TRANSLATE_MAX_TOKENS / TRANSLATE_CONCURRENCY / ASR_DATA(任务在 <ASR_DATA>/_texttrans)

用法（tmux 内）：
  set -a; . .env; set +a
  python translate_jobs.py            # 循环模式，接住新 job
  python translate_jobs.py --once
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
JOBDIR = Path(os.environ.get("ASR_DATA", "./data")) / "_texttrans"

CONTENT_SYS = (
    "你是一位殿堂级的中英文翻译大家，译笔融汇严复「信达雅」、傅雷「神似」、余光中之文采。\n"
    "【准则】信：忠实原文论证与言外之意，概念/数据/专名精确；达：摆脱翻译腔，地道流畅当代书面中文；"
    "雅：再现节奏、语气、锋芒，用词凝练。全篇术语人名统一。\n"
    "【格式】输入是 HTML 片段，原样保留所有 HTML 标签（<p><h1><ul><li><blockquote><a><i><b>"
    "<code><pre><img> 等），只译标签内自然语言；<a> 的 href、<img> 的 src、代码、URL 一律原样保留；"
    "专名有通行译名用通行译名，技术/品牌名可保留英文；只输出翻译后的 HTML 片段本身，不要任何解释或代码围栏。"
)
TITLE_SYS = "你是顶尖中英翻译家。把给定英文标题译成简洁优雅的简体中文，只输出译文本身。"

_FENCE = re.compile(r"^\s*```(?:html)?\s*|\s*```\s*$", re.IGNORECASE)
_SESSION = requests.Session()
_SESSION.mount("https://", HTTPAdapter(pool_connections=8, pool_maxsize=32))
_RETRYABLE = {408, 425, 429, 500, 502, 503, 504, 529}


def _strip(t):
    return _FENCE.sub("", t).strip()


def _backoff(a, cap=60.0):
    base = min(cap, 2.0 ** a)
    return base * 0.5 + random.uniform(0, base * 0.5)


def call_m3(system, user, max_tokens=None, max_retries=8):
    body = {"model": MODEL, "max_tokens": max_tokens or MAX_TOKENS, "system": system,
            "messages": [{"role": "user", "content": user}]}
    headers = {"x-api-key": TOKEN, "authorization": "Bearer " + TOKEN,
               "anthropic-version": "2023-06-01", "content-type": "application/json"}
    last = None
    for attempt in range(1, max_retries + 1):
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
        if attempt < max_retries:
            time.sleep(_backoff(attempt))
    raise RuntimeError(f"失败(重试{max_retries}次): {last}")


def do_job(job_path):
    job = json.loads(Path(job_path).read_text())
    title_zh = _strip(call_m3(TITLE_SYS, job["title"], max_tokens=200)).strip('"').strip("「」")[:700]
    content_zh = "\n".join(_strip(call_m3(CONTENT_SYS, ch)) for ch in job["chunks"])
    done = job_path[:-len(".job.json")] + ".done.json"
    tmp = done + ".tmp"
    Path(tmp).write_text(json.dumps(
        {"id": job["id"], "source": job["source"], "content_hash": job["content_hash"],
         "title_zh": title_zh, "content_zh": content_zh, "model": MODEL},
        ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, done)
    return len(content_zh)


def collect():
    todo = []
    for j in sorted(glob.glob(f"{JOBDIR}/*.job.json")):
        if not os.path.exists(j[:-len(".job.json")] + ".done.json"):
            todo.append(j)
    return todo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--idle-sleep", type=int, default=60)
    args = ap.parse_args()
    if not BASE or not TOKEN:
        raise SystemExit("缺 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN")
    print(f"[jobs] M3={MODEL} 并发={CONCURRENCY} 任务目录={JOBDIR}", flush=True)
    while True:
        todo = collect()
        if todo:
            print(f"[jobs] 待翻译 {len(todo)} 篇", flush=True)
            done = fail = 0
            with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
                futs = {ex.submit(do_job, j): j for j in todo}
                for fut in as_completed(futs):
                    j = Path(futs[fut]).name
                    try:
                        n = fut.result(); done += 1
                        print(f"  ✓ [{done+fail}/{len(todo)}] {j} {n}字", flush=True)
                    except Exception as e:
                        fail += 1
                        print(f"  ✗ {j}: {str(e)[:120]}", flush=True)
            print(f"[jobs] 本轮 成功 {done} 失败 {fail}", flush=True)
        if args.once:
            break
        time.sleep(args.idle_sleep)


if __name__ == "__main__":
    main()
