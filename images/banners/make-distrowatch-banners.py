#!/usr/bin/env python3
"""Render the two static DistroWatch leaderboards from the SVG mark and Inter."""

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin, features
import PIL


HERE = Path(__file__).resolve().parent
MARK = HERE.parents[1] / "assets" / "intergen-mark" / "svg" / "intergenos_logo_transparent.svg"
SCALE = 4
HEIGHT = 90
VOID = "#050810"
TEXT = "#e2e8f0"
SECONDARY = "#7a8ba8"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def font_at(data, size, weight):
    """Use the supplied bytes directly: fontconfig cannot substitute a face."""
    font = ImageFont.truetype(BytesIO(data), size * SCALE,
                              layout_engine=ImageFont.Layout.BASIC)
    family, style = font.getname()
    if family != "Inter Variable":
        raise ValueError(f"Expected Inter Variable, got {family!r}")
    axes = font.get_variation_axes()
    if [axis["name"] for axis in axes] != [b"Optical size", b"Weight"]:
        raise ValueError(f"Unexpected Inter variation axes: {axes!r}")
    # Match optical sizing to the final pixel size, not the oversampled canvas.
    values = [max(14, min(size, 32)), weight]
    font.set_variation_by_axes(values)
    print(json.dumps({"renderer_font": family, "style": style,
                      "font_sha256": digest(data), "size_px": size,
                      "axes": {"optical_size": values[0], "weight": weight}}))
    return font


def text(draw, position, value, font, fill=TEXT):
    draw.text(tuple(round(v * SCALE) for v in position), value,
              font=font, fill=fill, anchor="lt")


def wordmark(draw, position, font):
    # The canonical wordmark has wide tracking; body copy keeps zero tracking.
    x, y = position
    for letter in "INTERGENOS":
        text(draw, (x, y), letter, font)
        x += font.getlength(letter) / SCALE + 3


def render(width, font_data, mark_data, output):
    wide = width == 970
    field_width = 300 if wide else 280
    mark_x = 34 if wide else 24
    copy_x = 340 if wide else 310
    title_size = 28 if wide else 21
    body_size = 16 if wide else 14
    image = Image.new("RGB", (width * SCALE, HEIGHT * SCALE), VOID)
    draw = ImageDraw.Draw(image)
    # §4 reserves pure black for the mark; the adjacent copy sits on the void.
    draw.rectangle((0, 0, field_width * SCALE - 1, HEIGHT * SCALE - 1),
                   fill="#000000")

    # Uniform scale, original path/stroke/caps/joins. Only transparent canvas
    # padding falls outside the strip; the painted pulse has a 5 px margin.
    mark_height = 112
    command = ["rsvg-convert", "--height", str(mark_height * SCALE)]
    result = subprocess.run(command, input=mark_data, capture_output=True, check=True)
    if result.stderr:
        raise RuntimeError(result.stderr.decode())
    mark = Image.open(BytesIO(result.stdout)).convert("RGBA")
    image.paste(mark, (mark_x * SCALE, (HEIGHT - mark_height) * SCALE // 2), mark)
    print(json.dumps({"svg_sha256": digest(mark_data), "command": command,
                      "rendered_size": mark.size, "svg_text_nodes": 0}))

    name_font = font_at(font_data, 20, 400)
    regular = font_at(font_data, title_size, 400)
    bold = font_at(font_data, title_size, 700)
    body = font_at(font_data, body_size, 400)
    wordmark(draw, (mark_x + 56, 22), name_font)
    prefix = "Security is not first. It is "
    emphasis = "only."
    copy_y = 20 if wide else 23
    prefix_width = regular.getlength(prefix) / SCALE
    line_width = prefix_width + bold.getlength(emphasis) / SCALE
    if copy_x + line_width > width - 24:
        raise ValueError("Tagline exceeds the right clear space")
    text(draw, (copy_x, copy_y), prefix, regular)
    text(draw, (copy_x + prefix_width, copy_y), emphasis, bold)
    release = "Explore R001.3 at intergenos.org"
    if copy_x + body.getlength(release) / SCALE > width - 24:
        raise ValueError("Release line exceeds the right clear space")
    text(draw, (copy_x, 57), release, body, SECONDARY)

    image = image.resize((width, HEIGHT), Image.Resampling.LANCZOS)
    metadata = PngImagePlugin.PngInfo()
    metadata.add(b"sRGB", b"\x00")
    target = output / f"distrowatch-leaderboard-{width}x90.png"
    image.save(target, pnginfo=metadata, optimize=False, compress_level=9)
    print(json.dumps({"output": str(target), "size": image.size,
                      "mode": image.mode, "bytes": target.stat().st_size,
                      "sha256": digest(target.read_bytes())}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path,
                        default=Path("/usr/share/fonts/inter/InterVariable.ttf"))
    parser.add_argument("--output-dir", type=Path, default=HERE)
    args = parser.parse_args()
    font_data = args.font.read_bytes()
    mark_data = MARK.read_bytes()
    root = ET.fromstring(mark_data)
    children = list(root)
    if len(children) != 1 or children[0].tag != "{http://www.w3.org/2000/svg}path":
        raise ValueError("Expected a single path in the transparent logo SVG")
    path = children[0]
    if (path.get("stroke") != "#0099FF" or path.get("fill") != "none"
            or path.get("stroke-linecap") != "round"
            or path.get("stroke-linejoin") != "round"):
        raise ValueError("Unexpected logo stroke attributes")
    print(json.dumps({"font_file": str(args.font.resolve()),
                      "font_sha256": digest(font_data), "Pillow": PIL.__version__,
                      "FreeType": features.version_module("freetype2"),
                      "zlib": features.version_codec("zlib"),
                      "rsvg": subprocess.check_output(
                          ["rsvg-convert", "--version"], text=True).strip()}))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for width in (970, 728):
        render(width, font_data, mark_data, args.output_dir)


if __name__ == "__main__":
    main()
