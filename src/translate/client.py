"""MiniMax-M3 翻译客户端（Anthropic 兼容接口）。

负责单次 messages 调用、重试、以及把完整请求/响应写入 api_call 审计表。
"""
from __future__ import annotations

import json
import time
from typing import Optional

import requests

from common import config, db

_ENDPOINT = config.ANTHROPIC_BASE_URL + "/v1/messages"
_HEADERS = {
    "x-api-key": config.ANTHROPIC_AUTH_TOKEN,
    "authorization": "Bearer " + config.ANTHROPIC_AUTH_TOKEN,
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}


class TranslateError(RuntimeError):
    pass


def call_messages(system: str, user: str, *, article_id: Optional[int] = None,
                  target_lang: str = "zh", max_tokens: Optional[int] = None,
                  max_retries: int = 5) -> dict:
    """调用模型，返回 {'text', 'input_tokens', 'output_tokens'}。每次调用都落库审计。"""
    body = {
        "model": config.TRANSLATE_MODEL,
        "max_tokens": max_tokens or config.TRANSLATE_MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    last_err = None
    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        try:
            resp = requests.post(_ENDPOINT, headers=_HEADERS, json=body, timeout=180)
            latency = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                # content/usage 可能显式为 null（键存在但值为 None，default 不生效）→ 兜底成空
                text = "".join(
                    b.get("text", "") for b in (data.get("content") or [])
                    if b.get("type") == "text")
                usage = data.get("usage") or {}
                in_tok = usage.get("input_tokens")
                out_tok = usage.get("output_tokens")
                _log_call(article_id, target_lang, body, resp.text, in_tok, out_tok,
                          latency, "ok", None)
                if not text.strip():
                    raise TranslateError("空响应")
                return {"text": text, "input_tokens": in_tok, "output_tokens": out_tok}
            # 非 200
            latency = int((time.time() - t0) * 1000)
            _log_call(article_id, target_lang, body, resp.text, None, None,
                      latency, "error", f"HTTP {resp.status_code}")
            last_err = TranslateError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = min(90, 2 ** attempt)
                print(f"    [{resp.status_code}] 退避 {wait}s (第{attempt}次)")
                time.sleep(wait)
                continue
            raise last_err
        except requests.RequestException as e:
            latency = int((time.time() - t0) * 1000)
            _log_call(article_id, target_lang, body, None, None, None, latency,
                      "error", str(e)[:500])
            last_err = e
            wait = min(90, 2 ** attempt)
            print(f"    [网络错误] {e} 退避 {wait}s (第{attempt}次)")
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
