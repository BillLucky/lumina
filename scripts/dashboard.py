"""lumina 全局实时看板（类 top/watch，自刷新 CLI 面板）。

一眼看全所有环节进度：抓取 / 下载 / 本地 ASR 转写 / M3 翻译 / 导读 / 出书。
数据来自 MySQL（单一事实源）+ 磁盘扫描，无需额外埋点。

用法（推荐用仓库根目录的 ./lumina 启动器，免设环境变量）：
  ./lumina              # 自刷新（默认 3s）
  ./lumina -n 5         # 5 秒刷新
  ./lumina --once       # 只打印一次（给 watch/管道用）
  按 Ctrl-C 退出。

鲁棒性：MySQL 抖动/重启时只做 1 次快速探测（retries=1），不阻塞刷新；
DB 不可达时降级渲染（仍显示磁盘侧的 ASR/出书/各 lane 存活）。
"""
from __future__ import annotations

import argparse
import glob
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]) + "/src")
from common import config, db                       # noqa: E402
from book.build_book import BOOK_META               # noqa: E402
from scrape.scrape_podcast import SERIES            # noqa: E402

G, Y, R, C, DIM, B, RS = ("\033[32m", "\033[33m", "\033[31m", "\033[36m",
                          "\033[2m", "\033[1m", "\033[0m")
LANES = ["a16z_grind.sh", "text_grind.sh", "text_grind2.sh",
         "pipe_avc.sh", "pipe_gwern.sh", "pipe_cleanup.sh", "watchdog.sh"]
PODCAST = set(SERIES.keys())
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def bar(done, total, width=22):
    pct = (done / total * 100) if total else 0
    fill = int(pct / 100 * width)
    col = G if pct >= 100 else (C if pct >= 50 else (Y if pct > 0 else DIM))
    return f"{col}{'█'*fill}{DIM}{'░'*(width-fill)}{RS} {col}{pct:4.0f}%{RS}"


def alive(script):
    return subprocess.run(["pgrep", "-f", script], capture_output=True).returncode == 0


def counts():
    """source_key -> dict(arts, trans, failed, summ)。DB 不可达时抛异常，由调用方降级。"""
    out = {}
    with db.cursor(retries=1) as c:
        c.execute("""SELECT s.source_key,
               COUNT(DISTINCT CASE WHEN a.is_external=0 THEN a.id END) arts,
               SUM(CASE WHEN t.status='done' THEN 1 ELSE 0 END) trans,
               SUM(CASE WHEN t.status='failed' THEN 1 ELSE 0 END) failed,
               COUNT(DISTINCT su.id) summ
           FROM sources s
           LEFT JOIN articles a ON a.source_id=s.id
           LEFT JOIN translations t ON t.article_id=a.id AND t.target_lang='zh'
           LEFT JOIN summaries su ON su.article_id=a.id
           GROUP BY s.id""")
        for r in c.fetchall():
            out[r["source_key"]] = dict(arts=r["arts"] or 0, trans=r["trans"] or 0,
                                        failed=r["failed"] or 0, summ=r["summ"] or 0)
    return out


def audio_stats():
    """各播客系列：已下 mp3 数、已转写 asr.json 数。"""
    dl = asr = 0
    for k in SERIES:
        dl += len(glob.glob(f"{config.DATA_DIR}/{k}/audio/*.mp3"))
        asr += len(glob.glob(f"{config.DATA_DIR}/{k}/*.asr.json"))
    return dl, asr


def book_count():
    return len(glob.glob(f"{config.OUTPUT_DIR}/books/*/*.epub"))


def render(tick=0):
    ts = datetime.now().strftime("%H:%M:%S")
    spin = SPIN[tick % len(SPIN)]
    L = []
    L.append(f"{B}{C} lumina 全局看板 {RS} {C}{spin}{RS} {ts}   "
             + "  ".join(f"{(G+'●'+RS) if alive(s) else (R+'○'+RS)} {s[:-3]}" for s in LANES))
    L.append(DIM + "─" * 78 + RS)

    # ASR（本地转写，纯磁盘扫描，DB 挂了也能显示）—— 播客瓶颈
    dl, asr = audio_stats()
    L.append(f"{B}🎧 本地 ASR 转写{RS}   {bar(asr, dl)}   {asr}/{dl} 集已下载")
    L.append("")

    # 翻译（M3）—— 依赖 DB；不可达则降级
    try:
        cs = counts()
    except Exception as e:
        L.append(f"{R}⚠ MySQL 不可达{RS}  {DIM}({str(e)[:60]}) — Docker 可能在重启，看板不阻塞，稍后自动恢复{RS}")
        L.append(DIM + " Ctrl-C 退出 · 数据源 MySQL + 磁盘扫描" + RS)
        return "\n".join(L)

    keys = sorted({k for (k, _) in BOOK_META})
    L.append(f"{B}🌐 M3 翻译进度{RS}   {DIM}（译文 / 文章·集，✗ 为待重试的失败）{RS}")
    rows = [(k, cs.get(k, dict(arts=0, trans=0, failed=0, summ=0))) for k in keys]
    rows = [r for r in rows if r[1]["arts"] > 0]
    # 未完成的排前面（剩余多优先），完成的折叠到底部
    pend = sorted([r for r in rows if r[1]["trans"] < r[1]["arts"]],
                  key=lambda r: r[1]["arts"] - r[1]["trans"], reverse=True)
    done = [r for r in rows if r[1]["trans"] >= r[1]["arts"]]
    for k, c in pend:
        tag = "🎧" if k in PODCAST else "  "
        fail = f"  {R}✗{c['failed']}{RS}" if c["failed"] else ""
        L.append(f"  {tag}{k:20} {bar(c['trans'], c['arts'])}  {c['trans']:>5}/{c['arts']:<5}{fail}")
    if done:
        L.append(f"  {G}✓ 已译完{RS} ({len(done)}): " + " ".join(k for k, _ in done))
    L.append("")

    # 出书
    tot_books = sum(1 for k in keys for lg in ("en", "zh")
                    if glob.glob(f"{config.OUTPUT_DIR}/books/{k}/{k}_{lg}.epub"))
    L.append(f"{B}📚 已出书{RS}   {tot_books} 本（en+zh 各计）   {DIM}详见 output/INDEX.md{RS}")

    # 总进度
    ta = sum(c["arts"] for c in cs.values())
    tt = sum(c["trans"] for c in cs.values())
    tf = sum(c["failed"] for c in cs.values())
    L.append(DIM + "─" * 78 + RS)
    ftxt = f"   {R}✗{tf} 待重试{RS}" if tf else ""
    L.append(f"{B}总进度{RS}  翻译 {bar(tt, ta, 30)}  {tt}/{ta}{ftxt}     ASR {bar(asr, dl, 14)}")
    L.append(DIM + " Ctrl-C 退出 · 数据源 MySQL + 磁盘扫描" + RS)
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="lumina 全局实时看板")
    ap.add_argument("-n", "--interval", type=float, default=3, help="刷新间隔秒（默认3）")
    ap.add_argument("--once", action="store_true", help="只打印一次")
    args = ap.parse_args()
    if args.once:
        print(render())
        return
    tick = 0
    try:
        while True:
            out = render(tick)
            sys.stdout.write("\033[H\033[J")        # 光标归位 + 清屏
            sys.stdout.write(out + "\n")
            sys.stdout.flush()
            tick += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
