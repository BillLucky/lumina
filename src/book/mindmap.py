"""思维导图渲染：把「中心主题 + N 个要点」画成一张横向树形图（PNG）。

浅底、主题色枝干、圆角节点，嵌入文章开头，帮助读者一眼抓住核心结构。
中文用宋体/黑体，英文用 Georgia。配色复用各来源主题。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from book.cover import THEMES, DEFAULT_THEME, FONTS

W = 1600
PAD = 40
ROOT_W = 430
NODE_W = 760
GAP_X = 90
ROW_GAP = 36
BG = "#fbfaf6"


def _font(key, size):
    try:
        return ImageFont.truetype(FONTS[key], size)
    except Exception:
        return ImageFont.load_default()


def _wrap(draw, text, font, max_w, cjk):
    lines, cur = [], ""
    units = list(text) if cjk else text.split()
    join = "" if cjk else " "
    for u in units:
        trial = (cur + join + u).strip() if cur else u
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur); cur = u
    if cur:
        lines.append(cur)
    return lines


def _rounded(d, box, r, fill=None, outline=None, width=1):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def make_mindmap(source_key: str, lang: str, central: str,
                 points: list[dict], out_path: Path) -> Path:
    bg_c, accent, _ = THEMES.get(source_key, DEFAULT_THEME)
    cjk = lang == "zh"
    title_font = _font("zh_hei" if cjk else "en_bold", 40)
    label_font = _font("zh_hei" if cjk else "en_bold", 36)
    detail_font = _font("zh" if cjk else "en_reg", 30)

    # 先测量每个要点节点高度
    tmp = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    node_layouts = []
    inner_w = NODE_W - 60
    for p in points[:6]:
        label = p.get("label", "")
        detail = p.get("detail", "")
        l_lines = _wrap(tmp, label, label_font, inner_w, cjk)
        d_lines = _wrap(tmp, detail, detail_font, inner_w, cjk)
        h = 28 + len(l_lines) * 46 + 8 + len(d_lines) * 40 + 28
        node_layouts.append((l_lines, d_lines, h))

    total_h = sum(h for *_, h in node_layouts) + ROW_GAP * (len(node_layouts) - 1)
    H = max(total_h + 2 * PAD, 420)
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # 中心主题节点（左，竖向居中）
    root_x0, root_x1 = PAD, PAD + ROOT_W
    c_lines = _wrap(d, central, title_font, ROOT_W - 56, cjk)
    root_h = 40 + len(c_lines) * 52 + 40
    root_y0 = (H - root_h) // 2
    root_y1 = root_y0 + root_h
    _rounded(d, [root_x0, root_y0, root_x1, root_y1], 24, fill=bg_c)
    ty = root_y0 + 40
    for ln in c_lines:
        lw = d.textlength(ln, font=title_font)
        d.text((root_x0 + (ROOT_W - lw) / 2, ty), ln, font=title_font, fill="#ffffff")
        ty += 52
    root_anchor = (root_x1, (root_y0 + root_y1) // 2)

    # 要点节点（右，依次堆叠）
    node_x0 = root_x1 + GAP_X
    node_x1 = node_x0 + NODE_W
    y = PAD
    for idx, (l_lines, d_lines, h) in enumerate(node_layouts):
        cy = y + h // 2
        # 连接线：从中心节点右缘到要点节点左缘，三次贝塞尔平滑曲线
        _curve(d, root_anchor, (node_x0, cy), accent)
        # 节点
        _rounded(d, [node_x0, y, node_x1, y + h], 18, fill="#ffffff",
                 outline=accent, width=4)
        # 左侧色条
        _rounded(d, [node_x0, y, node_x0 + 12, y + h], 6, fill=accent)
        tx = node_x0 + 34
        yy = y + 26
        for ln in l_lines:
            d.text((tx, yy), ln, font=label_font, fill="#1a1a1a"); yy += 46
        yy += 8
        for ln in d_lines:
            d.text((tx, yy), ln, font=detail_font, fill="#555"); yy += 40
        y += h + ROW_GAP

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def _curve(d, p0, p1, color):
    """从 p0 到 p1 画平滑的水平三次贝塞尔曲线。"""
    x0, y0 = p0
    x1, y1 = p1
    cx = (x0 + x1) / 2
    pts = []
    for i in range(41):
        t = i / 40
        mt = 1 - t
        x = mt**3*x0 + 3*mt**2*t*cx + 3*mt*t**2*cx + t**3*x1
        y = mt**3*y0 + 3*mt**2*t*y0 + 3*mt*t**2*y1 + t**3*y1
        pts.append((x, y))
    d.line(pts, fill=color, width=5, joint="curve")
