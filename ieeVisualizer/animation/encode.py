"""Frame encoders: GIF with Pillow (always available: matplotlib depends on
it), MP4 through an ``ffmpeg`` executable when one is found.

GIF bytes are deterministic: every frame is quantized with the same fixed
method (median cut, no dithering) and Pillow writes no timestamp. GIF stores
delays in centiseconds, so ``1000 / fps`` ms is rounded to 10 ms and the
caller is told the delay actually stored.

MP4 is encoded with libx264 (``-threads 1``, bitexact flags, metadata
stripped) or, without libx264, MPEG-4 part 2. Frames are padded to even
sizes for yuv420p. Byte-for-byte reproducibility of MP4 is NOT promised:
it depends on the ffmpeg build.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence

import numpy as np


def gif_delay_ms(fps: int) -> int:
    return max(10, int(round(100.0 / fps)) * 10)


def encode_gif(frames: Sequence[np.ndarray], fps: int, loop: bool) -> bytes:
    """``frames`` are HxWx3 uint8 arrays (repeated arrays are fine)."""
    from PIL import Image

    cache: dict[int, Image.Image] = {}
    images = []
    for f in frames:
        key = id(f)
        if key not in cache:
            cache[key] = Image.fromarray(f, "RGB").quantize(
                colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        images.append(cache[key])
    buf = io.BytesIO()
    kw = {"loop": 0} if loop else {}
    images[0].save(buf, format="GIF", save_all=True, append_images=images[1:],
                   duration=gif_delay_ms(fps), optimize=False, disposal=1, **kw)
    return buf.getvalue()


def find_ffmpeg(path: str | None = None) -> str | None:
    """The ffmpeg executable: ``path`` if given (must exist), else
    ``$IEEVISUALIZER_FFMPEG`` or ``ffmpeg`` on PATH."""
    if path is not None:
        return shutil.which(path)
    return shutil.which(os.environ.get("IEEVISUALIZER_FFMPEG", "ffmpeg"))


def ffmpeg_version(exe: str) -> str:
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        return f"unknown ({type(e).__name__})"
    return (out.stdout.splitlines() or ["unknown"])[0]


def _has_x264(exe: str) -> bool:
    try:
        out = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True,
                             text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return " libx264 " in out.stdout


def encode_mp4(frames: Sequence[np.ndarray], fps: int, exe: str, timeout: float = 300) -> bytes:
    h, w = frames[0].shape[:2]
    codec = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-threads", "1"] \
        if _has_x264(exe) else ["-c:v", "mpeg4", "-q:v", "2"]
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out.mp4")
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps),
               "-i", "-", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", *codec,
               "-pix_fmt", "yuv420p", "-map_metadata", "-1", "-fflags", "+bitexact",
               "-flags:v", "+bitexact", "-movflags", "+faststart", out]
        raw = b"".join(np.ascontiguousarray(f).tobytes() for f in frames)
        proc = subprocess.run(cmd, input=raw, capture_output=True, timeout=timeout)
        if proc.returncode != 0:
            from ..render.base import issue
            from .base import AnimationError

            raise AnimationError([issue("", "encoder_failed",
                                        f"ffmpeg exited with {proc.returncode}: "
                                        f"{proc.stderr.decode(errors='replace').strip()}")])
        with open(out, "rb") as fh:
            return fh.read()
