"""Cuadros de los primeros segundos de un video, para que la IA lea el gancho (no solo la portada).

Usa ffmpeg si está instalado (brew install ffmpeg). Sin ffmpeg, o si el video no se puede bajar,
se sigue con la imagen de portada como hasta ahora.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx

from ..config import settings

# Segundos que se capturan: el arranque (gancho), el primer giro y la mitad del primer tramo
FRAME_AT = (0.5, 1.5, 3.0, 6.0)
MAX_BYTES = 25_000_000


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg"))


def video_url(media_urls: list[str] | None) -> str:
    for u in media_urls or []:
        low = u.lower()
        if low.startswith("http") and ("youtube" not in low) and (".mp4" in low or "video" in low):
            return u
    return ""


def _download(url: str, dest: Path) -> bool:
    try:
        with httpx.stream("GET", url, timeout=60, follow_redirects=True) as r:
            if r.status_code != 200:
                return False
            size = 0
            with dest.open("wb") as f:
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        return False
                    f.write(chunk)
        return size > 0
    except Exception:
        return False


def extract_frames(creative_id: int, url: str, out_dir: Path | None = None) -> list[Path]:
    """Baja el video (una vez, queda en caché) y saca un cuadro en cada segundo de FRAME_AT que exista."""
    if not url or not ffmpeg_available():
        return []
    out_dir = out_dir or settings.assets_dir / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = [out_dir / f"{creative_id}_{str(t).replace('.', '_')}.jpg" for t in FRAME_AT]
    if all(f.exists() for f in frames[:2]):
        return [f for f in frames if f.exists()]
    video = out_dir / f"{creative_id}.mp4"
    if not video.exists() and not _download(url, video):
        video.unlink(missing_ok=True)
        return []
    got = []
    for t, f in zip(FRAME_AT, frames):
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-vf", "scale=640:-2", str(f)]
        try:
            subprocess.run(cmd, check=False, timeout=60)
        except Exception:
            continue
        if f.exists() and f.stat().st_size > 0:
            got.append(f)
    video.unlink(missing_ok=True)  # los cuadros alcanzan; el video no se guarda
    return got
