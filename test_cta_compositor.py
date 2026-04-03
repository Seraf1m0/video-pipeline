"""
test_cta_compositor.py — тест CTA overlay, вшитого в GPU compositor

Проверяет, что prerender_cta + composite_cta работают корректно
для всех 3 каналов (DE, FR, ES).

Что делает:
  1. prerender_cta — декодирует каждый .mov, проверяет размеры и кол-во кадров
  2. composite_cta — рендерит синтетический кадр с CTA на GPU, сохраняет PNG
  3. end-to-end  — нарезает 120s из реального финального видео, прогоняет
                   gpu_compositor.run() с CTA, проверяет выход

Запуск:
    python test_cta_compositor.py
    python test_cta_compositor.py --skip-e2e   # только шаги 1-2
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "agents/assembler")

import numpy as np
import torch
from PIL import Image

from gpu_compositor import prerender_cta, composite_cta

OUT_DIR = Path("D:/Video-pipeline-temp/test_cta_compositor")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ─── Конфигурация каналов ─────────────────────────────────────────────────────

CHANNELS = [
    {
        "id":           "de",
        "label":        "DE — cosmos_de",
        "cta_mov":      Path("D:/Video-pipeline-temp/test_cta_de.mov"),
        "cta_dur_exp":  6.5,     # ожидаемая длительность (s)
        "fps":          25.0,
        "color_grade":  "warm_cinematic",
        "final_video":  Path("D:/Video-pipeline-data/channels/de/Video_20260401_223509/output/Video_20260401_223509_DE_final.mp4"),
        "cut_from_s":   120.0,   # откуда нарезать для e2e
        "cut_dur_s":    120.0,
    },
    {
        "id":           "fr",
        "label":        "FR — cosmos_fr",
        "cta_mov":      Path("D:/Video-pipeline-temp/test_cta_fr.mov"),
        "cta_dur_exp":  13.0,
        "fps":          25.0,
        "color_grade":  "cool_cinematic",
        "final_video":  Path("D:/Video-pipeline-data/channels/fr/Video_20260402_180801/output/Video_20260402_180801_FR_final.mp4"),
        "cut_from_s":   120.0,
        "cut_dur_s":    120.0,
    },
    {
        "id":           "es",
        "label":        "ES — religion_es",
        "cta_mov":      Path("D:/Video-pipeline-temp/test_cta_es.mov"),
        "cta_dur_exp":  13.0,
        "fps":          25.0,
        "color_grade":  "gold_dramatic",
        "final_video":  Path("D:/Video-pipeline-data/channels/es/Video_20260401_224228/output/Video_20260401_224228_ES_final.mp4"),
        "cut_from_s":   60.0,
        "cut_dur_s":    120.0,
    },
]

PASS = "✓"
FAIL = "✗"


def probe_dur(p: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration", "-of", "csv=p=0", str(p)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


# ─── Шаг 1: prerender_cta ────────────────────────────────────────────────────

def step1_prerender() -> dict[str, dict | None]:
    """Декодируем все CTA .mov, проверяем размеры."""
    print("\n" + "=" * 60)
    print("  Шаг 1: prerender_cta — декодирование CTA .mov")
    print("=" * 60)

    results = {}
    for ch in CHANNELS:
        lang    = ch["id"]
        mov     = ch["cta_mov"]
        fps     = ch["fps"]
        dur_exp = ch["cta_dur_exp"]

        if not mov.exists():
            print(f"  [{FAIL}] {lang}: файл не найден: {mov}")
            results[lang] = None
            continue

        t0 = time.time()
        data = prerender_cta(mov, fps)
        elapsed = time.time() - t0

        if data is None:
            print(f"  [{FAIL}] {lang}: prerender_cta вернул None")
            results[lang] = None
            continue

        n        = data["n"]
        dur      = data["dur"]
        h, w     = data["frames_np"].shape[1], data["frames_np"].shape[2]
        ok_res   = (h == 1080 and w == 1920)
        ok_dur   = abs(dur - dur_exp) < 1.0
        ok_dtype = data["frames_np"].dtype == np.uint8

        status = PASS if (ok_res and ok_dur and ok_dtype) else FAIL
        print(f"  [{status}] {ch['label']}: {n} кадров  {w}x{h}  "
              f"{dur:.1f}s (ожид.≈{dur_exp:.1f}s)  dtype={data['frames_np'].dtype}  "
              f"decode={elapsed:.1f}s")

        if not ok_res:
            print(f"         [{FAIL}] Ожидалось 1920x1080, получили {w}x{h}")
        if not ok_dur:
            print(f"         [{FAIL}] Длительность {dur:.1f}s ≠ {dur_exp:.1f}s")

        results[lang] = data

    return results


# ─── Шаг 2: composite_cta (синтетический кадр) ───────────────────────────────

def step2_composite(cta_data_map: dict[str, dict | None]):
    """Накладываем CTA на серый синтетический кадр, сохраняем PNG."""
    print("\n" + "=" * 60)
    print("  Шаг 2: composite_cta — наложение на синтетический кадр")
    print("=" * 60)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    # Серый 1920x1080 кадр [H, W, 3] float
    gray = torch.full((1080, 1920, 3), 0.45, dtype=torch.float32, device=device)

    for ch in CHANNELS:
        lang = ch["id"]
        data = cta_data_map.get(lang)
        if data is None:
            print(f"  [SKIP] {lang}: нет данных prerender")
            continue

        fps = ch["fps"]
        cta_dur = data["dur"]

        # Тест 3 случаев:
        # A) frame_num=0   — CTA не должна быть видна (до первой плашки)
        # B) frame_num соответствует старту первой плашки + 1s → CTA должна быть видна
        # C) frame_num после конца первой плашки → CTA не должна быть видна

        ts_first = 5.0   # первая плашка через 5s
        timestamps = [ts_first, ts_first + 40.0]

        # Кадр ДО плашки
        fn_before = int((ts_first - 2.0) * fps)
        frame_before = composite_cta(gray.clone(), fn_before, fps, data, timestamps)
        diff_before = (frame_before - gray).abs().max().item()

        # Кадр В середине плашки
        fn_mid = int((ts_first + cta_dur / 2) * fps)
        frame_mid = composite_cta(gray.clone(), fn_mid, fps, data, timestamps)
        diff_mid = (frame_mid - gray).abs().max().item()

        # Кадр ПОСЛЕ плашки
        fn_after = int((ts_first + cta_dur + 1.0) * fps)
        frame_after = composite_cta(gray.clone(), fn_after, fps, data, timestamps)
        diff_after = (frame_after - gray).abs().max().item()

        ok_before = diff_before < 0.01   # не должно быть изменений
        ok_mid    = diff_mid > 0.01      # должны быть изменения (CTA наложена)
        ok_after  = diff_after < 0.01    # CTA завершилась → нет изменений

        status = PASS if (ok_before and ok_mid and ok_after) else FAIL
        print(f"  [{status}] {ch['label']}")
        print(f"         до плашки (frame {fn_before}):   diff={diff_before:.4f} "
              f"{'OK — нет оверлея' if ok_before else FAIL + ' — должно быть 0!'}")
        print(f"         в плашке  (frame {fn_mid}):   diff={diff_mid:.4f} "
              f"{'OK — CTA видна' if ok_mid else FAIL + ' — CTA НЕ накладывается!'}")
        print(f"         после     (frame {fn_after}): diff={diff_after:.4f} "
              f"{'OK — нет оверлея' if ok_after else FAIL + ' — должно быть 0!'}")

        # Сохраняем PNG для визуальной проверки
        png_path = OUT_DIR / f"cta_frame_{lang}.png"
        arr = (frame_mid.clamp(0, 1) * 255).byte().cpu().numpy().astype(np.uint8)
        Image.fromarray(arr, "RGB").save(png_path)
        print(f"         Сохранён PNG: {png_path.name}")


# ─── Шаг 3: end-to-end gpu_compositor.run() ──────────────────────────────────

def step3_e2e():
    """Запускаем gpu_compositor.run() на коротком клипе с CTA."""
    from gpu_compositor import run as gpu_run
    from apply_cta import compute_timestamps, get_duration as cta_dur_fn

    print("\n" + "=" * 60)
    print("  Шаг 3: end-to-end — gpu_compositor.run() с CTA")
    print("=" * 60)

    results = []
    for ch in CHANNELS:
        lang       = ch["id"]
        cta_mov    = ch["cta_mov"]
        fps        = ch["fps"]
        final_vid  = ch["final_video"]
        cut_from   = ch["cut_from_s"]
        cut_dur    = ch["cut_dur_s"]
        grade      = ch["color_grade"]

        print(f"\n  [{lang.upper()}] {ch['label']}")

        # Проверяем наличие исходников
        if not final_vid.exists():
            print(f"    [SKIP] финальное видео не найдено: {final_vid}")
            results.append((lang, "SKIP"))
            continue
        if not cta_mov.exists():
            print(f"    [SKIP] CTA .mov не найден: {cta_mov}")
            results.append((lang, "SKIP"))
            continue

        # 1. Нарезаем клип из финального видео (только видео, без аудио)
        cut_path = OUT_DIR / f"e2e_cut_{lang}.mp4"
        print(f"    Нарезка {cut_dur:.0f}s c {cut_from:.0f}s → {cut_path.name}...")
        r = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{cut_from:.3f}", "-t", f"{cut_dur:.3f}",
            "-i", str(final_vid),
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20",
            "-an",
            str(cut_path),
        ], stdin=subprocess.DEVNULL)
        if r.returncode != 0 or not cut_path.exists():
            print(f"    [{FAIL}] нарезка не удалась")
            results.append((lang, "FAIL"))
            continue

        actual_dur = probe_dur(cut_path)
        print(f"    Клип: {actual_dur:.1f}s")

        # 2. Вычисляем тайминги CTA для короткого клипа
        # Используем маленький gap чтобы в 120s попало несколько плашек
        cta_dur_s = cta_dur_fn(cta_mov)
        timestamps = compute_timestamps(
            total_dur  = actual_dur,
            intro_dur  = 0.0,
            cta_dur    = cta_dur_s,
            min_gap_s  = 30.0,   # маленький gap для теста
            max_gap_s  = 40.0,
        )
        if not timestamps:
            # Fallback: вставить одну плашку в 15s
            timestamps = [15.0]
        print(f"    Тайминги: {[f'{t:.0f}s' for t in timestamps]}")

        # 3. gpu_compositor.run() — только videotrack, color_grade, CTA
        out_path = OUT_DIR / f"e2e_cta_{lang}.mp4"
        print(f"    Рендер → {out_path.name}...")
        t0 = time.time()
        ok = gpu_run(
            videotrack      = cut_path,
            output          = out_path,
            intro           = None,
            audio           = None,
            ass_path        = None,
            color_grade_name= grade,
            fps             = fps,
            cta_path        = cta_mov,
            cta_timestamps  = timestamps,
            cta_intro_dur   = 0.0,
        )
        elapsed = time.time() - t0

        if ok and out_path.exists():
            out_dur = probe_dur(out_path)
            out_mb  = out_path.stat().st_size // 1024 // 1024
            dur_ok  = abs(out_dur - actual_dur) < 2.0
            status  = PASS if dur_ok else FAIL
            print(f"    [{status}] {out_dur:.1f}s  {out_mb}MB  за {elapsed:.1f}s")
            if not dur_ok:
                print(f"           [{FAIL}] Ожидалось ~{actual_dur:.1f}s, получили {out_dur:.1f}s")
            results.append((lang, "PASS" if dur_ok else "FAIL"))
        else:
            print(f"    [{FAIL}] gpu_compositor вернул ошибку или файл не создан")
            results.append((lang, "FAIL"))

    return results


# ─── Итог ────────────────────────────────────────────────────────────────────

def print_summary(e2e_results: list | None = None):
    print("\n" + "=" * 60)
    print("  ЧТО ПРОВЕРЯТЬ ВРУЧНУЮ:")
    print("=" * 60)
    print(f"  Папка: {OUT_DIR}")
    print()
    print("  Синтетические кадры (Шаг 2):")
    for ch in CHANNELS:
        lang = ch["id"]
        print(f"    cta_frame_{lang}.png — CTA должна быть видна на сером фоне")
    print()
    print("  E2E видео (Шаг 3):")
    for ch in CHANNELS:
        lang = ch["id"]
        print(f"    e2e_cta_{lang}.mp4   — плашка должна появляться в указанные моменты")
    print()
    if e2e_results:
        print("  Итог e2e:")
        for lang, status in e2e_results:
            sym = PASS if status == "PASS" else ("—" if status == "SKIP" else FAIL)
            print(f"    [{sym}] {lang.upper()}: {status}")


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-e2e", action="store_true",
                    help="Пропустить шаг 3 (end-to-end рендер)")
    args = ap.parse_args()

    t_start = time.time()

    # Шаг 1
    cta_data_map = step1_prerender()

    # Шаг 2
    step2_composite(cta_data_map)

    # Шаг 3
    e2e_results = None
    if not args.skip_e2e:
        e2e_results = step3_e2e()
    else:
        print("\n  [Шаг 3 пропущен — --skip-e2e]")

    total = time.time() - t_start
    print(f"\n  Всего: {total:.1f}s")

    print_summary(e2e_results)
