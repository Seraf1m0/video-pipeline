"""
setup_sfx.py — копирует SFX-файлы из исходной папки в assets/sfx/<категория>/

Запуск: python assets/sfx/setup_sfx.py
"""

import shutil
from pathlib import Path

SRC = Path("E:/Футажи и музыка/Звуки для монтажа")
DST = Path(__file__).resolve().parent

# ─── Маппинг: (glob от SRC, категория) ───────────────────────────────────────
RULES: list[tuple[str, str]] = [

    # ── whoosh (обычные, средние) ──────────────────────────────────────────────
    ("Whoosh SFX/Woosh Effect *.wav",       "whoosh"),
    ("Whoosh SFX/SCH - Whoosh - *.mp3",     "whoosh"),
    ("Whoosh SFX/Digital/dig_whoosh_*.wav", "whoosh"),
    ("Whoosh SFX/1.mp3",                    "whoosh"),
    ("Whoosh SFX/2.mp3",                    "whoosh"),
    ("Whoosh SFX/2(1).mp3",                 "whoosh"),
    ("Whoosh SFX/3.mp3",                    "whoosh"),
    ("Whoosh SFX/4.mp3",                    "whoosh"),
    ("Whoosh SFX/8.mp3",                    "whoosh"),
    ("Whoosh SFX/9.mp3",                    "whoosh"),
    ("Whoosh SFX/13.mp3",                   "whoosh"),
    ("Whoosh SFX/13.wav",                   "whoosh"),
    ("Whoosh SFX/16.mp3",                   "whoosh"),
    ("Whoosh SFX/ДЛЯ ПЕРЕХОДА.mp3",         "whoosh"),
    ("Whoosh SFX/Переход.mp3",              "whoosh"),
    ("Whoosh SFX/ТОП 1.mp3",               "whoosh"),

    # ── whoosh_big (тяжёлые, для whip_pan) ────────────────────────────────────
    ("Whoosh SFX/Big/big_whoosh_*.wav",                                "whoosh_big"),
    ("NEGATIVIST AUDIO - VITAL CINEMATIC SFX - FREE PACK/NGTVST - Whoosh *.wav", "whoosh_big"),
    ("Whoosh SFX/Zoom Whoosh1.wav",                                    "whoosh_big"),
    ("Whoosh SFX/Zoom Whosh2.wav",                                     "whoosh_big"),
    ("Whoosh SFX/MINI slide.wav",                                      "whoosh_big"),

    # ── whoosh_fast (лёгкие быстрые, для smooth_zoom / cross_zoom) ────────────
    ("Whoosh SFX/Fast/fast_whoosh_*.wav",      "whoosh_fast"),
    ("Whoosh SFX/SCH - Whoosh Fast - *.mp3",   "whoosh_fast"),
    ("Whoosh SFX/Fisheye_Simple_01.mp3",       "whoosh_fast"),
    ("Whoosh SFX/Flat_Short_01.mp3",           "whoosh_fast"),
    ("Whoosh SFX/Flat_Zoom_01.mp3",            "whoosh_fast"),

    # ── impact (удары, каждый 5-й переход) ────────────────────────────────────
    ("NEGATIVIST AUDIO - VITAL CINEMATIC SFX - FREE PACK/NGTVST - Hit *.wav",  "impact"),
    ("Hits/Punch Hit Kick *.mp3",                                               "impact"),
    ("Hits/Distortion_Zoom_Hit_01.mp3",                                         "impact"),
    ("Hits/удар  .  kick.mp3",                                                  "impact"),

    # ── glitch (для glitch_flash переходов) ───────────────────────────────────
    ("[Глитч]/glitch *.wav",                "glitch"),
    ("[Глитч]/5.wav",                       "glitch"),
    ("[Глитч]/глитч звук.wav",             "glitch"),
    ("[Глитч]/pomehi-shumnyiy-blizkiy-aktivnyiy.mp3", "glitch"),
    ("[Глитч]/zvuk3.mp3",                   "glitch"),

    # ── riser (нарастание перед сильным переходом, ~1s до) ────────────────────
    ("NEGATIVIST AUDIO - VITAL CINEMATIC SFX - FREE PACK/NGTVST - Riser *.wav", "riser"),

    # ── downlifter (после перехода интро→видеоряд) ────────────────────────────
    ("NEGATIVIST AUDIO - VITAL CINEMATIC SFX - FREE PACK/NGTVST - Downer *.wav", "downlifter"),
    ("Басс дроп, bass drop/Басс дроп (*.wav",                                     "downlifter"),

    # ── boom (кинематографический удар, каждый 10-й переход) ─────────────────
    ("NEGATIVIST AUDIO - VITAL CINEMATIC SFX - FREE PACK/NGTVST - Boom *.wav",    "boom"),
    ("NEGATIVIST AUDIO - VITAL CINEMATIC SFX - FREE PACK/NGTVST - Braam *.wav",   "boom"),
    ("NEGATIVIST AUDIO - VITAL CINEMATIC SFX - FREE PACK/NGTVST - Warhorn *.wav", "boom"),
]


def run():
    total = 0
    for pattern, category in RULES:
        cat_dir = DST / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        # glob() treats [ ] as char-class; use rglob on the specific subfolder when needed
        if "[" in pattern:
            # Split on first /, treat the rest as a glob within that literal subdir
            parts = pattern.split("/", 1)
            subdir = SRC / parts[0]
            matches = list(subdir.glob(parts[1])) if len(parts) > 1 else [subdir / parts[0]]
        else:
            matches = list(SRC.glob(pattern))
        for src_file in matches:
            dst_file = cat_dir / src_file.name
            if not dst_file.exists():
                shutil.copy2(src_file, dst_file)
                print(f"  ✓ [{category}] {src_file.name}")
                total += 1
            else:
                print(f"  · [{category}] {src_file.name} (уже есть)")
    print(f"\n✅ Скопировано: {total} файлов")
    for cat_dir in sorted(DST.iterdir()):
        if cat_dir.is_dir() and cat_dir.name != "__pycache__":
            n = len(list(cat_dir.glob("*.*")))
            print(f"   {cat_dir.name}/  — {n} файлов")


if __name__ == "__main__":
    run()
