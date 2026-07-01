"""把服务器译好的 data/_texttrans/*.done.json 导入本地 translations 表（文本源）。

配合 export_text_jobs.py + 服务器 translate_jobs.py。幂等：src_hash 与当前原文一致才写
（原文已更新则跳过，等重新导出）。导入后可删对应 .job/.done.json（可选）。

用法：
  PYTHONPATH=src .venv/bin/python scripts/import_text_jobs.py           # 导入全部 done.json
  PYTHONPATH=src .venv/bin/python scripts/import_text_jobs.py --clean   # 导入后删任务文件
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]) + "/src")
from common import config, db                       # noqa: E402

JOBDIR = config.DATA_DIR / "_texttrans"


def main():
    clean = "--clean" in sys.argv[1:]
    done_files = sorted(glob.glob(f"{JOBDIR}/*.done.json"))
    imported = stale = 0
    for df in done_files:
        d = json.loads(Path(df).read_text())
        aid = d["id"]
        with db.cursor() as c:
            c.execute("SELECT content_hash FROM articles WHERE id=%s", (aid,))
            row = c.fetchone()
            if not row or row["content_hash"] != d.get("content_hash"):
                stale += 1                     # 原文已变，跳过（等重新导出翻译）
                continue
            c.execute("""INSERT INTO translations
                    (article_id,target_lang,model,title_translated,content_translated,src_hash,status)
                 VALUES (%s,'zh',%s,%s,%s,%s,'done')
                 ON DUPLICATE KEY UPDATE model=VALUES(model),
                    title_translated=VALUES(title_translated),
                    content_translated=VALUES(content_translated),
                    src_hash=VALUES(src_hash),status='done',error=NULL""",
                (aid, d.get("model"), (d.get("title_zh") or "")[:700],
                 d.get("content_zh") or "", d["content_hash"]))
        imported += 1
        if clean:
            Path(df).unlink(missing_ok=True)
            jf = df[:-len(".done.json")] + ".job.json"
            Path(jf).unlink(missing_ok=True)
    print(f"导入 {imported} 篇 · 跳过(原文已变) {stale}{' · 已清任务文件' if clean else ''}")


if __name__ == "__main__":
    main()
