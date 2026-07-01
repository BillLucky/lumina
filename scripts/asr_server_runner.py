#!/usr/bin/env python3
"""GPU 服务器端批量 ASR runner —— 用官方 qwen_asr 包 + Qwen3-ASR-1.7B 权重跑在 CUDA GPU 上，
把播客音频批量转写成与本地 lumina 完全一致的 <eid>.asr.json，rsync 回本地即可无缝入库、制书。

工程要点（踩过的坑）：
  - 先 ffmpeg 转 16kHz mono wav 再喂模型：直接喂 mp3/m4a 会走慢路径（CPU 卡顿、RTF 飙到 15x）。
  - 限制 BLAS/OMP 线程：多核机器不限会严重超额订阅 → CPU 互相争抢、吞吐趋零。
  - 只认指定 GPU：由启动脚本用 CUDA_VISIBLE_DEVICES 固定到空闲卡，不影响同机其它 GPU 服务。
  - max_new_tokens 默认 512 太小会把长音频块截断，长块需 ~5000，这里放大到 12288。

断点续传 / 增量安全：
  - 已存在 <eid>.asr.json 的音频直接跳过；将来新同步进来的音频，下次运行自动补做、不重复。
  - 原子落盘（先写 .tmp 再 rename），进程被 kill 也不会留半截 json。

输出契约（与本地 mlx 版一致）：<data>/<series>/<eid>.asr.json = {"text": 全文, ...}。
本地 scrape_podcast --no-download 读到该缓存即跳过 ASR，直接 upsert 入库（slug=eid，一集一章）。

用法（路径全走 env/参数，见 run_asr.sh）：
  ASR_MODEL=/path/to/Qwen3-ASR-1.7B ASR_DATA=/path/to/data \
  CUDA_VISIBLE_DEVICES=3 python asr_server_runner.py
"""
import argparse
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

# ── 必须在 import torch 之前限制线程 ──
_TH = os.environ.get("ASR_THREADS", "16")
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_v] = _TH
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import torch                                    # noqa: E402
from qwen_asr import Qwen3ASRModel              # noqa: E402


def to_wav16k(src: Path) -> Path:
    fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="asrin_")
    os.close(fd)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-ar", "16000", "-ac", "1", "-f", "wav", tmp],
        check=True)
    return Path(tmp)


def collect(data: Path, series: str | None):
    """待办 = <series>/audio/<eid>.mp3 且缺 <series>/<eid>.asr.json。"""
    todo, total = [], 0
    for mp3 in sorted(data.glob("*/audio/*.mp3")):
        if series and mp3.parent.parent.name != series:
            continue
        total += 1
        out = mp3.parent.parent / f"{mp3.stem}.asr.json"
        if not out.exists():
            todo.append((mp3, out))
    return todo, total


def main():
    ap = argparse.ArgumentParser()
    # 路径走 env/参数，不硬编码任何具体机器路径（本仓库开源）
    ap.add_argument("--data", default=os.environ.get("ASR_DATA", "./data"),
                    help="音频根目录（含 <series>/audio/*.mp3），或设 ASR_DATA")
    ap.add_argument("--model", default=os.environ.get("ASR_MODEL", "./models/Qwen3-ASR-1.7B"),
                    help="Qwen3-ASR-1.7B 权重目录，或设 ASR_MODEL")
    ap.add_argument("--series", default=None, help="只处理某系列，默认全部")
    ap.add_argument("--language", default="English")
    ap.add_argument("--batch", type=int, default=12, help="模型内部分块的批大小（吞吐↔显存）")
    ap.add_argument("--max-new-tokens", type=int, default=12288,
                    help="每块最大生成 token。默认 512 太小会截断长音频（20 分钟块约需 5000）")
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 条（冒烟测试用）")
    args = ap.parse_args()

    data = Path(args.data)
    todo, total = collect(data, args.series)
    if args.limit:
        todo = todo[:args.limit]
    dev = os.environ.get("CUDA_VISIBLE_DEVICES", "?")
    print(f"[asr] GPU={dev}  待转写 {len(todo)} / 该范围音频 {total}（其余已有 asr.json，跳过）", flush=True)
    if not todo:
        print("[asr] 没有待处理音频，退出。", flush=True)
        return

    print(f"[asr] 加载 {args.model} → CUDA …", flush=True)
    t0 = time.time()
    model = Qwen3ASRModel.from_pretrained(
        args.model, dtype=torch.bfloat16, device_map="cuda",
        max_inference_batch_size=args.batch, max_new_tokens=args.max_new_tokens)
    print(f"[asr] 模型就绪 {time.time()-t0:.0f}s device={getattr(model,'device',None)}", flush=True)

    done = failed = 0
    audio_secs = proc_secs = 0.0
    for i, (mp3, out) in enumerate(todo, 1):
        if out.exists():
            continue
        wav = None
        try:
            wav = to_wav16k(mp3)
            t = time.time()
            res = model.transcribe(str(wav), language=args.language, return_time_stamps=False)
            dt = time.time() - t
            text = (getattr(res[0], "text", "") or "").strip()
            if not text:
                raise RuntimeError("空转写结果")
            tmp = out.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(
                {"text": text, "used_model": "Qwen3-ASR-1.7B", "lang": args.language},
                ensure_ascii=False), encoding="utf-8")
            tmp.replace(out)                     # 原子落盘
            done += 1
            proc_secs += dt
            print(f"  ✓ [{i}/{len(todo)}] {mp3.parent.parent.name}/{mp3.stem[:10]} "
                  f"{len(text)}字 {dt:.0f}s", flush=True)
        except Exception as e:
            failed += 1
            print(f"  ✗ [{i}/{len(todo)}] {mp3.name}: {str(e)[:160]}", flush=True)
        finally:
            if wav and wav.exists():
                wav.unlink()
    print(f"[asr] 完成：成功 {done}，失败 {failed}，累计转写耗时 {proc_secs/60:.1f} 分钟", flush=True)


if __name__ == "__main__":
    main()
