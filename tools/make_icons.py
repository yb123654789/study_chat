#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 PWA 图标（192 / 512 / maskable 512）。Pillow 画，不依赖外部素材。"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "web" / "icons"
BLUE = (37, 99, 235)
WHITE = (255, 255, 255)


def font(size: int) -> ImageFont.FreeTypeFont:
    for p in (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
              r"C:\Windows\Fonts\simhei.ttf"):
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def draw(size: int, pad_ratio: float) -> Image.Image:
    """pad_ratio: 内容占画布的比例（maskable 要留安全区）。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=BLUE)

    # 白色对勾 + 一道底线，比纯文字小尺寸更清楚
    box = size * (1 - pad_ratio * 2)
    x0, y0 = (size - box) / 2, (size - box) / 2
    w = max(2, int(size * 0.055))
    d.line(
        [(x0 + box * 0.16, y0 + box * 0.52), (x0 + box * 0.42, y0 + box * 0.76), (x0 + box * 0.86, y0 + box * 0.24)],
        fill=WHITE, width=w, joint="curve",
    )
    # 底部一道短横，暗示「计划/进度条」
    d.rounded_rectangle(
        [x0 + box * 0.16, y0 + box * 0.90, x0 + box * 0.60, y0 + box * 0.90 + w],
        radius=w // 2, fill=(255, 255, 255, 150),
    )
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    draw(192, 0.10).save(OUT / "icon-192.png")
    draw(512, 0.10).save(OUT / "icon-512.png")
    draw(512, 0.22).save(OUT / "icon-maskable-512.png")   # 安全区内
    for p in sorted(OUT.glob("*.png")):
        print(p.relative_to(OUT), p.stat().st_size, "B")


if __name__ == "__main__":
    main()
