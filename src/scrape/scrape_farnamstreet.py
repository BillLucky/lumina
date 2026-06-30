"""Farnam Street / Shane Parrish（fs.blog，WordPress）文章抓取器。复用 wp_common.scrape_wp。

用法：
  python -m scrape.scrape_farnamstreet
  python -m scrape.scrape_farnamstreet --limit 5
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
    scrape_wp("farnamstreet", "https://fs.blog", "Shane Parrish",
              "farnamstreet", limit=args.limit)
