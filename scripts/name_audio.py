"""把 UUID 命名的播客音频，整理成「序号-中文标题（English Title）.mp3」可读名，便于查阅。

做法（安全、不破坏管线）：
  - 原始 data/<series>/audio/<eid>.mp3 **保持不动**（ASR 按 eid 查找依赖它）。
  - 在 data/<series>/audio_named/ 下建**软链接**：<NNN>-<中文>（<English>）.mp3 → ../audio/<eid>.mp3。
  - 序号按发布时间正序（与书一致）；中文标题取自已完成的译文，没有则仅英文。
  - 另写 manifest.tsv（序号/日期/中文/英文/原文件）便于检索。
  - 可重复运行：随翻译进度补全中文名（会重建软链接）。

用法：
  PYTHONPATH=src .venv/bin/python scripts/name_audio.py            # 全部系列
  PYTHONPATH=src .venv/bin/python scripts/name_audio.py --series a16z
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]) + "/src")
from common import config, db                       # noqa: E402
from scrape.scrape_podcast import SERIES, parse_feed  # noqa: E402

_BAD = re.compile(r'[/\\:*?"<>|\n\r\t]+')


def sanitize(name: str, maxlen: int = 120) -> str:
    name = _BAD.sub(" ", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:maxlen].strip()


def zh_titles(source_key: str) -> dict:
    """eid(slug) → 中文标题（已完成的 zh 译文标题）。"""
    out = {}
    with db.cursor() as c:
        c.execute("""SELECT a.slug, t.title_translated
                     FROM articles a JOIN sources s ON s.id=a.source_id
                     LEFT JOIN translations t ON t.article_id=a.id
                          AND t.target_lang='zh' AND t.status='done'
                     WHERE s.source_key=%s""", (source_key,))
        for r in c.fetchall():
            if r["title_translated"]:
                out[r["slug"]] = r["title_translated"]
    return out


def name_series(source_key: str):
    s = SERIES.get(source_key)
    if not s:
        print(f"  跳过未知系列 {source_key}")
        return
    audio_dir = config.DATA_DIR / source_key / "audio"
    if not audio_dir.exists():
        print(f"  [{source_key}] 无音频目录，跳过")
        return
    named_dir = config.DATA_DIR / source_key / "audio_named"
    named_dir.mkdir(parents=True, exist_ok=True)
    # 清掉旧软链接（保证可重复运行）
    for old in named_dir.glob("*.mp3"):
        if old.is_symlink():
            old.unlink()

    eps = parse_feed(s["feed"])                       # 发布时间倒序
    eps = list(reversed(eps))                         # 转为正序（最早=1）
    zh = zh_titles(source_key)

    manifest = ["序号\t日期\t中文标题\t英文标题\t原文件"]
    cnt = 0
    for idx, ep in enumerate(eps, 1):
        mp3 = audio_dir / f"{ep['eid']}.mp3"
        if not mp3.exists():
            continue
        en = ep["title"] or ep["eid"]
        zh_t = zh.get(ep["eid"], "")
        label = f"{zh_t}（{en}）" if zh_t else en
        fname = f"{idx:03d}-{sanitize(label)}.mp3"
        link = named_dir / fname
        try:
            link.symlink_to(Path("..") / "audio" / f"{ep['eid']}.mp3")
            cnt += 1
        except FileExistsError:
            pass
        date = ep["published_at"].strftime("%Y-%m-%d") if ep["published_at"] else "?"
        manifest.append(f"{idx:03d}\t{date}\t{zh_t}\t{en}\t{ep['eid']}.mp3")

    (named_dir / "manifest.tsv").write_text("\n".join(manifest), encoding="utf-8")
    zh_n = sum(1 for ep in eps if ep["eid"] in zh and (audio_dir / f"{ep['eid']}.mp3").exists())
    print(f"  [{source_key}] 命名 {cnt} 个（其中带中文名 {zh_n}）→ {named_dir}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", default=None, help="单系列；默认全部播客系列")
    args = ap.parse_args()
    keys = [args.series] if args.series else list(SERIES.keys())
    print("整理音频可读名（软链接 + manifest.tsv）...")
    for k in keys:
        name_series(k)
    print("完成。浏览 data/<系列>/audio_named/ 即可按「序号-中文（英文）」查阅。")


if __name__ == "__main__":
    main()
