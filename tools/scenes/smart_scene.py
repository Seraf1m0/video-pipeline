"""
smart_scene.py — SmartScene: точный саунд дизайн через ffmpeg постобработку.

Как работает:
  1. go() / hold() двигают курсор и складывают звуковые события в список
  2. После рендера маним, _remaster() строит ffmpeg filter_complex:
       - adelay   → ставит звук в нужную секунду
       - atrim    → обрезает начало файла (для peak alignment)
       - afade    → плавный вход/выход
       - volume   → нормализация по громкости
       - amix     → микс всех дорожек
       - заменяет аудио дорожку в финальном mp4

Peak alignment (align="peak"):
  Пик файла попадает ровно в base_t (момент события).
  Если пик позже base_t — обрезаем начало файла.

Нормализация:
  Каждый файл измеряется через volumedetect.
  Gain подбирается так чтобы mean_volume → lufs_target.
"""

import random
import subprocess
from pathlib import Path
from manim import *

_SFX = Path(__file__).resolve().parent.parent.parent / "assets" / "sfx"

_db_cache:   dict[str, float] = {}
_peak_cache: dict[str, float] = {}
_dur_cache:  dict[str, float] = {}

_SHORT = 0.3   # файлы короче → только anti-click, без fade-out


# ── Утилиты ───────────────────────────────────────────────────────────────────

def sfx_pick(cat: str, min_dur: float = None, max_dur: float = None,
             attempts: int = 40) -> str | None:
    d = _SFX / cat
    if not d.exists():
        return None
    fs = sorted([f for f in d.iterdir() if f.suffix.lower() in (".mp3", ".wav")])
    if not fs:
        return None
    if min_dur is None and max_dur is None:
        return str(random.choice(fs))
    sample = random.sample(fs, min(len(fs), attempts))
    filtered = [f for f in sample if _dur_ok(str(f), min_dur, max_dur)]
    return str(random.choice(filtered)) if filtered else str(random.choice(fs))


def sfx_dur(path: str) -> float:
    if path in _dur_cache:
        return _dur_cache[path]
    try:
        proc = subprocess.Popen(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        out, _ = proc.communicate()
        v = float(out.decode("utf-8", errors="replace").strip())
    except Exception:
        v = 2.0
    _dur_cache[path] = v
    return v


def _dur_ok(path: str, min_dur, max_dur) -> bool:
    d = sfx_dur(path)
    if min_dur and d < min_dur:
        return False
    if max_dur and d > max_dur:
        return False
    return True


def _measure_db(path: str) -> float:
    """Средняя громкость файла через volumedetect."""
    if path in _db_cache:
        return _db_cache[path]
    try:
        proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-i", str(path),
             "-af", "volumedetect", "-f", "null", "NUL"],
            stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
        )
        _, err = proc.communicate()
        for line in err.decode("utf-8", errors="replace").splitlines():
            if "mean_volume" in line:
                val = float(line.split(":")[-1].strip().replace(" dB", ""))
                _db_cache[path] = val
                return val
    except Exception:
        pass
    _db_cache[path] = -30.0
    return -30.0


def _find_peak_time(path: str) -> float:
    """Время пика громкости в файле через ebur128."""
    if path in _peak_cache:
        return _peak_cache[path]
    try:
        proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-i", str(path),
             "-af", "ebur128=peak=sample:framelog=verbose",
             "-f", "null", "NUL"],
            stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
        )
        _, err = proc.communicate()
        best_t, best_m = 0.0, -999.0
        for line in err.decode("utf-8", errors="replace").splitlines():
            if "M:" in line and "t:" in line:
                try:
                    t = float(line.split("t:")[1].split()[0])
                    m = float(line.split("M:")[1].split()[0])
                    if m > best_m:
                        best_m, best_t = m, t
                except Exception:
                    pass
        _peak_cache[path] = best_t
        return best_t
    except Exception:
        _peak_cache[path] = 0.0
        return 0.0


# ── Профили ───────────────────────────────────────────────────────────────────
# (папка, fade_in, fade_out, max_dur, lufs_target, align, min_dur)
#
# align="peak"  → пик файла попадает в base_t (момент события)
# align="start" → файл стартует в base_t

PROFILES: dict[str, tuple] = {
    # (folder, fade_in, fade_out, max_dur, lufs_target, align, min_dur)
    "whoosh":      ("whoosh",           0.00, 0.15, 2.5, -47.0, "peak",  0.5),
    "whoosh_fast": ("whoosh_fast",      0.00, 0.10, 0.8, -50.0, "peak",  0.3),
    "whoosh_big":  ("whoosh_big",       0.05, 0.20, 3.0, -44.0, "peak",  0.5),
    "tick":        ("infographic_tick", 0.00, 0.04, 0.5, -45.0, "start", None),
    "done":        ("notification",     0.05, 0.15, 2.0, -41.0, "start", 0.2),
    "keyboard":    ("keyboard",         0.00, 0.04, 0.3, -47.0, "start", None),
    "riser":       ("riser",            0.40, 0.50, 5.0, -48.0, "start", 1.0),
    "boom":        ("boom",             0.00, 0.00, 4.0, -42.0, "peak",  0.5),
}


# ── Событие ───────────────────────────────────────────────────────────────────

class _Ev:
    __slots__ = ("t", "path", "lufs", "fi", "fo", "ss", "trim")

    def __init__(self, t, path, lufs, fi, fo, ss=0.0, trim=None):
        self.t    = t       # абсолютное время старта в видео (секунды)
        self.path = path
        self.lufs = lufs    # целевой уровень нормализации
        self.fi   = fi      # fade-in duration
        self.fo   = fo      # fade-out duration
        self.ss   = ss      # обрезка начала файла (atrim start)
        self.trim = trim    # максимальная длина воспроизведения


# ═══════════════════════════════════════════════════════════════════════════════
# SmartScene
# ═══════════════════════════════════════════════════════════════════════════════

class SmartScene(Scene):

    def setup(self):
        self._cur  = 0.0
        self._evs: list[_Ev] = []

    @property
    def cursor(self) -> float:
        return self._cur

    def go(
        self,
        *anims,
        dur: float = 1.0,
        rf=smooth,
        sfx: str = None,
        sfx_gain: float = 0,
        sfx_n: int = None,
        sfx_path: str = None,
        sfx_trim: float = None,
        **kw,
    ):
        """
        Анимация + звук.

        sfx      — ключ из PROFILES
        sfx_gain — поправка к lufs_target (dB)
        sfx_n    — кол-во тиков по кривой rf за dur (только для sfx="tick")
        sfx_path — явный путь к файлу
        sfx_trim — обрезать файл до N секунд
        """
        if sfx:
            if sfx == "tick" and sfx_n:
                self._sched_ticks(dur=dur, n=sfx_n, gain_delta=sfx_gain, rf=rf)
            else:
                self._sched_sound(sfx, gain_delta=sfx_gain,
                                  path=sfx_path, trim=sfx_trim)
        self.play(*anims, run_time=dur, rate_func=rf, **kw)
        self._cur += dur

    def hold(self, dur: float = 1.0, sfx: str = None, sfx_gain: float = 0):
        """Пауза. Опционально — звук в начале паузы."""
        if sfx:
            self._sched_sound(sfx, gain_delta=sfx_gain)
        self.wait(dur)
        self._cur += dur

    def sound(self, kind: str, at: float = None, gain_delta: float = 0,
              path: str = None, trim: float = None):
        """Запланировать звук вручную. at=None → текущий курсор."""
        self._sched_sound(kind, at=at, gain_delta=gain_delta, path=path, trim=trim)

    # ── Внутренние ────────────────────────────────────────────────────────────

    def _sched_sound(self, kind, at=None, gain_delta=0, path=None, trim=None):
        cat, fi, fo, mxd, lufs_base, align, min_d = PROFILES[kind]
        base_t = self._cur if at is None else at
        p = path or sfx_pick(cat, max_dur=mxd, min_dur=min_d)
        if not p:
            return

        ss = 0.0
        if align == "peak":
            peak_t = _find_peak_time(p)
            if base_t >= peak_t:
                ev_t = base_t - peak_t   # стартуем раньше, пик попадает в base_t
                ss   = 0.0
            else:
                ev_t = 0.0               # стартуем с t=0
                ss   = peak_t - base_t  # обрезаем начало файла
        else:
            ev_t = max(0.0, base_t)
            ss   = 0.0

        self._evs.append(_Ev(ev_t, p, lufs_base + gain_delta, fi, fo, ss, trim))

    @staticmethod
    def _rf_inv(rf, v: float, steps: int = 60) -> float:
        """Числовая инверсия rate_func: найти t такое что rf(t) ≈ v."""
        lo, hi = 0.0, 1.0
        for _ in range(steps):
            mid = (lo + hi) * 0.5
            if rf(mid) < v:
                lo = mid
            else:
                hi = mid
        return (lo + hi) * 0.5

    def _sched_ticks(self, dur, n=16, at=None, gain_delta=0, rf=None):
        """Распределить n тиков по кривой rf — каждый тик разный файл, обрезан до 0.11s."""
        t0 = self._cur if at is None else at
        cat, fi, fo, mxd, lufs_base, _, min_d = PROFILES["tick"]
        lufs = lufs_base + gain_delta
        TICK_TRIM = 0.11  # жёсткая обрезка — тик = щелчок, не гудение

        # Заранее набираем пул коротких файлов
        d = _SFX / cat
        all_files = sorted([f for f in d.iterdir() if f.suffix.lower() in (".mp3", ".wav")])
        short_files = [f for f in all_files if sfx_dur(str(f)) <= mxd] if all_files else all_files
        if not short_files:
            short_files = all_files
        if not short_files:
            return

        for i in range(n):
            if rf is not None and n > 1:
                progress = i / (n - 1)
                t_frac = self._rf_inv(rf, progress)
            else:
                t_frac = i / n
            p = str(random.choice(short_files))
            self._evs.append(_Ev(t0 + t_frac * dur, p, lufs, fi, fo, trim=TICK_TRIM))

    # ── Render hook ───────────────────────────────────────────────────────────

    def render(self, preview=False):
        super().render(preview)
        if not self._evs:
            return
        try:
            fw  = self.renderer.file_writer
            src = Path(
                getattr(fw, "movie_file_path", None)
                or getattr(fw, "_movie_file_path", None)
                or getattr(fw, "final_file_path", None)
                or ""
            )
            if src.exists():
                self._remaster(src)
            else:
                print(f"[WARN] output file not found: {src}")
        except Exception as e:
            import traceback
            print(f"[WARN] remaster failed: {e}")
            traceback.print_exc()

    # ── ffmpeg микс ───────────────────────────────────────────────────────────

    def _remaster(self, video: Path):
        vid_dur = sfx_dur(str(video))
        inputs  = ["-i", str(video)]
        parts, labels = [], []

        for i, ev in enumerate(self._evs):
            sd       = sfx_dur(ev.path)
            delay_ms = int(ev.t * 1000)

            # Нормализация: gain подбирается чтобы mean_volume → lufs_target
            mean_db = _measure_db(ev.path)
            gain_db = max(-40.0, min(20.0, ev.lufs - mean_db))
            lin     = 10 ** (gain_db / 20)

            chain = f"[{i+1}:a]"

            # Обрезка начала файла (peak alignment)
            if ev.ss > 0.001:
                trim_f = f"atrim=start={ev.ss:.4f}"
                if ev.trim:
                    trim_f += f":duration={ev.trim:.4f}"
                chain += f"{trim_f},asetpts=PTS-STARTPTS,"
            elif ev.trim:
                chain += f"atrim=duration={ev.trim:.4f},asetpts=PTS-STARTPTS,"

            # Фейды ДО adelay — timestamps относительно начала файла (0),
            # иначе после adelay timestamps сдвинуты и afade st= срабатывает не там
            eff_sd = (ev.trim or sd) - ev.ss
            if eff_sd < _SHORT:
                chain += "afade=t=in:st=0:d=0.010,"
            else:
                if ev.fi > 0:
                    chain += f"afade=t=in:st=0:d={ev.fi:.3f},"
                if ev.fo > 0:
                    fo_st = max(0.0, eff_sd - ev.fo)
                    if fo_st > 0:
                        chain += f"afade=t=out:st={fo_st:.3f}:d={ev.fo:.3f},"

            # Нормализация громкости и ресэмплинг
            chain += f"volume={lin:.5f},"
            chain += "aresample=44100,aformat=channel_layouts=stereo"

            # Задержка последней — вставляет тишину в начало уже обработанного сигнала
            if delay_ms > 0:
                chain += f",adelay={delay_ms}|{delay_ms}"

            chain += f"[s{i}]"
            parts.append(chain)
            labels.append(f"[s{i}]")
            inputs += ["-i", ev.path]

        n   = len(labels)
        mix = "".join(labels) + f"amix=inputs={n}:normalize=0,volume=1.0[aout]"
        parts.append(mix)

        tmp = video.with_stem(video.stem + "_tmp")
        cmd = [
            "ffmpeg", "-y", *inputs,
            "-filter_complex", ";".join(parts),
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-t", str(vid_dur),
            str(tmp),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0:
            try:
                tmp.replace(video)
            except OSError:
                # Windows: файл открыт в другом процессе — сначала удаляем оригинал
                try:
                    video.unlink(missing_ok=True)
                    tmp.rename(video)
                except Exception as e2:
                    print(f"[WARN] rename failed: {e2}")
                    return
            print(f"[OK] remastered: {video.name}")
        else:
            print(f"[ERR] ffmpeg:\n{r.stderr[-1200:]}")
            if tmp.exists():
                tmp.unlink()
