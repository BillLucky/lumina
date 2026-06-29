"""礼貌型 HTTP 抓取器。

原则（不对目标站点造成压力）：
  - 单线程串行抓取，每次请求之间随机 sleep [FETCH_MIN_DELAY, FETCH_MAX_DELAY]
  - 固定一个可识别的 User-Agent，便于站方追溯
  - 指数退避重试；遇 429 / 5xx 等待更久
  - 每次请求写 fetch_log 审计表
"""
from __future__ import annotations

import random
import time
from typing import Optional

import requests

from . import config, db


class PoliteFetcher:
    def __init__(self, source_id: Optional[int] = None,
                 min_delay: float = None, max_delay: float = None):
        self.source_id = source_id
        self.min_delay = config.FETCH_MIN_DELAY if min_delay is None else min_delay
        self.max_delay = config.FETCH_MAX_DELAY if max_delay is None else max_delay
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config.HTTP_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en;q=0.9",
        })
        self._last_request_at = 0.0

    def _throttle(self):
        elapsed = time.time() - self._last_request_at
        delay = random.uniform(self.min_delay, self.max_delay)
        if elapsed < delay:
            time.sleep(delay - elapsed)

    def get(self, url: str, *, as_json: bool = False, note: str = ""):
        """抓取一个 URL，返回 requests.Response。失败抛异常。"""
        last_exc = None
        for attempt in range(1, config.FETCH_MAX_RETRIES + 1):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=config.FETCH_TIMEOUT)
                self._last_request_at = time.time()
                nbytes = len(resp.content)
                ok = resp.status_code == 200
                db.log_fetch(self.source_id, url, resp.status_code, nbytes, ok,
                             note or (f"attempt {attempt}"))
                if resp.status_code == 200:
                    return resp
                if resp.status_code in (429, 500, 502, 503, 504):
                    wait = min(60, 2 ** attempt + random.uniform(0, 2))
                    print(f"  [{resp.status_code}] {url} -> 退避 {wait:.1f}s (第{attempt}次)")
                    time.sleep(wait)
                    continue
                # 4xx（非429）通常重试无意义
                resp.raise_for_status()
            except requests.RequestException as e:
                last_exc = e
                self._last_request_at = time.time()
                wait = min(60, 2 ** attempt + random.uniform(0, 2))
                print(f"  [error] {url}: {e} -> 退避 {wait:.1f}s (第{attempt}次)")
                db.log_fetch(self.source_id, url, None, None, False, str(e)[:500])
                time.sleep(wait)
        raise RuntimeError(f"抓取失败(已重试{config.FETCH_MAX_RETRIES}次): {url}: {last_exc}")
