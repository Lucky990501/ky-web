"""Content-preserving HTML from the existing Skill style, no network or model."""
from __future__ import annotations

from html import escape
import json
from pathlib import Path
import re

STYLE_PATH = Path(__file__).resolve().parents[1] / "skill_packages/wechat-official-account-writing/1.0.1/references/style.json"


def render_wechat_article(markdown: str) -> str:
    style = json.loads(STYLE_PATH.read_text(encoding="utf-8"))
    def inline(text):
        safe = escape(text)
        safe = re.sub(r"\*\*([^*\n]+)\*\*", lambda m: f'<strong style="color:{style["accent_color"]};font-weight:700">{m.group(1)}</strong>', safe)
        return safe
    body, paragraph = [], []
    first = True
    def flush():
        nonlocal first
        if paragraph:
            margin = 28 if first else style["paragraph_spacing"]
            body.append(f'<p style="margin:0 0 {margin}px">' + "<br>".join(inline(line) for line in paragraph) + '</p>')
            paragraph.clear()
            first = False
    for line in markdown.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        image = re.fullmatch(r"!\[([^\]]*)\]\(([^)]+)\)", line.strip())
        if heading:
            flush()
            level = len(heading.group(1))
            tag = min(level, 3)
            body.append(f'<h{tag} style="font-size:{24 if level == 1 else 20}px;line-height:1.6;margin:{style["section_spacing"]}px 0 18px;color:{style["body_color"]};border-bottom:2px solid {style["accent_color"]}">{inline(heading.group(2))}</h{tag}>')
        elif line.startswith("> "):
            flush()
            body.append(f'<blockquote style="margin:28px 0;padding:18px 20px;border-left:4px solid {style["accent_color"]};background-color:#fffdf4">{inline(line[2:])}</blockquote>')
        elif image:
            flush()
            # Only authenticated platform URLs. No arbitrary external hosts,
            # file paths, data URI, scripts, empty image or fake placeholder.
            url = image.group(2)
            if re.fullmatch(r"/api/v1/(?:storage|chat-images)/[A-Za-z0-9_./%-]+", url) and ".." not in url and "%" not in url:
                body.append(f'<figure style="margin:28px 0"><img src="{escape(url, quote=True)}" alt="{escape(image.group(1), quote=True)}" style="width:100%;height:auto"><figcaption style="font-size:{style["caption_size"]}px;color:{style["caption_color"]};line-height:1.6;text-align:center">{inline(image.group(1))}</figcaption></figure>')
            else:
                body.append('<!-- 图片来源未验证，未嵌入 -->')
        elif re.match(r"^\s*[-*]\s+", line):
            flush()
            bullet = re.sub(r"^\s*[-*]\s+", "", line)
            body.append(f'<p style="margin:0 0 18px">• {inline(bullet)}</p>')
        elif not line.strip():
            flush()
        else:
            paragraph.append(line)
    flush()
    return (f'<section id="article" style="color:{style["body_color"]};font-family:{style["font_family"]};font-size:{style["font_size"]}px;line-height:{style["line_height"]};letter-spacing:{style["letter_spacing"]}px;text-align:justify;overflow-wrap:break-word;background-color:#ffffff">'
            + "".join(body) + "</section>")
