"""集中读取 .env 配置。所有脚本统一从这里取参数。"""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# ---- 路径 ----
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"
for _d in (DATA_DIR, OUTPUT_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---- 数据库 ----
DB = dict(
    host=os.getenv("DB_HOST", "127.0.0.1"),
    port=int(os.getenv("DB_PORT", "3307")),
    user=os.getenv("DB_USER", "blog"),
    password=os.getenv("DB_PASSWORD", "blogpass"),
    database=os.getenv("DB_NAME", "blogbook"),
)

# ---- 抓取礼貌策略 ----
HTTP_USER_AGENT = os.getenv("HTTP_USER_AGENT", "blogbook-archiver/1.0")
FETCH_MIN_DELAY = float(os.getenv("FETCH_MIN_DELAY", "1.5"))
FETCH_MAX_DELAY = float(os.getenv("FETCH_MAX_DELAY", "3.0"))
FETCH_TIMEOUT = int(os.getenv("FETCH_TIMEOUT", "30"))
FETCH_MAX_RETRIES = int(os.getenv("FETCH_MAX_RETRIES", "4"))

# ---- 翻译 ----
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic").rstrip("/")
ANTHROPIC_AUTH_TOKEN = os.getenv("ANTHROPIC_AUTH_TOKEN", "")
TRANSLATE_MODEL = os.getenv("TRANSLATE_MODEL", "MiniMax-M3")
TRANSLATE_CONCURRENCY = int(os.getenv("TRANSLATE_CONCURRENCY", "4"))
TRANSLATE_MAX_TOKENS = int(os.getenv("TRANSLATE_MAX_TOKENS", "32768"))
