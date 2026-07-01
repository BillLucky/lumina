"""MiniMax-M3 翻译客户端（Anthropic 兼容接口）。

负责单次 messages 调用、重试、以及把完整请求/响应写入 api_call 审计表。

网络鲁棒性（切流量/网络抖动/服务端过载都能扛，配合上层「失败→下轮重译」双保险）：
  - 连接复用：全局 requests.Session + 连接池（pool_maxsize 覆盖并发数），少建连、少 TLS 握手，
    避免高并发下的 SSL EOF。
  - 可重试状态：限流 429、服务端 5xx、**过载 529**（Anthropic 兼容接口最常见）、请求超时 408/425。
  - 指数退避 + **随机抖动**：避免高并发在网络恢复瞬间同时重试造成惊群 → 二次限流。
  - 重试次数放大到 8、单次容忍 ~3–4 分钟：足够覆盖一次「切流量/换网络」的断连窗口。
  - 200 但响应体截断/非 JSON/空 → 也当可重试，不因偶发坏响应丢整篇。
  - 真·永久错误（400/401/413 等 4xx）才立即失败、不空转重试。
"""
from __future__ import annotations

import json
import random
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter

from common import config, db

_ENDPOINT = config.ANTHROPIC_BASE_URL + "/v1/messages"
_HEADERS = {
    "x-api-key": config.ANTHROPIC_AUTH_TOKEN,
    "authorization": "Bearer " + config.ANTHROPIC_AUTH_TOKEN,
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}

# 全局连接池：pool_maxsize 略大于翻译并发，避免连接不够而频繁新建/丢弃
_SESSION = requests.Session()
_ADAPTER = HTTPAdapter(pool_connections=8, pool_maxsize=48)
_SESSION.mount("https://", _ADAPTER)
_SESSION.mount("http://", _ADAPTER)

# 可重试的 HTTP 状态：请求超时 / 限流 / 服务端错误 / 过载(Anthropic 529)
_RETRYABLE = {408, 425, 429, 500, 502, 503, 504, 529}
# (connect, read) 超时：连接 15s 内必须建上，读 210s 容纳长块输出
_TIMEOUT = (15, 210)


class TranslateError(RuntimeError):
    pass


def _backoff(attempt: int, cap: float = 60.0) -> float:
    """指数退避 + 抖动（full jitter）。attempt 从 1 起。"""
    base = min(cap, 2.0 ** attempt)
    return base * 0.5 + random.uniform(0, base * 0.5)


def call_messages(system: str, user: str, *, article_id: Optional[int] = None,
                  target_lang: str = "zh", max_tokens: Optional[int] = None,
                  max_retries: int = 8) -> dict:
    """调用模型，返回 {'text', 'input_tokens', 'output_tokens'}。每次调用都落库审计。"""
    body = {
        "model": config.TRANSLATE_MODEL,
        "max_tokens": max_tokens or config.TRANSLATE_MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    last_err = None
    for attempt in range(1, max_retries + 1):
        last_attempt = attempt == max_retries
        t0 = time.time()
        try:
            resp = _SESSION.post(_ENDPOINT, headers=_HEADERS, json=body, timeout=_TIMEOUT)
            latency = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                # 防御式解析：200 但响应体截断/非 JSON/content 为 null/空 → 视为可重试
                try:
                    data = resp.json()
                    text = "".join(
                        b.get("text", "") for b in (data.get("content") or [])
                        if b.get("type") == "text")
                    usage = data.get("usage") or {}
                    in_tok = usage.get("input_tokens")
                    out_tok = usage.get("output_tokens")
                except Exception as pe:
                    _log_call(article_id, target_lang, body, resp.text[:2000], None, None,
                              latency, "error", f"解析失败: {pe}")
                    last_err = TranslateError(f"响应解析失败: {pe}")
                    if not last_attempt:
                        time.sleep(_backoff(attempt))
                    continue
                if not (text and text.strip()):
                    _log_call(article_id, target_lang, body, resp.text[:2000], in_tok,
                              out_tok, latency, "error", "空响应")
                    last_err = TranslateError("空响应")
                    if not last_attempt:
                        time.sleep(_backoff(attempt))
                    continue
                _log_call(article_id, target_lang, body, resp.text, in_tok, out_tok,
                          latency, "ok", None)
                return {"text": text, "input_tokens": in_tok, "output_tokens": out_tok}
            # 非 200
            latency = int((time.time() - t0) * 1000)
            _log_call(article_id, target_lang, body, resp.text, None, None,
                      latency, "error", f"HTTP {resp.status_code}")
            last_err = TranslateError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            if resp.status_code in _RETRYABLE:
                if not last_attempt:
                    wait = _backoff(attempt)
                    print(f"    [{resp.status_code}] 退避 {wait:.0f}s (第{attempt}/{max_retries}次)")
                    time.sleep(wait)
                continue
            raise last_err   # 真·永久错误（400/401/413 等）：不重试
        except requests.RequestException as e:
            latency = int((time.time() - t0) * 1000)
            _log_call(article_id, target_lang, body, None, None, None, latency,
                      "error", str(e)[:500])
            last_err = e
            if not last_attempt:
                wait = _backoff(attempt)
                print(f"    [网络错误] {type(e).__name__} 退避 {wait:.0f}s (第{attempt}/{max_retries}次)")
                time.sleep(wait)
    raise TranslateError(f"调用失败(已重试{max_retries}次): {last_err}")


def _log_call(article_id, target_lang, body, response_text, in_tok, out_tok,
              latency, status, error):
    try:
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO api_call
                       (article_id,target_lang,model,request_json,response_json,
                        prompt_tokens,completion_tokens,latency_ms,status,error)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (article_id, target_lang, config.TRANSLATE_MODEL,
                 json.dumps(body, ensure_ascii=False), response_text,
                 in_tok, out_tok, latency, status, error))
    except Exception as e:  # 审计失败不应中断翻译
        print(f"    [warn] api_call 落库失败: {e}")
