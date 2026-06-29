"""日期解析工具：把 'July 2023' / '2004' 这类文本转成可排序的 datetime。"""
import re
from datetime import datetime

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}

# "July 2023" / "Jul 2023" / "2004" / "January 2009"
_MONTH_YEAR = re.compile(
    r"\b(" + "|".join(_MONTHS) + r"|" +
    "|".join(m[:3] for m in _MONTHS) + r")\.?\s+(\d{4})\b", re.IGNORECASE)
_YEAR_ONLY = re.compile(r"\b(19|20)\d{2}\b")


# "Posted on June 18, 2007" / "June 18, 2007" / "18 June 2007"
_FULL_MDY = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.IGNORECASE)


def parse_full_date(text: str):
    """解析含「日」的完整日期，返回 (datetime|None, matched_text|None)。"""
    if not text:
        return None, None
    m = _FULL_MDY.search(text)
    if m:
        month = _MONTHS[m.group(1).lower()]
        return datetime(int(m.group(3)), month, int(m.group(2))), m.group(0)
    return parse_leading_date(text)


def parse_leading_date(text: str):
    """从正文开头一小段中提取发布日期。
    返回 (datetime|None, matched_text|None)。matched_text 用于从正文中剥离日期行。
    """
    head = (text or "")[:120]
    m = _MONTH_YEAR.search(head)
    if m:
        mon = m.group(1).lower()[:3]
        month = next((v for k, v in _MONTHS.items() if k.startswith(mon)), 1)
        year = int(m.group(2))
        return datetime(year, month, 1), m.group(0)
    m = _YEAR_ONLY.search(head)
    if m:
        return datetime(int(m.group(0)), 1, 1), m.group(0)
    return None, None
