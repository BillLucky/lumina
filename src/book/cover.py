"""书籍封面生成器（纯 PIL 排版式封面，无需联网/外部图）。

深色珠宝色背景 + 象牙白衬线标题 + 金色点缀，每位作者一套主题色；
中文用宋体、英文用 Georgia。原作者名醒目，译者/来源/开源仓库等信息谦逊地置于页脚。
支持多种页脚排版变体(variant)，便于挑选统一风格。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1600, 2400
MARGIN = 150

THEMES = {
    "paulgraham":      ("#16203b", "#c9a86a", "#eef0f6"),
    "naval":           ("#1d1c22", "#c9a227", "#f1ede2"),
    "pmarca":          ("#3a0e14", "#d9a441", "#f3e7e2"),
    "michaelseibel":   ("#0e3537", "#56c2b6", "#eaf4f1"),
    "startupmarketing":("#13321f", "#9bc53d", "#e9f2e6"),
    "a16z":            ("#3a0a1f", "#d4af6a", "#f2e6ea"),
    "a16z_ai":            ("#0a1f2e", "#5fb0d4", "#e6f0f2"),
    "a16z_crypto":       ("#1a0a2e", "#9a6ad4", "#ece6f2"),
    "a16z_raising_health": ("#0a2e1a", "#5fd49a", "#e6f2ea"),
    "a16z_live":         ("#2e1a0a", "#d49a5f", "#f2ece6"),
    "a16z_16min":        ("#2e0a0a", "#d45f5f", "#f2e6e6"),
    "a16z_benmarc":      ("#14142a", "#d4c95f", "#eceef2"),
    "a16z_hotline":      ("#0a2e2e", "#5fd4d4", "#e6f2f2"),
    "avc":               ("#10243a", "#4a9fd4", "#e8f0f6"),
    "abovethecrowd":     ("#2a1c10", "#d4a24a", "#f4ece2"),
    "farnamstreet":      ("#1a1f1c", "#7fb069", "#eef2ea"),
    "cdixon":            ("#241a3a", "#a98fd4", "#efeaf6"),
    "samaltman":         ("#0e1f1a", "#4ad0a0", "#e6f2ed"),
    "danluu":            ("#1b1b1f", "#9aa3b0", "#ececf0"),
    "feld":              ("#0e2a2e", "#4ec6c0", "#e6f2f1"),
    "firstround":        ("#2a1018", "#d46a86", "#f4e6ea"),
    "eladgil":           ("#22301a", "#9ac46a", "#eef2e6"),
    "gwern":             ("#15171a", "#8a98a8", "#e8ecef"),
}
DEFAULT_THEME = ("#222831", "#c9a86a", "#eef0f1")

SOURCE_SITE = {
    "paulgraham": "paulgraham.com",
    "naval": "nav.al",
    "pmarca": "pmarchive.com",
    "michaelseibel": "michaelseibel.com",
    "startupmarketing": "startup-marketing.com",
    "a16z": "a16z.com",
    "a16z_ai": "a16z.com",
    "a16z_crypto": "a16zcrypto.com",
    "a16z_raising_health": "a16z.com",
    "a16z_live": "a16z.com",
    "a16z_16min": "a16z.com",
    "a16z_benmarc": "a16z.com",
    "a16z_hotline": "a16z.com",
    "avc": "avc.com",
    "abovethecrowd": "abovethecrowd.com",
    "farnamstreet": "fs.blog",
    "cdixon": "cdixon.org",
    "samaltman": "blog.samaltman.com",
    "danluu": "danluu.com",
    "feld": "feld.com",
    "firstround": "review.firstround.com",
    "eladgil": "blog.eladgil.com",
    "gwern": "gwern.net",
}

FONTS = {
    "en_bold": "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "en_reg":  "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "en_ital": "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
    "zh":      "/System/Library/Fonts/Supplemental/Songti.ttc",
    "zh_hei":  "/System/Library/Fonts/STHeiti Medium.ttc",
    "zh_light":"/System/Library/Fonts/STHeiti Light.ttc",
}

# 译者署名（中文含中文名；libiao.ai 的可点击链接放在书内版权页）
TRANSLATOR = {
    "zh": "译者　Bill Li（李标） · Opus 4.8 · MiniMax M3",
    "en": "Translated by Bill Li · Opus 4.8 · MiniMax M3",
}


def _font(key: str, size: int):
    try:
        return ImageFont.truetype(FONTS[key], size)
    except Exception:
        return ImageFont.load_default()


def _wrap(draw, text, font, max_w, cjk=False):
    lines, cur = [], ""
    units = list(text) if cjk else text.split()
    join = "" if cjk else " "
    for u in units:
        trial = (cur + join + u).strip() if cur else u
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = u
    if cur:
        lines.append(cur)
    return lines


def _ctext(d, cx, y, text, font, fill):
    w = d.textlength(text, font=font)
    d.text((cx - w / 2, y), text, font=font, fill=fill)


def _fit_one(d, text, avail, cjk, start=96, floor=44):
    """把单行文字缩放到不超过 avail 宽，返回字号。"""
    size = start
    while size > floor:
        f = _font("zh_hei" if cjk else "en_reg", size)
        if d.textlength(text, font=f) <= avail:
            break
        size -= 4
    return size


def _fit_author(d, author, avail, cjk):
    """作者名排版：能放下就一行；放不下且是「English（中文）」格式则拆两行。
    返回 [(行文本, 字号)]。"""
    if d.textlength(author, font=_font("zh_hei" if cjk else "en_reg", 96)) <= avail:
        return [(author, 96)]
    # 尝试在中文括号处拆分：英文名 / （中文名）
    for sep in ("（", "("):
        if sep in author:
            i = author.index(sep)
            en, zh = author[:i].strip(), author[i:].strip()
            return [(en, _fit_one(d, en, avail, cjk, start=84)),
                    (zh, _fit_one(d, zh, avail, cjk, start=72))]
    # 否则整体缩放到放下
    return [(author, _fit_one(d, author, avail, cjk))]


def make_cover(source_key: str, lang: str, title: str, author: str,
               span: str, out_path: Path, repo: str = "", variant: int = 1) -> Path:
    bg, accent, ink = THEMES.get(source_key, DEFAULT_THEME)
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    cjk = lang == "zh"
    cx = W / 2
    dim = _blend(bg, ink, 0.55)   # 页脚次要文字颜色（柔和）

    # 顶部细金线 + 小标签
    d.rectangle([MARGIN, 296, W - MARGIN, 300], fill=accent)
    label = "双语开源文集" if cjk else "OPEN · BILINGUAL EDITION"
    lf = _font("zh_hei" if cjk else "en_reg", 46)
    _ctext(d, cx, 356, label, lf, accent)

    # 标题（大号，居中，自动折行）
    title_font_key = "zh" if cjk else "en_bold"
    size = 150 if cjk else 130
    tf = _font(title_font_key, size)
    lines = _wrap(d, title, tf, W - 2 * MARGIN, cjk=cjk)
    while len(lines) > 4 and size > 70:
        size -= 12
        tf = _font(title_font_key, size)
        lines = _wrap(d, title, tf, W - 2 * MARGIN, cjk=cjk)
    line_h = int(size * 1.28)
    y = (H - line_h * len(lines)) // 2 - 200
    for ln in lines:
        _ctext(d, cx, y, ln, tf, ink)
        y += line_h

    # 金色菱形
    cy = y + 60
    d.polygon([(cx, cy-22), (cx+22, cy), (cx, cy+22), (cx-22, cy)], fill=accent)

    # 作者（醒目，自适应：过宽则按「English（中文）」拆行，并缩放到版心内）
    avail = W - 2 * MARGIN
    a_lines = _fit_author(d, author, avail, cjk)
    ay = cy + 80
    for ln, sz in a_lines:
        _ctext(d, cx, ay, ln, _font("zh_hei" if cjk else "en_reg", sz), ink)
        ay += int(sz * 1.18)

    # —— 页脚信息区（来源 / 译者 / 仓库，谦逊小字）——
    site = SOURCE_SITE.get(source_key, "")
    src_line = (f"原文来源 · {site}" if cjk else f"Source · {site}")
    tr_line = TRANSLATOR["zh" if cjk else "en"]
    repo_line = ""
    if repo:
        repo_line = (f"开源仓库 · {repo}" if cjk else f"Open source · {repo}")
    _draw_footer(d, cx, accent, ink, dim, cjk, src_line, tr_line, repo_line, span, variant)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def _draw_footer(d, cx, accent, ink, dim, cjk, src_line, tr_line, repo_line, span, variant):
    f_site = _font("zh_hei" if cjk else "en_reg", 44)
    f_tr = _font("zh_light" if cjk else "en_ital", 40)
    f_repo = _font("zh_hei" if cjk else "en_reg", 40)  # 含「开源仓库」中文需 CJK 字体
    f_span = _font("en_ital", 48)

    if variant == 1:
        # 极简：细线 + 三行居中 + 年代
        base = H - 470
        d.rectangle([MARGIN + 180, base, W - MARGIN - 180, base + 3], fill=accent)
        y = base + 46
        _ctext(d, cx, y, src_line, f_site, ink); y += 66
        _ctext(d, cx, y, tr_line, f_tr, dim); y += 60
        if repo_line:
            _ctext(d, cx, y, repo_line, f_repo, dim); y += 70
        _ctext(d, cx, y + 6, span, f_span, accent)

    elif variant == 2:
        # 圆角框包住来源+译者+仓库，年代在框下
        bx0, bx1 = MARGIN + 90, W - MARGIN - 90
        by0 = H - 540
        lines_n = 3 if repo_line else 2
        by1 = by0 + 70 + lines_n * 64
        d.rounded_rectangle([bx0, by0, bx1, by1], radius=22,
                            outline=accent, width=3)
        y = by0 + 44
        _ctext(d, cx, y, src_line, f_site, ink); y += 64
        _ctext(d, cx, y, tr_line, f_tr, dim); y += 60
        if repo_line:
            _ctext(d, cx, y, repo_line, f_repo, dim)
        _ctext(d, cx, by1 + 36, span, f_span, accent)

    else:  # variant 3
        # 左右分栏：左下来源/译者/仓库左对齐 + 竖向accent条；年代右下
        x0 = MARGIN + 16
        base = H - 470
        d.rectangle([x0, base + 6, x0 + 8, base + 6 + 168], fill=accent)
        tx = x0 + 36
        y = base
        d.text((tx, y), src_line, font=f_site, fill=ink); y += 64
        d.text((tx, y), tr_line, font=f_tr, fill=dim); y += 58
        if repo_line:
            d.text((tx, y), repo_line, font=f_repo, fill=dim)
        sw = d.textlength(span, font=f_span)
        d.text((W - MARGIN - sw, base + 100), span, font=f_span, fill=accent)


def _blend(c1, c2, t):
    a = tuple(int(c1[i:i+2], 16) for i in (1, 3, 5))
    b = tuple(int(c2[i:i+2], 16) for i in (1, 3, 5))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
