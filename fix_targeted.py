"""Замена конкретных плохих видео с BLIP-валидацией кандидатов."""
import sys, shutil, json
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'agents/video_validator')

from stock_finder import find_stock_video, _used_stock_ids, _used_stock_urls

UPSCALED = Path('data/media/Video_20260313_204235/upscaled')

# Все известные плохие ID — чтобы не нарваться снова
BAD_IDS = {
    'pixabay_10',      # старый дубль #085
    'pixabay_10715',   # океан/облака
    'pixabay_7350',    # виниловый проигрыватель
    'pixabay_214946',  # здание
    'pixabay_334395',  # галактика #011 (уже занят)
    'pixabay_55990',   # земля из космоса #038 (уже занят)
}
_used_stock_ids.update(BAD_IDS)

# Сегменты из транскрипта для правильного запроса
SEGMENTS = {
    76: 'Strahlungsenergie und Galaxienentstehung im frühen Universum',
    85: 'Schwarze Löcher und Gravitationswellen im Universum',
}

def fix(seg_num: int):
    target = UPSCALED / f'video_{seg_num:03d}.mp4'
    seg_text = SEGMENTS.get(seg_num, 'cosmos universe space galaxy')

    print(f'\n{"="*60}')
    print(f'Fixing #{seg_num:03d}: "{seg_text[:60]}"')

    stock = find_stock_video(
        segment_text=seg_text,
        niche='cosmos',
        min_duration=10,
        segment_idx=seg_num,
    )

    if not stock:
        print(f'  FAILED: нет стока для #{seg_num}')
        return False

    local = stock.get('local_path')
    if not local or not Path(local).exists():
        print(f'  FAILED: local_path не найден')
        return False

    backup = target.with_suffix('.bak.mp4')
    shutil.copy2(target, backup)
    shutil.move(local, target)
    backup.unlink(missing_ok=True)

    print(f'  DONE: #{seg_num:03d} => {stock["id"]} ({stock["source"]})')

    # Сохраняем кадр для проверки
    import subprocess
    r = subprocess.run(['ffprobe','-v','quiet','-print_format','json','-show_format',str(target)],
                       capture_output=True, text=True)
    dur = float(json.loads(r.stdout)['format']['duration'])
    chk = Path(f'temp/chk_fixed_{seg_num:03d}.jpg')
    subprocess.run(['ffmpeg','-y','-ss',str(dur*0.5),'-i',str(target),
                    '-vframes','1','-q:v','2','-loglevel','quiet',str(chk)], capture_output=True)
    print(f'  Frame: {chk.name}')
    return True

fix(76)
fix(85)

print('\n\nГотово!')
