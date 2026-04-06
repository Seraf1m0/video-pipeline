"""
Thumbnail Editor — Flask web app for YouTube thumbnail editing.
1920x1080 PNG export via Pillow.

Run:
    py agents/meta_agent/thumbnail_editor/app.py
Then open: http://localhost:5050
"""

import base64
import io
import json
import os
import sys
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file

from PIL import Image, ImageFilter, ImageEnhance, ImageDraw, ImageFont

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent.parent.parent

app = Flask(__name__, template_folder=str(_HERE / "templates"), static_folder=str(_HERE / "static"))

CANVAS_W = 1920
CANVAS_H = 1080

OVERLAY_DIR = _PROJECT_ROOT / "config" / "thumbnail_overlays"

FONT_DIRS = [
    _PROJECT_ROOT / "config" / "fonts",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts",
    Path("C:/Windows/Fonts"),
]

def _list_fonts() -> list[dict]:
    fonts = []
    seen = set()
    for d in FONT_DIRS:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.ttf")) + sorted(d.glob("*.otf")):
            name = f.stem
            if name.lower() not in seen:
                seen.add(name.lower())
                fonts.append({"name": name, "path": str(f)})
    return fonts


def _find_font(name: str, size: int, fallback: str | None = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for d in FONT_DIRS:
        if not d.exists():
            continue
        for ext in ("*.ttf", "*.otf"):
            for f in d.glob(ext):
                if f.stem.lower() == name.lower():
                    try:
                        return ImageFont.truetype(str(f), size)
                    except Exception:
                        pass
    if fallback:
        return _find_font(fallback, size)
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def _apply_bg_effects(img: Image.Image, effects: dict) -> Image.Image:
    img = img.convert("RGB")

    brightness = effects.get("brightness", 100) / 100
    contrast   = effects.get("contrast",   100) / 100
    saturation = effects.get("saturation", 100) / 100
    blur_r     = effects.get("blur",         0)

    if brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if saturation != 1.0:
        img = ImageEnhance.Color(img).enhance(saturation)
    if blur_r > 0:
        img = img.filter(ImageFilter.GaussianBlur(radius=blur_r))

    # Vignette
    vignette = effects.get("vignette", 0)
    if vignette > 0:
        vig = Image.new("L", img.size, 0)
        draw = ImageDraw.Draw(vig)
        w, h = img.size
        strength = int(vignette * 2.55)
        for i in range(min(w, h) // 2):
            alpha = int(strength * (1 - i / (min(w, h) / 2)))
            draw.rectangle([i, i, w - i, h - i], outline=alpha)
        vig = vig.filter(ImageFilter.GaussianBlur(radius=min(w, h) // 6))
        vig_rgb = Image.new("RGB", img.size, (0, 0, 0))
        img = Image.composite(vig_rgb, img, vig)

    return img


def _draw_text_element(draw: ImageDraw.Draw, elem: dict, scale: float = 1.0) -> None:
    text       = elem.get("text", "")
    x          = int(elem.get("x", 0) * scale)
    y          = int(elem.get("y", 0) * scale)
    font_name  = elem.get("font", "Arial")
    font_size  = int(elem.get("size", 80) * scale * (elem.get("scale", 100) / 100))
    color      = elem.get("color", "#ffffff")
    text_align = elem.get("textAlign", elem.get("align", "left"))  # match canvas key
    bold       = elem.get("bold", False)
    shadow     = elem.get("shadow", True)
    stroke_w   = int(elem.get("stroke_width", 0) * scale)
    stroke_c   = elem.get("stroke_color", "#000000")
    opacity    = elem.get("opacity", 100) / 100

    font_lookup = (font_name + "Bold") if bold else font_name
    font = _find_font(font_lookup, font_size, fallback=font_name if bold else None)

    rgb  = _hex_to_rgb(color)
    fill = tuple(int(c * opacity) for c in rgb) + (int(255 * opacity),)

    lines  = text.split("\n")
    line_h = int(font_size * 1.2)  # matches canvas: size * 1.2

    # Pillow anchor: lt=left-top, mt=middle-top, rt=right-top
    anchor = {"left": "lt", "center": "mt", "right": "rt"}.get(text_align, "lt")

    for i, line in enumerate(lines):
        ly = y + i * line_h

        if shadow:
            sdx, sdy = int(3 * scale), int(3 * scale)
            draw.text((x + sdx, ly + sdy), line, font=font,
                      fill=(0, 0, 0, int(180 * opacity)), anchor=anchor)

        if stroke_w > 0:
            s_rgb  = _hex_to_rgb(stroke_c)
            s_fill = s_rgb + (int(255 * opacity),)
            for dx in range(-stroke_w, stroke_w + 1):
                for dy in range(-stroke_w, stroke_w + 1):
                    if dx != 0 or dy != 0:
                        draw.text((x + dx, ly + dy), line, font=font,
                                  fill=s_fill, anchor=anchor)

        draw.text((x, ly), line, font=font, fill=fill, anchor=anchor)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    fonts = _list_fonts()
    return render_template("index.html", fonts=fonts)


@app.route("/api/fonts")
def api_fonts():
    return jsonify(_list_fonts())


@app.route("/api/overlays")
def api_overlays():
    """List stock overlay PNGs from config/thumbnail_overlays/ with metadata."""
    import json as _json
    meta_path = OVERLAY_DIR / "overlays_meta.json"
    meta = {}
    if meta_path.exists():
        with open(meta_path, encoding="utf-8") as mf:
            raw = _json.load(mf)
            meta = {k: v for k, v in raw.items() if not k.startswith("_")}

    overlays = []
    if OVERLAY_DIR.exists():
        files = sorted(OVERLAY_DIR.glob("*.png")) + sorted(OVERLAY_DIR.glob("*.jpg"))
        for f in files:
            if f.name == "overlays_meta.json":
                continue
            m = meta.get(f.name, {})
            overlays.append({
                "name": f.stem,
                "file": f.name,
                "label": m.get("label", f.stem),
                "tags": m.get("tags", []),
            })
    return jsonify(overlays)


@app.route("/api/overlay/<path:filename>")
def api_overlay(filename):
    """Serve a single overlay image as base64."""
    path = (OVERLAY_DIR / filename).resolve()
    try:
        path.relative_to(OVERLAY_DIR.resolve())
    except ValueError:
        return jsonify({"error": "forbidden"}), 403
    if not path.exists():
        return jsonify({"error": "not found"}), 404
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = path.suffix.lower().lstrip(".")
    mime = "jpeg" if ext in ("jpg", "jpeg") else "png"
    return jsonify({"data": f"data:image/{mime};base64,{b64}"})


@app.route("/api/overlay-img/<path:filename>")
def api_overlay_img(filename):
    """Serve overlay image as raw file (for <img> src)."""
    path = (OVERLAY_DIR / filename).resolve()
    try:
        path.relative_to(OVERLAY_DIR.resolve())
    except ValueError:
        return jsonify({"error": "forbidden"}), 403
    if not path.exists():
        return jsonify({"error": "not found"}), 404
    ext = path.suffix.lower().lstrip(".")
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
    return send_file(str(path), mimetype=mime)


@app.route("/api/sessions")
def api_sessions():
    """List available sessions with generated photos."""
    sessions = []
    data_dir = _PROJECT_ROOT / "data" / "media"
    if data_dir.exists():
        for ch in sorted(data_dir.iterdir()):
            if not ch.is_dir():
                continue
            for sess in sorted(ch.iterdir(), reverse=True):
                photos_dir = sess / "photos"
                if photos_dir.exists():
                    photos = list(photos_dir.glob("*.png")) + list(photos_dir.glob("*.jpg"))
                    if photos:
                        sessions.append({
                            "channel": ch.name,
                            "session": sess.name,
                            "photos": [str(p) for p in sorted(photos)],
                        })
    return jsonify(sessions[:20])


@app.route("/api/projects")
def api_projects():
    """List sessions that contain thumbnail project JSON files."""
    sessions = []
    channels_dir = Path("D:/Video-pipeline-data/channels")
    if not channels_dir.exists():
        channels_dir = _PROJECT_ROOT / "data" / "projects"
    if channels_dir.exists():
        for ch in sorted(channels_dir.iterdir()):
            if not ch.is_dir():
                continue
            for sess in sorted(ch.iterdir(), reverse=True):
                if not sess.is_dir():
                    continue
                projects = sorted(
                    sess.glob("*_project.json"),
                    key=lambda x: x.stat().st_mtime,
                    reverse=True
                )
                if not projects:
                    continue
                sessions.append({
                    "channel": ch.name,
                    "session": sess.name,
                    "mtime": int(sess.stat().st_mtime),
                    "files": [
                        {"name": f.stem, "path": str(f)}
                        for f in projects
                    ],
                })
    return jsonify(sessions[:60])


@app.route("/api/load_project", methods=["POST"])
def api_load_project():
    """Read a project JSON file from disk and return its contents."""
    path = request.json.get("path", "")
    try:
        p = Path(path)
        if not p.exists() or p.suffix.lower() != ".json":
            return jsonify({"error": "File not found"}), 404
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify({"project": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/load_image", methods=["POST"])
def api_load_image():
    """Load image from path, return base64."""
    data = request.json
    path = Path(data.get("path", ""))
    if not path.exists():
        return jsonify({"error": "File not found"}), 404
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = path.suffix.lower().lstrip(".")
    mime = "jpeg" if ext in ("jpg", "jpeg") else "png"
    return jsonify({"data": f"data:image/{mime};base64,{b64}"})


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """Generate image via PixelAgent API."""
    import urllib.request
    import urllib.error
    data = request.json
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "Prompt is required"}), 400
    aspect_ratio = data.get("aspect_ratio", "16:9")

    # Read API key from config/.env
    api_key = os.environ.get("PIXEL_API_KEY", "")
    env_path = _PROJECT_ROOT / "config" / ".env"
    if not api_key and env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("PIXEL_API_KEY="):
                api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

    if not api_key:
        return jsonify({"error": "PIXEL_API_KEY not found in config/.env"}), 400

    try:
        import requests as _requests, json as _json
        resp = _requests.post(
            "https://voiceapi.csv666.ru/api/v1/image/create",
            json={"prompt": prompt, "aspect_ratio": aspect_ratio},
            headers={"X-API-Key": api_key},
            timeout=120
        )
        resp.raise_for_status()
        result = resp.json()
        img_b64 = result.get("image_b64", "")
        if not img_b64:
            return jsonify({"error": "No image returned from API"}), 500
        return jsonify({"data": f"data:image/webp;base64,{img_b64}"})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/remove_bg", methods=["POST"])
def api_remove_bg():
    """Remove background from image using rembg."""
    try:
        from rembg import remove as rembg_remove
    except ImportError:
        return jsonify({"error": "rembg not installed. Run: pip install rembg"}), 500

    data = request.json
    img_data = data.get("img_data", "")
    if not img_data:
        return jsonify({"error": "No image data provided"}), 400

    try:
        _, encoded = img_data.split(",", 1)
        img_bytes = base64.b64decode(encoded)
        output = rembg_remove(img_bytes)
        b64 = base64.b64encode(output).decode()
        return jsonify({"data": f"data:image/png;base64,{b64}"})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/save_project", methods=["POST"])
def api_save_project():
    """Save project JSON to disk."""
    data = request.get_json(force=True)
    path = data.get("path", "").strip()
    json_data = data.get("data", "")
    if not path or not json_data:
        return jsonify({"error": "path and data required"}), 400
    try:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json_data, encoding="utf-8")
        return jsonify({"ok": True, "path": str(out)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/save_png", methods=["POST"])
def api_save_png():
    """Save a base64-encoded PNG to disk (canvas-rendered export)."""
    data = request.get_json(force=True)
    path = data.get("path", "").strip()
    b64  = data.get("data", "")
    if not path or not b64:
        return jsonify({"error": "path and data required"}), 400
    try:
        img_bytes = base64.b64decode(b64)
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(img_bytes)
        return jsonify({"ok": True, "path": str(out), "size": len(img_bytes)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/export", methods=["POST"])
def api_export():
    """
    Export final 1920x1080 PNG.
    Body: { bg_data: <base64>, effects: {...}, elements: [...], output_path: str }
    """
    data = request.json

    # Background
    bg_b64 = data.get("bg_data", "")
    if bg_b64:
        header, encoded = bg_b64.split(",", 1)
        img_bytes = base64.b64decode(encoded)
        bg = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        bg = bg.resize((CANVAS_W, CANVAS_H), Image.LANCZOS)
    else:
        bg = Image.new("RGBA", (CANVAS_W, CANVAS_H), (20, 20, 20, 255))

    # Apply effects
    effects = data.get("effects", {})
    bg = _apply_bg_effects(bg.convert("RGB"), effects).convert("RGBA")

    # Draw elements (image overlays + text)
    draw = ImageDraw.Draw(bg)
    for elem in data.get("elements", []):
        if elem.get("type") == "image":
            src = elem.get("src", "")
            if not src:
                continue
            _, encoded = src.split(",", 1)
            ov = Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGBA")
            img_scale = elem.get("scale", 100) / 100
            w = max(1, int(elem.get("width", 200) * img_scale))
            h = max(1, int(elem.get("height", 200) * img_scale))
            ov = ov.resize((w, h), Image.LANCZOS)
            opacity = elem.get("opacity", 100) / 100
            if opacity < 1.0:
                r, g, b, a = ov.split()
                a = a.point(lambda v: int(v * opacity))
                ov = Image.merge("RGBA", (r, g, b, a))
            rotation = elem.get("rotation", 0)
            if rotation:
                ov = ov.rotate(-rotation, expand=True, resample=Image.BICUBIC)
                # Recalculate paste position based on new size and anchor
                ax = elem.get("anchorX", 0.5)
                ay = elem.get("anchorY", 0.5)
                orig_cx = int(elem.get("x", 0)) + int(ax * w)
                orig_cy = int(elem.get("y", 0)) + int(ay * h)
                x_paste = orig_cx - ov.width // 2
                y_paste = orig_cy - ov.height // 2
            else:
                x_paste = int(elem.get("x", 0))
                y_paste = int(elem.get("y", 0))
            bg.paste(ov, (x_paste, y_paste), ov)
        elif elem.get("type") == "shape":
            shape_type = elem.get("shapeType", "rect")
            sx = int(elem.get("x", 0))
            sy = int(elem.get("y", 0))
            sw = max(1, int(elem.get("width", 100)))
            sh = max(1, int(elem.get("height", 100)))
            fill_hex  = elem.get("fill", "#4a9eff")
            stroke_hex = elem.get("stroke", "#ffffff")
            stroke_w  = int(elem.get("strokeWidth", 0))
            opacity   = elem.get("opacity", 100) / 100
            rotation  = elem.get("rotation", 0)

            # Create shape on transparent layer
            shape_img = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
            sd = ImageDraw.Draw(shape_img)

            def hex2rgba(h, a=255):
                if not h or h == 'transparent': return (0,0,0,0)
                h = h.lstrip('#')
                return tuple(int(h[i:i+2],16) for i in (0,2,4)) + (int(a),)

            fill_c   = hex2rgba(fill_hex,   int(255*opacity)) if fill_hex != 'transparent' else (0,0,0,0)
            stroke_c = hex2rgba(stroke_hex, int(255*opacity))
            outline  = stroke_c if stroke_w > 0 else None

            if shape_type in ('rect', 'roundRect'):
                br = int(elem.get("borderRadius", 0))
                if br > 0:
                    sd.rounded_rectangle([sx, sy, sx+sw, sy+sh], radius=br, fill=fill_c, outline=outline, width=stroke_w or 1)
                else:
                    sd.rectangle([sx, sy, sx+sw, sy+sh], fill=fill_c, outline=outline, width=stroke_w or 1)
            elif shape_type == 'circle':
                sd.ellipse([sx, sy, sx+sw, sy+sh], fill=fill_c, outline=outline, width=stroke_w or 1)
            elif shape_type == 'triangle':
                pts = [(sx+sw//2, sy), (sx+sw, sy+sh), (sx, sy+sh)]
                sd.polygon(pts, fill=fill_c, outline=outline)
            elif shape_type in ('star', 'hexagon'):
                import math
                n_pts = int(elem.get("points", 5))
                cx2, cy2 = sx + sw//2, sy + sh//2
                if shape_type == 'star':
                    oR, iR = min(sw,sh)//2, int(min(sw,sh)//2 * 0.42)
                    pts = []
                    for i in range(n_pts*2):
                        a = (i * math.pi / n_pts) - math.pi/2
                        r2 = oR if i%2==0 else iR
                        pts.append((cx2 + int(r2*math.cos(a)), cy2 + int(r2*math.sin(a))))
                else:
                    r2 = min(sw,sh)//2
                    pts = [(cx2+int(r2*math.cos((i*2*math.pi/n_pts)-math.pi/2)),
                            cy2+int(r2*math.sin((i*2*math.pi/n_pts)-math.pi/2))) for i in range(n_pts)]
                sd.polygon(pts, fill=fill_c, outline=outline)
            elif shape_type == 'line':
                cy3 = sy + sh//2
                sd.line([(sx, cy3), (sx+sw, cy3)], fill=stroke_c, width=max(stroke_w,2))
            elif shape_type == 'arrow':
                hw = int(sh * 0.4); hl = int(sw * 0.35); cy3 = sy + sh//2
                pts = [(sx, cy3-int(sh*0.18)), (sx+sw-hl, cy3-int(sh*0.18)),
                       (sx+sw-hl, cy3-hw), (sx+sw, cy3),
                       (sx+sw-hl, cy3+hw), (sx+sw-hl, cy3+int(sh*0.18)),
                       (sx, cy3+int(sh*0.18))]
                sd.polygon(pts, fill=fill_c, outline=outline)

            # Apply rotation if any
            if rotation:
                ax = elem.get("anchorX", 0.5); ay2 = elem.get("anchorY", 0.5)
                pcx = sx + int(ax*sw); pcy = sy + int(ay2*sh)
                shape_img = shape_img.rotate(-rotation, center=(pcx, pcy), resample=Image.BICUBIC, expand=False)

            bg.paste(shape_img, (0, 0), shape_img)
        else:
            _draw_text_element(draw, elem, scale=1.0)

    # Export
    output_path = data.get("output_path", "")
    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="PNG", optimize=False)
    buf.seek(0)

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        bg.convert("RGB").save(str(out), format="PNG", optimize=False)

    return send_file(buf, mimetype="image/png", as_attachment=True, download_name="thumbnail.png")


@app.route("/api/preview", methods=["POST"])
def api_preview():
    """Quick preview at reduced resolution."""
    data = request.json
    scale = 0.5

    bg_b64 = data.get("bg_data", "")
    if bg_b64:
        header, encoded = bg_b64.split(",", 1)
        img_bytes = base64.b64decode(encoded)
        bg = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        bg = bg.resize((int(CANVAS_W * scale), int(CANVAS_H * scale)), Image.LANCZOS)
    else:
        bg = Image.new("RGB", (int(CANVAS_W * scale), int(CANVAS_H * scale)), (20, 20, 20))

    effects = data.get("effects", {})
    bg = _apply_bg_effects(bg, effects)
    draw = ImageDraw.Draw(bg.convert("RGBA"))
    for elem in data.get("elements", []):
        _draw_text_element(draw, elem, scale=scale)

    buf = io.BytesIO()
    bg.save(buf, format="JPEG", quality=85)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    return jsonify({"preview": f"data:image/jpeg;base64,{b64}"})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5050)
    args, _ = parser.parse_known_args()
    print(f"[THUMBNAIL EDITOR] http://localhost:{args.port}", flush=True)
    app.run(host="0.0.0.0", port=args.port, debug=False)
