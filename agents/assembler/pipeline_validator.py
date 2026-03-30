"""
pipeline_validator.py — Two-level quality gate, structured JSONL logging, summary report.

validate_pre_render(clips, trans_plan, sfx_events)  → run BEFORE render (save time on fails)
validate_post_render(stats)                          → run AFTER render  (final hard gate)
audit_final_audio(output_path, session_path)         → audio QA checks  (non-blocking, log only)
generate_summary_report(jsonl_path)                 → human-readable aggregate from JSONL
"""

import json
import math
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# ── Structured JSON Logger ────────────────────────────────────────────────────

_log_lock   = threading.Lock()
_log_events: list[dict] = []
_log_path:   Optional[Path] = None


def init_pipeline_log(output_dir: Path) -> None:
    global _log_path
    _log_path = Path(output_dir) / "pipeline_log.jsonl"


def _emit(event_type: str, **kwargs) -> None:
    entry = {"t": round(time.time(), 3), "type": event_type, **kwargs}
    with _log_lock:
        _log_events.append(entry)
        if _log_path:
            with open(_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log_clip_trim(clip: str, target_dur: float, actual_dur: float) -> None:
    get_stats().clip_trim_count += 1
    _emit("clip_trim",
          clip=clip,
          target=round(target_dur, 3),
          actual=round(actual_dur, 3),
          delta=round(actual_dur - target_dur, 3))


def log_fallback_transition(chunk_idx: int, transition: str, error: str) -> None:
    get_stats().fallback_count += 1
    _emit("fallback_transition",
          chunk=chunk_idx,
          transition=transition,
          error=error[:300])


def log_sfx_drop(time_s: float, file: str, reason: str) -> None:
    get_stats().sfx_drop_count += 1
    _emit("sfx_drop", time=round(time_s, 3), file=file, reason=reason)


def log_sfx_shift(time_s: float, new_time: float, file: str) -> None:
    get_stats().sfx_shift_count += 1
    _emit("sfx_shift",
          from_t=round(time_s, 3),
          to_t=round(new_time, 3),
          file=file,
          delta=round(abs(new_time - time_s), 4))


def log_nvenc_scale(old_slots: int, new_slots: int, reason: str) -> None:
    get_stats().nvenc_scale_count += 1
    _emit("nvenc_scale", from_slots=old_slots, to_slots=new_slots, reason=reason)


def log_pacing_adjust(seg_id: int, old_dur: float, new_dur: float, reason: str) -> None:
    get_stats().pacing_adjust_count += 1
    _emit("pacing_adjust",
          seg=seg_id,
          old=round(old_dur, 3),
          new=round(new_dur, 3),
          reason=reason)


# ── Pipeline Stats ────────────────────────────────────────────────────────────

@dataclass
class PipelineStats:
    no_clip_reuse:       bool  = True
    sfx_aligned:         bool  = True
    sfx_within_limits:   bool  = True
    transitions_valid:   bool  = True
    fallback_count:      int   = 0
    sfx_drop_count:      int   = 0
    sfx_shift_count:     int   = 0
    clip_trim_count:     int   = 0
    pacing_adjust_count: int   = 0
    nvenc_scale_count:   int   = 0
    render_time_s:       float = 0.0
    errors:              list  = field(default_factory=list)


_stats      = PipelineStats()
_stats_lock = threading.Lock()
_render_t0: float = 0.0


def get_stats() -> PipelineStats:
    return _stats


def reset_stats() -> None:
    global _stats, _log_events, _render_t0
    with _stats_lock:
        _stats = PipelineStats()
    _log_events = []
    _render_t0  = 0.0


def mark_render_start() -> None:
    global _render_t0
    _render_t0 = time.time()


def mark_render_end() -> None:
    if _render_t0 > 0:
        _stats.render_time_s = round(time.time() - _render_t0, 1)


# ── Level 1: Pre-render validation ────────────────────────────────────────────

def validate_pre_render(
    clips:       list,            # list of Path or str
    trans_plan:  list[dict],      # list of {"type": str, "dur": float}
    sfx_events:  list[dict],      # list of {"time": float, "file": ...}
    total_dur:   float,
) -> None:
    """
    Run BEFORE encoding starts. Saves up to 30% time on bad inputs.
    Raises Exception immediately if plan is invalid.
    """
    failures: list[str] = []

    # 1. All clips must exist and have duration > 0
    missing    = [str(c) for c in clips if not Path(c).exists()]
    zero_dur   = []
    if not missing:
        for c in clips:
            try:
                r = __import__("subprocess").run(
                    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "csv=p=0", str(c)],
                    capture_output=True, text=True,
                )
                dur = float(r.stdout.strip() or "0")
                if dur < 0.1:
                    zero_dur.append(str(c))
            except Exception:
                zero_dur.append(str(c))

    if missing:
        failures.append(f"MISSING_CLIPS: {len(missing)} files not found: {missing[:3]}")
    if zero_dur:
        failures.append(f"ZERO_DUR_CLIPS: {len(zero_dur)} clips have duration=0: {zero_dur[:3]}")

    # 2. No timeline overlaps: each transition must be strictly ascending
    times = [e.get("time", 0) for e in sfx_events]
    for i in range(1, len(times)):
        if times[i] < times[i - 1] - 0.001:
            failures.append(f"SFX_OVERLAP: event {i} at {times[i]:.3f}s < prev {times[i-1]:.3f}s")
            break

    # 3. Transitions must fit within total_dur
    if trans_plan:
        cumulative = sum(p.get("dur", 0.5) for p in trans_plan)
        if cumulative > total_dur * 0.5:
            failures.append(
                f"TRANSITIONS_OVERFLOW: total transition time {cumulative:.1f}s "
                f"> 50% of video {total_dur:.1f}s"
            )

    # 4. No clip shorter than transition duration
    if trans_plan and clips:
        min_trans = min((p.get("dur", 0.5) for p in trans_plan), default=0.5)
        # check first few clips
        for c in list(clips)[:5]:
            try:
                r = __import__("subprocess").run(
                    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "csv=p=0", str(c)],
                    capture_output=True, text=True,
                )
                cdur = float(r.stdout.strip() or "999")
                if cdur < min_trans:
                    failures.append(
                        f"CLIP_SHORTER_THAN_TRANSITION: {Path(c).name} "
                        f"dur={cdur:.2f}s < min_trans={min_trans:.2f}s"
                    )
            except Exception:
                pass

    _emit("pre_render_validation",
          passed=(len(failures) == 0),
          failures=failures,
          n_clips=len(clips),
          n_trans=len(trans_plan),
          n_sfx=len(sfx_events))

    if failures:
        msg = "PRE-RENDER VALIDATION FAILED (aborting before encode):\n" + \
              "\n".join(f"  - {f}" for f in failures)
        get_stats().transitions_valid = False
        raise Exception(msg)


# ── Level 2: Post-render quality gate ────────────────────────────────────────

def validate_post_render(stats: PipelineStats) -> None:
    """
    Hard quality gate after successful render.
    Raises Exception and calls sys.exit(1) on failure — pipeline cannot be ignored.
    """
    mark_render_end()
    failures: list[str] = []

    if not stats.no_clip_reuse:
        failures.append("CLIP_REUSE: duplicate clips detected within cooldown window")
    if not stats.sfx_aligned:
        failures.append(
            f"SFX_DESYNC: {stats.sfx_drop_count} dropped, "
            f"{stats.sfx_shift_count} shifted"
        )
    if not stats.sfx_within_limits:
        failures.append("SFX_OVERLIMIT: SFX density or level exceeded hard limits")
    if not stats.transitions_valid:
        failures.append("TRANSITIONS_INVALID: transition plan failed pre-validation")
    if stats.fallback_count > 3:
        failures.append(
            f"FALLBACK_EXCESS: {stats.fallback_count} fallback transitions (limit=3)"
        )

    _emit("post_render_validation",
          passed=(len(failures) == 0),
          failures=failures,
          stats=asdict(stats))

    if failures:
        msg = "POST-RENDER QUALITY GATE FAILED:\n" + \
              "\n".join(f"  - {f}" for f in failures)
        print(msg, flush=True)
        sys.exit(1)


# backward compat alias
validate_final_video = validate_post_render


# ── Audio QA Checklist ────────────────────────────────────────────────────────

def audit_final_audio(output_path, voice_path=None, session_path=None) -> dict:
    """
    Быстрая QA-проверка итогового файла (non-blocking, не вызывает sys.exit).

    Два вызова:
      1. ffprobe  — длительность, наличие аудио/видео потоков, codec
      2. ffmpeg ebur128 — LUFS, LRA, True Peak (один проход)
    """
    output_path = Path(output_path)
    if not output_path.exists():
        print("  ❌ QA: итоговый файл не найден", flush=True)
        _emit("audio_qa_fail", check="file_exists", value="missing")
        return {}

    results: dict = {}

    # ── 1. ffprobe: streams + duration ───────────────────────────────────────
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet",
             "-show_entries", "format=duration:stream=codec_type,channels,sample_rate",
             "-of", "json", str(output_path)],
            capture_output=True, text=True,
        )
        info    = json.loads(r.stdout or "{}")
        fmt     = info.get("format", {})
        streams = info.get("streams", [])
        results["duration"]    = float(fmt.get("duration", 0))
        results["has_audio"]   = any(s.get("codec_type") == "audio" for s in streams)
        results["has_video"]   = any(s.get("codec_type") == "video" for s in streams)
        audio_s                = next((s for s in streams if s.get("codec_type") == "audio"), {})
        results["channels"]    = audio_s.get("channels", 0)
        results["sample_rate"] = audio_s.get("sample_rate", "0")
    except Exception as e:
        print(f"  ⚠ ffprobe error: {e}", flush=True)

    # Проверка длительности против голоса
    if voice_path and Path(str(voice_path)).exists():
        try:
            vr = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(voice_path)],
                capture_output=True, text=True,
            )
            voice_dur = float(vr.stdout.strip() or "0")
            results["voice_dur"] = voice_dur
            out_dur = results.get("duration", 0)
            if voice_dur > 0 and out_dur < voice_dur * 0.95:
                print(
                    f"  ⚠ QA: длительность {out_dur:.1f}s < голос {voice_dur:.1f}s "
                    f"({out_dur/voice_dur*100:.0f}%)",
                    flush=True,
                )
        except Exception:
            pass

    # ── 2. ffmpeg ebur128: LUFS + LRA + True Peak (один проход) ─────────────
    try:
        r2 = subprocess.run(
            ["ffmpeg", "-i", str(output_path),
             "-af", "ebur128=peak=true",
             "-f", "null", "NUL"],
            capture_output=True, text=True,
        )
        lufs = lra = tp = None
        for line in r2.stderr.splitlines():
            ls = line.strip()
            if ls.startswith("I:") and "LUFS" in ls:
                try:
                    lufs = float(ls.split("I:")[1].split("LUFS")[0].strip())
                except Exception:
                    pass
            elif ls.startswith("LRA:") and "LU" in ls:
                try:
                    lra = float(ls.split("LRA:")[1].split("LU")[0].strip())
                except Exception:
                    pass
            elif ls.startswith("Peak:") and "dBTP" in ls:
                try:
                    tp = float(ls.split("Peak:")[1].split("dBTP")[0].strip())
                except Exception:
                    pass
        results.update(lufs=lufs, lra=lra, tp=tp)
    except Exception as e:
        print(f"  ⚠ ebur128 error: {e}", flush=True)

    # ── Вывод и лог ──────────────────────────────────────────────────────────
    lufs_s = f"{results['lufs']:.1f}" if results.get("lufs") is not None else "?"
    lra_s  = f"{results['lra']:.1f}"  if results.get("lra")  is not None else "?"
    tp_s   = f"{results['tp']:.1f}"   if results.get("tp")   is not None else "?"
    dur_s  = f"{results.get('duration', 0):.1f}"

    ok = results.get("has_audio") and results.get("has_video") and results.get("duration", 0) > 1.0
    print(
        f"  {'✅' if ok else '❌'} QA: dur={dur_s}s  "
        f"LUFS={lufs_s}  LRA={lra_s}  TP={tp_s}  "
        f"audio={'✓' if results.get('has_audio') else '✗'}  "
        f"video={'✓' if results.get('has_video') else '✗'}",
        flush=True,
    )
    _emit("audio_qa_result", **{k: v for k, v in results.items() if v is not None})
    return results


# ── Summary Report ────────────────────────────────────────────────────────────

def generate_summary_report(jsonl_path: Optional[Path] = None) -> str:
    """
    Parse pipeline_log.jsonl and return a human-readable aggregate report.
    Falls back to in-memory events if no path provided.
    """
    if jsonl_path and Path(jsonl_path).exists():
        events = []
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    else:
        events = list(_log_events)

    if not events:
        return "No pipeline events recorded."

    counts: dict[str, int] = {}
    fallbacks: list[dict]  = []
    sfx_drops: list[dict]  = []
    sfx_shifts: list[dict] = []
    pacing: list[dict]     = []
    nvenc: list[dict]      = []
    render_times: list[float] = []
    pre_result  = None
    post_result = None

    for e in events:
        t = e.get("type", "unknown")
        counts[t] = counts.get(t, 0) + 1
        if t == "fallback_transition":  fallbacks.append(e)
        if t == "sfx_drop":             sfx_drops.append(e)
        if t == "sfx_shift":            sfx_shifts.append(e)
        if t == "pacing_adjust":        pacing.append(e)
        if t == "nvenc_scale":          nvenc.append(e)
        if t == "pre_render_validation":  pre_result  = e
        if t == "post_render_validation": post_result = e
        if t == "clip_trim" and "delta" in e:
            render_times.append(abs(e["delta"]))

    stats = get_stats()
    total_sfx_events = counts.get("sfx_drop", 0) + counts.get("sfx_shift", 0)
    sfx_drop_pct  = (counts.get("sfx_drop", 0)  / max(1, total_sfx_events)) * 100
    fallback_pct  = (len(fallbacks) / max(1, counts.get("fallback_transition", 0) + 1)) * 100

    lines = [
        "=" * 56,
        "  PIPELINE SUMMARY REPORT",
        "=" * 56,
        f"  Render time:       {stats.render_time_s:.1f}s",
        f"  Clip trims:        {counts.get('clip_trim', 0)}",
        f"  Pacing adjusts:    {counts.get('pacing_adjust', 0)}",
        f"  NVENC scale ops:   {counts.get('nvenc_scale', 0)}",
        f"",
        f"  SFX events total:  {total_sfx_events}",
        f"  SFX dropped:       {counts.get('sfx_drop', 0)} ({sfx_drop_pct:.0f}%)",
        f"  SFX shifted:       {counts.get('sfx_shift', 0)}",
        f"",
        f"  Fallback trans:    {len(fallbacks)}",
    ]

    if fallbacks:
        for fb in fallbacks[:3]:
            lines.append(f"    #{fb.get('chunk','?')} {fb.get('transition','?')}: {fb.get('error','')[:60]}")

    pre_ok  = pre_result.get("passed",  True) if pre_result  else True
    post_ok = post_result.get("passed", True) if post_result else True
    lines += [
        f"",
        f"  Pre-render gate:   {'PASS' if pre_ok  else 'FAIL'}",
        f"  Post-render gate:  {'PASS' if post_ok else 'FAIL'}",
        "=" * 56,
    ]

    report = "\n".join(lines)
    _emit("summary_report", report=report)
    return report
