"""MySQL 访问层。用 PyMySQL，封装最常用的查询/写入与几个领域操作。

设计上保持「薄」：脚本里直接写 SQL，这里只提供连接管理和便捷方法，
便于未来接入更多博客来源时复用。
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from typing import Any, Iterable, Optional

import pymysql
from pymysql.cursors import DictCursor

from . import config


def connect():
    return pymysql.connect(
        host=config.DB["host"],
        port=config.DB["port"],
        user=config.DB["user"],
        password=config.DB["password"],
        database=config.DB["database"],
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
    )


@contextmanager
def cursor():
    conn = connect()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ---- 领域便捷方法 -------------------------------------------------------

def get_source_id(source_key: str) -> int:
    with cursor() as cur:
        cur.execute("SELECT id FROM sources WHERE source_key=%s", (source_key,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"source not found: {source_key}")
        return row["id"]


def log_fetch(source_id: Optional[int], url: str, status: Optional[int],
              nbytes: Optional[int], ok: bool, note: str = "") -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO fetch_log (source_id,url,http_status,bytes,ok,note) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (source_id, url[:768], status, nbytes, 1 if ok else 0, note[:512]),
        )


def upsert_article(source_id: int, *, slug: str, url: str, title: str,
                   author: Optional[str], published_at, published_text: Optional[str],
                   raw_html: Optional[str], content_html: Optional[str],
                   content_text: Optional[str], meta: Optional[dict],
                   http_status: Optional[int], is_external: bool = False) -> tuple[int, bool]:
    """插入或更新一篇文章。返回 (article_id, content_changed)。
    content_changed 用于增量翻译：正文 hash 变化才需要重译。
    """
    content_hash = sha256(content_text or "")
    word_count = len((content_text or "").split())
    meta_json = json.dumps(meta, ensure_ascii=False) if meta else None
    with cursor() as cur:
        cur.execute(
            "SELECT id, content_hash FROM articles WHERE source_id=%s AND slug=%s",
            (source_id, slug),
        )
        existing = cur.fetchone()
        if existing:
            changed = existing["content_hash"] != content_hash
            cur.execute(
                """UPDATE articles SET url=%s,title=%s,author=%s,published_at=%s,
                       published_text=%s,word_count=%s,raw_html=%s,content_html=%s,
                       content_text=%s,content_hash=%s,meta_json=%s,http_status=%s,
                       is_external=%s,fetched_at=NOW()
                   WHERE id=%s""",
                (url, title, author, published_at, published_text, word_count,
                 raw_html, content_html, content_text, content_hash, meta_json,
                 http_status, 1 if is_external else 0, existing["id"]),
            )
            return existing["id"], changed
        cur.execute(
            """INSERT INTO articles
                   (source_id,slug,url,title,author,published_at,published_text,
                    word_count,raw_html,content_html,content_text,content_hash,
                    meta_json,http_status,is_external,fetched_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())""",
            (source_id, slug, url, title, author, published_at, published_text,
             word_count, raw_html, content_html, content_text, content_hash,
             meta_json, http_status, 1 if is_external else 0),
        )
        return cur.lastrowid, True


def renumber_chrono(source_id: int) -> int:
    """按 published_at 升序为某来源的非站外文章重排 chrono_index（最早=1）。"""
    with cursor() as cur:
        cur.execute(
            """SELECT id FROM articles
               WHERE source_id=%s AND is_external=0
               ORDER BY published_at IS NULL, published_at ASC, id ASC""",
            (source_id,),
        )
        ids = [r["id"] for r in cur.fetchall()]
        for idx, aid in enumerate(ids, start=1):
            cur.execute("UPDATE articles SET chrono_index=%s WHERE id=%s", (idx, aid))
        return len(ids)
