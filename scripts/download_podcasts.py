#!/usr/bin/env python3
"""服务器端播客下载器 —— 直接从 Simplecast feed 把音频下到 data/<series>/audio/<eid>.mp3。

放服务器跑（机房网稳、不占本地流量）。下完的 mp3 由 asr_server_runner(luminaasr) 自动转写、
translate_asr(luminatrans) 自动翻译，全链路服务器闭环。本地只需 rsync 回 asr.json/zh.json 出书。

自包含（仅 stdlib），与本地 scrape_podcast 同一份 feed 列表 + 同一个 eid 规则（awEpisodeId），
故服务器与本地按同一 eid 命名，**同一集不会重复下载、两边天然一致**。

断点续传 / 增量：已存在且 >500KB 的 mp3 跳过；.part 临时文件 + 原子 rename，中断可续跑。

用法（tmux 内）：
  python download_podcasts.py                 # 全部系列(小→大)
  python download_podcasts.py --series a16z_live a16z_raising_health
  python download_podcasts.py --workers 6
"""
import argparse
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime  # noqa: F401 (保持与本地解析一致的依赖面)
from pathlib import Path

UA = "Mozilla/5.0 (lumina podcast fetcher)"
DATA = Path(os.environ.get("ASR_DATA", "./data"))

SERIES = {
    "a16z": "https://feeds.simplecast.com/JGE3yC0V",
    "a16z_raising_health": "https://feeds.simplecast.com/BXDamaKF",
    "a16z_live": "https://feeds.simplecast.com/AuWJKpna",
    "a16z_crypto": "https://feeds.simplecast.com/XPOpH7r4",
    "a16z_ai": "https://feeds.simplecast.com/Hb_IuXOo",
    "a16z_16min": "https://feeds.simplecast.com/j9kKMsfH",
    "a16z_benmarc": "https://feeds.simplecast.com/mAT9rqvu",
    "a16z_hotline": "https://feeds.simplecast.com/kBzSlpXS",
}
ORDER = ["a16z_hotline", "a16z_benmarc", "a16z_16min", "a16z_ai",
         "a16z_crypto", "a16z_live", "a16z_raising_health", "a16z"]


def episode_id(audio_url, guid):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(audio_url).query)
    eid = (q.get("awEpisodeId") or q.get("awEpisodeID") or [None])[0]
    if eid:
        return eid
    tail = re.sub(r"[^a-zA-Z0-9]+", "-", (guid or audio_url).split("/")[-1]).strip("-")
    return tail[:64] or "ep"


def parse_feed(feed_url):
    req = urllib.request.Request(feed_url, headers={"User-Agent": UA})
    xml = urllib.request.urlopen(req, timeout=40).read()
    root = ET.fromstring(xml)
    eps = []
    for it in root.findall(".//item"):
        enc = it.find("enclosure")
        if enc is None or not enc.get("url"):
            continue
        url = enc.get("url")
        eps.append((episode_id(url, it.findtext("guid") or ""), url))
    return eps


def fetch(url, dst: Path, tries=4):
    """下载单集（urllib 自动跟随 mgln.ai→CDN 302）。.part 临时 + 原子 rename。"""
    if dst.exists() and dst.stat().st_size > 500_000:
        return "skip"
    part = dst.with_suffix(".mp3.part")
    last = None
    for t in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as f:
                while True:
                    b = r.read(1 << 16)
                    if not b:
                        break
                    f.write(b)
            if part.stat().st_size < 200_000:
                raise RuntimeError(f"文件过小 {part.stat().st_size}B")
            os.replace(part, dst)
            return "ok"
        except Exception as e:
            last = e
    if part.exists():
        part.unlink(missing_ok=True)
    raise RuntimeError(f"下载失败(重试{tries}次): {last}")


def run(series_list, workers):
    for key in series_list:
        audio = DATA / key / "audio"
        audio.mkdir(parents=True, exist_ok=True)
        try:
            eps = parse_feed(SERIES[key])
        except Exception as e:
            print(f"[{key}] feed 解析失败: {e}", flush=True)
            continue
        todo = [(eid, url) for eid, url in eps
                if not ((audio / f"{eid}.mp3").exists() and (audio / f"{eid}.mp3").stat().st_size > 500_000)]
        print(f"[{key}] feed {len(eps)} 集，待下载 {len(todo)}", flush=True)
        ok = skip = fail = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(fetch, url, audio / f"{eid}.mp3"): eid for eid, url in todo}
            for fut in as_completed(futs):
                eid = futs[fut]
                try:
                    r = fut.result()
                    ok += (r == "ok"); skip += (r == "skip")
                    if (ok + skip) % 20 == 0:
                        print(f"  [{key}] {ok+skip}/{len(todo)}", flush=True)
                except Exception as e:
                    fail += 1
                    print(f"  ✗ [{key}] {eid[:10]}: {str(e)[:100]}", flush=True)
        print(f"[{key}] 完成：下载 {ok} 跳过 {skip} 失败 {fail}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", nargs="*", default=None)
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()
    series = args.series or ORDER
    run(series, args.workers)
    print("==================== 下载全部完成 ====================", flush=True)


if __name__ == "__main__":
    main()
