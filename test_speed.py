"""
test_speed.py — мини-тест всего пайплайна на N сегментах.

Запуск:
    python test_speed.py --channel es --segments 20
    python test_speed.py --channel de --segments 20
    python test_speed.py --channel fr --segments 20

Тестирует все шаги:
  1. Загрузка embeddings (D: SSD)
  2. Clip selection (CLIP + e5 matching)
  3. Trim клипов (чтение D: SSD → запись C: SSD)
  4. Render видеоряда (gray_wipe / crossfade / fadeblack)
  5. Аудио микс
  6. Финальный рендер с субтитрами
"""
import sys, os, time, json, shutil, argparse, subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "agents" / "assembler"))
sys.path.insert(0, str(Path(__file__).parent / "agents" / "library"))
sys.path.insert(0, str(Path(__file__).parent / "agents" / "utils"))
sys.path.insert(0, str(Path(__file__).parent / "agents"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from paths import get_clips_dir, get_library_json, get_embeddings_file, get_niche
from paths import CHANNELS_DIR

CHANNEL_MAP = {
    "de": "channel_001_cosmos_de",
    "fr": "channel_002_cosmos_fr",
    "es": "channel_003_religion_es",
}

def t(label, fn):
    t0 = time.time()
    result = fn()
    elapsed = time.time() - t0
    print(f"  ✅ {label}: {elapsed:.1f}s")
    return result, elapsed

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default="es", choices=["de", "fr", "es"])
    parser.add_argument("--segments", type=int, default=20)
    args = parser.parse_args()

    channel_id = CHANNEL_MAP[args.channel]
    N = args.segments

    print(f"\n{'='*55}")
    print(f"Speed test — канал: {args.channel.upper()}, {N} сегментов")
    print(f"{'='*55}")

    # Найти последнюю сессию
    lang_dir = CHANNELS_DIR / args.channel
    sessions = sorted(lang_dir.iterdir()) if lang_dir.exists() else []
    session_dir = next((s for s in reversed(sessions)
                        if (s / "transcripts" / "result.json").exists()
                        and (s / "input").exists()), None)
    if not session_dir:
        print("❌ Нет сессии с result.json"); sys.exit(1)

    session = session_dir.name
    result_json = session_dir / "transcripts" / "result.json"
    audio_file  = next((session_dir / "input").glob("*.mp3"), None) or \
                  next((session_dir / "input").glob("*.wav"), None)

    print(f"Сессия:  {session}")
    print(f"Аудио:   {audio_file.name if audio_file else 'не найден'}")

    from paths import TEMP_ROOT
    from transitions import set_temp_dir
    out_dir  = Path("data/speed_test") / args.channel
    temp_dir = TEMP_ROOT / "speed_test" / args.channel
    shutil.rmtree(out_dir, ignore_errors=True)
    shutil.rmtree(temp_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    temp_dir.mkdir(parents=True)
    set_temp_dir(temp_dir)

    t0_total = time.time()

    # ── 1. Embeddings ─────────────────────────────────────────────
    print(f"\n[1] Загрузка embeddings с {get_embeddings_file(channel_id).drive}:")
    def load_emb():
        import numpy as np
        npz = get_embeddings_file(channel_id)
        data = np.load(str(npz))
        return len(data.files)
    n_emb, t1 = t("embeddings", load_emb)
    print(f"      {n_emb} arrays, файл: {get_embeddings_file(channel_id)}")

    # ── 2. Clip selection ─────────────────────────────────────────
    print(f"\n[2] Clip selection ({N} сегментов):")
    def select():
        from clip_selector import select_clips_for_video
        with open(result_json, encoding="utf-8") as f:
            data = json.load(f)
        segs = data.get("segments", [])[:N]
        # patch total_duration
        if segs:
            data_mini = {**data, "segments": segs,
                         "total_duration": float(segs[-1].get("end", 100))}
            mini_json = out_dir / "result_mini.json"
            mini_json.write_text(json.dumps(data_mini), encoding="utf-8")
        return select_clips_for_video(
            session=session, channel_id=channel_id,
            segments=segs, max_repeats_in_video=5, max_from_prev=5,
            intro_duration=0.0,
        )
    result, t2 = t("clip selection", select)
    main_clips = result.get("main_clips", [])
    print(f"      подобрано: {len(main_clips)} клипов")

    # ── 3. Trim клипов ────────────────────────────────────────────
    clips_src_drive = get_clips_dir(channel_id).drive
    print(f"\n[3] Trim клипов ({clips_src_drive} → {temp_dir.drive}):")
    clips_dir = get_clips_dir(channel_id)
    trim_dir  = temp_dir / "trimmed"
    trim_dir.mkdir(parents=True, exist_ok=True)

    def trim():
        tasks = []
        for seg_id, clip_id, dur in main_clips[:N]:
            if clip_id is None: continue
            src = clips_dir / f"{clip_id}.mp4"
            dst = trim_dir / f"clip_{int(seg_id):03d}.mp4"
            if not src.exists(): continue
            tasks.append((src, dst, float(dur)))
        from concurrent.futures import ThreadPoolExecutor
        def _trim(args):
            src, dst, dur = args
            subprocess.run([
                "ffmpeg", "-y", "-i", str(src),
                "-t", f"{dur:.3f}", "-c", "copy",
                "-loglevel", "error", str(dst)
            ], check=False)
            return dst
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(_trim, tasks))
        return [r for r in results if r.exists()]
    trimmed, t3 = t("trim clips", trim)
    total_trim_mb = sum(f.stat().st_size for f in trimmed) / 1024 / 1024
    print(f"      {len(trimmed)} клипов, {total_trim_mb:.0f}MB → {trim_dir.drive}:")

    if not trimmed:
        print("❌ Нет обрезанных клипов"); sys.exit(1)

    # ── 4. Render видеоряда ───────────────────────────────────────
    print(f"\n[4] Render видеоряда ({len(trimmed)} клипов):")
    videotrack = out_dir / "videotrack.mp4"

    # Импортируем стиль канала
    with open("config/channel_styles.json", encoding="utf-8") as f:
        styles = json.load(f)
    style = styles.get(channel_id, {})
    trans_name = style.get("clip_transition", "crossfade")
    trans_dur  = float(style.get("clip_transition_duration", 0.5))
    durations  = [float(d) for _, _, d in main_clips[:len(trimmed)]]

    def render_video():
        from transitions import concat_all_with_transitions
        concat_all_with_transitions(trimmed, videotrack, trans_name, trans_dur)
        return videotrack.exists()
    ok, t4 = t(f"videotrack ({trans_name})", render_video)
    if videotrack.exists():
        vt_mb = videotrack.stat().st_size / 1024 / 1024
        print(f"      {vt_mb:.0f}MB → {videotrack.drive}:")

    # ── 5. Аудио ──────────────────────────────────────────────────
    print(f"\n[5] Аудио микс:")
    audio_out = out_dir / "audio.m4a"
    total_dur = durations[0] if durations else 30.0
    total_dur = sum(durations)

    def mix_audio():
        if not audio_file: return False
        subprocess.run([
            "ffmpeg", "-y", "-i", str(audio_file),
            "-t", f"{total_dur:.1f}",
            "-c:a", "aac", "-b:a", "192k",
            "-loglevel", "error", str(audio_out)
        ], check=False)
        return audio_out.exists()
    ok_audio, t5 = t("audio mix", mix_audio)

    # ── 6. Финальный рендер ───────────────────────────────────────
    print(f"\n[6] Финальный рендер:")
    final_out = out_dir / f"test_{args.channel}_final.mp4"

    # ASS субтитры
    ass_path = out_dir / "test.ass"
    try:
        from ass_generator import generate_ass, generate_karaoke_ass, generate_scripture_ass
        sub_style = style.get("subtitle_style", "default")
        mini_result = out_dir / "result_mini.json"
        if not mini_result.exists():
            mini_result = result_json
        intro_dur = 0.0
        if sub_style == "karaoke":
            generate_karaoke_ass(str(mini_result), str(ass_path),
                style.get("subtitle_font", "Montserrat Bold"), 56,
                60, 120, intro_dur, 3, "&H00FFFFFF", "&H00707070", 1)
        elif sub_style == "scripture":
            generate_scripture_ass(str(mini_result), str(ass_path),
                style.get("subtitle_font", "Arial Black"), 66,
                350, 200, intro_dur, 3)
        else:
            generate_ass(str(mini_result), str(ass_path),
                "Organetto Bold", 48, 120, 120, 50, intro_dur, 28, 3)
        ass_ok = ass_path.exists()
    except Exception as e:
        print(f"  ⚠️ ASS: {e}")
        ass_ok = False

    def final():
        ass_posix = str(ass_path).replace("\\", "/")
        if len(ass_posix) >= 2 and ass_posix[1] == ":":
            ass_posix = ass_posix[0] + "\\:" + ass_posix[2:]
        sub_f = f"ass='{ass_posix}'" if ass_ok else ""
        vf = f"eq=contrast=1.05:saturation=1.04{','+ sub_f if sub_f else ''}"

        inputs = ["-i", str(videotrack)]
        if ok_audio and audio_out.exists():
            inputs += ["-i", str(audio_out)]

        cmd = ["ffmpeg", "-y"] + inputs + [
            "-vf", vf,
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19",
            "-c:a", "aac", "-b:a", "192k",
            "-loglevel", "error", str(final_out)
        ]
        r = subprocess.run(cmd)
        if r.returncode != 0:
            # fallback x264
            cmd[cmd.index("h264_nvenc")] = "libx264"
            cmd[cmd.index("-preset")+1]  = "fast"
            subprocess.run(cmd, check=False)
        return final_out.exists()
    ok_final, t6 = t("final render (NVENC)", final)
    if final_out.exists():
        sz = final_out.stat().st_size / 1024 / 1024
        print(f"      {sz:.0f}MB → {final_out}")

    # ── Итог ─────────────────────────────────────────────────────
    total = time.time() - t0_total
    print(f"\n{'='*55}")
    print(f"ИТОГ  ({N} сегментов, ~{sum(durations):.0f}s видео)")
    print(f"{'='*55}")
    print(f"  Embeddings:    {t1:.1f}s  (D: SSD)")
    print(f"  Clip select:   {t2:.1f}s")
    print(f"  Trim clips:    {t3:.1f}s  (D: SSD trim+copy)")
    print(f"  Videotrack:    {t4:.1f}s  (D: SSD render)")
    print(f"  Audio:         {t5:.1f}s")
    print(f"  Final render:  {t6:.1f}s  (C: SSD)")
    print(f"  ─────────────────────")
    print(f"  TOTAL:         {total:.1f}s  ({total/60:.1f}мин)")
    print(f"{'='*55}\n")

if __name__ == "__main__":
    main()
