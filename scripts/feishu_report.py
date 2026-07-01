#!/usr/bin/env python3
"""服务器端飞书通知器 —— 每小时推「状态看板卡片」+ 关键里程碑即时推送。

跑在服务器上（tmux 常驻），把下载/ASR/播客翻译/文本翻译进度定期同步到飞书群机器人，
手机上即可看。里程碑不频繁（每整百集转写、每整两百篇文本、系列/全部完成才推）。

webhook 从 env/文件读，**绝不硬编码**（保密）：FEISHU_WEBHOOK。数据目录 ASR_DATA。

用法（tmux 内）：
  set -a; . .env; set +a
  python feishu_report.py                 # 循环：每 3600s 推看板 + 里程碑
  python feishu_report.py --once          # 立刻推一次看板
  python feishu_report.py --interval 1800 # 半小时一次
"""
import argparse
import glob
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
DATA = Path(os.environ.get("ASR_DATA", "./data"))
STATE = DATA.parent / "feishu_state.json"
FEED_TOTAL = 1686   # 已知 8 系列 feed 合计（近似，用于下载进度分母）


def post(payload):
    try:
        req = urllib.request.Request(WEBHOOK, json.dumps(payload).encode(),
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception as e:
        print(f"[feishu] 发送失败: {e}", flush=True)
        return False


def text_msg(t):
    return post({"msg_type": "text", "content": {"text": t}})


def bar(done, total, width=16):
    pct = (done / total * 100) if total else 0
    fill = int(pct / 100 * width)
    return f"{'█'*fill}{'░'*(width-fill)} {pct:.0f}%"


def gpu():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,power.draw,temperature.gpu",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
        line = out.strip().splitlines()[3]        # GPU3
        u, p, t = [x.strip() for x in line.split(",")]
        return f"{u}% · {float(p):.0f}W · {t}°C"
    except Exception:
        return "n/a"


def stats():
    mp3 = len(glob.glob(f"{DATA}/*/audio/*.mp3"))
    asr = len(glob.glob(f"{DATA}/*/*.asr.json"))
    zh = len(glob.glob(f"{DATA}/*/*.zh.json"))
    tj = len(glob.glob(f"{DATA}/_texttrans/*.job.json"))
    td = len(glob.glob(f"{DATA}/_texttrans/*.done.json"))
    return dict(mp3=mp3, asr=asr, zh=zh, tj=tj, td=td)


def card(s):
    md = (
        f"**📥 下载**  {bar(s['mp3'], FEED_TOTAL)}  {s['mp3']}/{FEED_TOTAL} 集\n"
        f"**🎧 ASR 转写**  {bar(s['asr'], s['mp3'])}  {s['asr']}/{s['mp3']}\n"
        f"**🌐 播客翻译**  {bar(s['zh'], s['asr'])}  {s['zh']}/{s['asr']}\n"
        f"**📝 文本翻译**  {bar(s['td'], s['tj'])}  {s['td']}/{s['tj']}\n"
        f"**🖥️ GPU3**  {gpu()}"
    )
    return {"msg_type": "interactive", "card": {
        "header": {"title": {"tag": "plain_text", "content": "📊 lumina 服务器状态"},
                   "template": "blue"},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": md}},
                     {"tag": "note", "elements": [{"tag": "plain_text",
                      "content": time.strftime("%Y-%m-%d %H:%M")}]}]}}


def milestones(prev, s):
    """返回要即时推送的里程碑文本列表（不频繁）。"""
    msgs = []
    for key, step, label in [("asr", 100, "🎧 ASR 转写"), ("td", 200, "📝 文本翻译")]:
        if s[key] // step > prev.get(key, 0) // step and s[key] > 0:
            msgs.append(f"{label} 已完成 {s[key]} 篇 🎉")
    if s["mp3"] >= FEED_TOTAL and prev.get("mp3", 0) < FEED_TOTAL:
        msgs.append(f"📥 全部音频下载完成（{s['mp3']} 集）✅")
    if s["asr"] >= s["mp3"] > 0 and prev.get("asr", 0) < prev.get("mp3", 1):
        msgs.append(f"🎧 全部 ASR 转写完成（{s['asr']} 集）✅")
    if s["td"] >= s["tj"] > 0 and prev.get("td", 0) < s["tj"]:
        msgs.append(f"📝 全部文本翻译完成（{s['td']} 篇）✅")
    return msgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=3600)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    if not WEBHOOK:
        raise SystemExit("缺 FEISHU_WEBHOOK")
    prev = {}
    if STATE.exists():
        try:
            prev = json.loads(STATE.read_text())
        except Exception:
            prev = {}
    while True:
        s = stats()
        for m in milestones(prev, s):        # 里程碑即时推
            text_msg("lumina · " + m)
        post(card(s))                        # 看板
        prev = s
        STATE.write_text(json.dumps(s))
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
