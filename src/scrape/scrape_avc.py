"""Fred Wilson（avc.com，WordPress）文章抓取器。复用 wp_common.scrape_wp。

注：avc.com 本体 2024-05 起停更（Fred 迁往 avc.xyz / Paragraph）；此处抓取近 20 年全量历史存档。

用法：
  python -m scrape.scrape_avc
  python -m scrape.scrape_avc --limit 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scrape.wp_common import scrape_wp   # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    scrape_wp("avc", "https://avc.com", "Fred Wilson", "avc", limit=args.limit)
