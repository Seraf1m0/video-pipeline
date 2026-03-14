"""
Video Editor — Flask web server
--------------------------------
Запуск: py web/editor.py
Открыть: http://localhost:5000
"""

import json
import os
import re
import sys
import subprocess
import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

BASE_DIR   = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "data" / "output"

sys.path.insert(0, str(BASE_DIR))

app = Flask(__name__, template_folder="templates")

render_tasks: dict[str, dict] = {}

_SESSION_RE = re.compile(r"^Video_(\d{8}_\d{6})$")


# ─── Session helpers ──────────────────────────────────────────────────────────

def find_sessions() -> list[dict]:
    if not OUTPUT_DIR.exists():
        return []
    sessions = []
    for d in sorted(OUTPUT_DIR.iterdir(), reverse=True):
        m = _SESSION_RE.match(d.name)
        if not m:
            continue
        ts = m.group(1)
        final  = d / "final_premiere.mp4"
        base   = d / "montage_with_audio.mp4"
        srt    = d / "subtitles.srt"
        music  = d / "music_track.aac"
        pd     = d / "premiere_data.json"
        sessions.append({
            "name":       d.name,
            "date":       f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}",
            "has_data":   pd.exists(),
            "has_final":  final.exists(),
            "has_base":   base.exists(),
            "has_srt":    srt.exists(),
            "has_music":  music.exists(),
            "final_size": f"{final.stat().st_size/1024/1024:.1f} MB" if final.exists() else None,
        })
    return sessions


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("editor.html", sessions=find_sessions())


@app.route("/api/sessions")
def api_sessions():
    return jsonify(find_sessions())


@app.route("/api/session/<name>/data")
def api_session_data(name: str):
    pd = OUTPUT_DIR / name / "premiere_data.json"
    if not pd.exists():
        return jsonify({"error": "premiere_data.json не найден"}), 404
    return jsonify(json.loads(pd.read_text(encoding="utf-8")))


@app.route("/video/<session>/<path:filename>")
def serve_video(session: str, filename: str):
    path = OUTPUT_DIR / session / filename
    if not path.exists():
        return "Not found", 404
    return send_file(str(path), mimetype="video/mp4", conditional=True)


@app.route("/api/task/<task_id>")
def api_task(task_id: str):
    task = render_tasks.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    return jsonify(task)


# ─── Assemble ─────────────────────────────────────────────────────────────────

@app.route("/api/assemble", methods=["POST"])
def api_assemble():
    body       = request.json or {}
    session    = body.get("session")
    settings   = body.get("settings")
    with_subs  = body.get("with_subs", True)
    with_music = body.get("with_music", True)

    if not session:
        return jsonify({"error": "session required"}), 400

    task_id = uuid.uuid4().hex[:8]
    render_tasks[task_id] = {"status": "running", "file": None, "progress": 0, "log": [], "error": None}

    def _run():
        try:
            from agents.video_editor.video_editor import assemble
            log_lines = render_tasks[task_id]["log"]

            def on_line(msg: str):
                log_lines.append(msg)
                if "frame=" in msg or "size=" in msg:
                    render_tasks[task_id]["progress"] = min(
                        render_tasks[task_id]["progress"] + 1, 95
                    )

            out = assemble(
                session,
                settings=settings,
                with_subs=with_subs,
                with_music=with_music,
                on_line=on_line,
            )
            render_tasks[task_id].update({"status": "done", "file": out.name, "progress": 100})
        except Exception as e:
            render_tasks[task_id].update({"status": "error", "error": str(e)})

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"task_id": task_id})


# ─── Preview ──────────────────────────────────────────────────────────────────

@app.route("/api/preview", methods=["POST"])
def api_preview():
    body          = request.json or {}
    session       = body.get("session")
    settings      = body.get("settings", {})
    preview_start = float(body.get("preview_start", 90))

    if not session:
        return jsonify({"error": "session required"}), 400

    # Find best source video
    for candidate in ["montage_with_audio.mp4", "final_premiere.mp4", "montage_base.mp4"]:
        src = OUTPUT_DIR / session / candidate
        if src.exists():
            break
    else:
        return jsonify({"error": "Нет видео. Сначала запусти Assemble."}), 400

    task_id  = uuid.uuid4().hex[:8]
    out_file = OUTPUT_DIR / session / f"preview_{task_id}.mp4"
    render_tasks[task_id] = {"status": "running", "file": None, "progress": 0, "error": None}

    def _run():
        try:
            from agents.video_editor.video_editor import lumetri_to_ffmpeg
            color_vf = lumetri_to_ffmpeg(settings.get("lumetri", {}))
            vf = color_vf if color_vf else "null"

            has_audio = src.name != "montage_base.mp4"
            audio_map = ["-map", "0:a:0"] if has_audio else []

            def _try(enc_args):
                return subprocess.run([
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", str(preview_start), "-i", str(src), "-t", "30",
                    "-vf", vf,
                    "-map", "0:v:0", *audio_map,
                    *enc_args,
                    *([] if not has_audio else ["-c:a", "aac", "-b:a", "128k"]),
                    str(out_file),
                ], capture_output=True)

            r = _try(["-c:v", "h264_nvenc", "-preset", "p1", "-b:v", "8M", "-pix_fmt", "yuv420p"])
            if r.returncode != 0:
                r = _try(["-c:v", "libx264", "-preset", "veryfast", "-b:v", "8M"])
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode(errors="replace")[-400:])

            render_tasks[task_id].update({"status": "done", "file": out_file.name, "progress": 100})
        except Exception as e:
            render_tasks[task_id].update({"status": "error", "error": str(e)})

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"task_id": task_id})


# ─── Log stream ───────────────────────────────────────────────────────────────

@app.route("/api/task/<task_id>/log")
def api_task_log(task_id: str):
    task = render_tasks.get(task_id, {})
    return jsonify(task.get("log", []))


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("[Editor] http://localhost:5000", flush=True)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
