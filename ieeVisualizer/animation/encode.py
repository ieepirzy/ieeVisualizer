"""Frame encoders: GIF with Pillow, MP4 through an ``ffmpeg`` executable.

Both consume frames one at a time (a generator is never materialized) and
check a :class:`~ieeVisualizer.animation.deadline.Deadline` between frames: the
GIF encoder keeps one palette byte per pixel (a frame repeated back to back
is quantized once), the MP4 encoder keeps nothing. GIF bytes are
deterministic (median cut, no dithering, no timestamp; delays in 10 ms
steps). MP4: libx264 (``-threads 1``, bitexact) or MPEG-4 part 2, padded to
even sizes for yuv420p; byte reproducibility depends on the ffmpeg build.
"""

from __future__ import annotations

import io
import itertools
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterable

import numpy as np

from ..render.base import issue
from .base import AnimationError
from .deadline import Deadline

#: Pillow's final GIF write, per pixel of every frame: at most 2.4e-8 s
#: measured (2.85 s for 120 M px of noise; about 1 s for chart-like frames).
#: The write cannot be interrupted, so it is not started without this much
#: time left.
GIF_WRITE_S_PER_PIXEL = 2.5e-8


def gif_delay_ms(fps: int) -> int:
    return max(10, int(round(100.0 / fps)) * 10)


def gif_frames(frames: Iterable[np.ndarray], deadline: Deadline | None = None) -> list:
    """Quantize ``frames`` (HxWx3 uint8) to palette images as they arrive; a
    frame object repeated back to back is quantized once and shared."""
    from PIL import Image

    images, last, last_img = [], None, None
    for f in frames:
        if deadline is not None:
            deadline.check()  # keeps the running phase (drawing feeds this)
        if f is not last:
            last, last_img = f, Image.fromarray(f, "RGB").quantize(
                colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        images.append(last_img)
        if deadline is not None:
            deadline.count("frames_encoded")
    return images


def write_gif(images: list, fps: int, loop: bool, deadline: Deadline | None = None) -> bytes:
    if deadline is not None and images:
        need = GIF_WRITE_S_PER_PIXEL * images[0].width * images[0].height * len(images)
        if deadline.remaining() < need:
            deadline.phase = f"encode: the GIF write needs about {need:.1f} s"
            raise deadline.exceeded()
    buf = io.BytesIO()
    kw = {"loop": 0} if loop else {}
    images[0].save(buf, format="GIF", save_all=True, append_images=images[1:],
                   duration=gif_delay_ms(fps), optimize=False, disposal=1, **kw)
    return buf.getvalue()


def encode_gif(frames: Iterable[np.ndarray], fps: int, loop: bool,
               deadline: Deadline | None = None) -> bytes:
    """GIF bytes of ``frames`` (a sequence or a generator)."""
    return write_gif(gif_frames(frames, deadline), fps, loop, deadline)


def find_ffmpeg(path: str | None = None) -> str | None:
    """The ffmpeg executable: ``path`` if given (must exist), else
    ``$IEEVISUALIZER_FFMPEG`` or ``ffmpeg`` on PATH."""
    if path is not None:
        return shutil.which(path)
    return shutil.which(os.environ.get("IEEVISUALIZER_FFMPEG", "ffmpeg"))


def ffmpeg_version(exe: str, timeout: float = 20) -> str:
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True,
                             timeout=max(timeout, 1e-3))
    except (OSError, subprocess.SubprocessError) as e:
        return f"unknown ({type(e).__name__})"
    return (out.stdout.splitlines() or ["unknown"])[0]


def _has_x264(exe: str, timeout: float = 20) -> bool:
    try:
        out = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True,
                             text=True, timeout=max(0.001, timeout))
    except (OSError, subprocess.SubprocessError):
        return False
    return " libx264 " in out.stdout


def encode_mp4(frames: Iterable[np.ndarray], fps: int, exe: str,
               deadline: Deadline | float = 300.0) -> bytes:
    """Frames are streamed to ffmpeg's stdin one at a time (never joined into
    one buffer); stderr goes to a file so a chatty ffmpeg cannot block.
    ``deadline`` (a Deadline or seconds) is checked before every frame (the
    frames' own generator checks it while drawing) and bounds the encoder
    probe and ffmpeg's finish; on expiry ffmpeg is killed and reaped, the
    temporary directory removed and ``render_deadline_exceeded`` raised. A
    write ffmpeg never reads would block: in a worker process
    (``isolation="process"``) the kill bounds that, inline it is best effort.
    ffmpeg failing, or exiting 0 without writing the file, is
    ``encoder_failed``."""
    deadline = Deadline.of(deadline)
    it = iter(frames)
    first = next(it)
    h, w = first.shape[:2]
    x264 = _has_x264(exe, min(20.0, deadline.remaining()))
    deadline.check()
    codec = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-threads", "1"] \
        if x264 else ["-c:v", "mpeg4", "-q:v", "2"]
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out.mp4")
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps),
               "-i", "-", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", *codec,
               "-pix_fmt", "yuv420p", "-map_metadata", "-1", "-fflags", "+bitexact",
               "-flags:v", "+bitexact", "-movflags", "+faststart", out]
        with open(os.path.join(tmp, "stderr.txt"), "w+b") as err:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                    stderr=err)
            try:
                for f in itertools.chain([first], it):
                    deadline.check()
                    try:
                        proc.stdin.write(np.ascontiguousarray(f).tobytes())
                        deadline.count("frames_encoded")
                    except BrokenPipeError:
                        break  # ffmpeg exited; its exit code says why
                try:
                    proc.stdin.close()
                except BrokenPipeError:
                    pass
                deadline.phase = "encode: ffmpeg finishing"
                code = proc.wait(timeout=max(deadline.remaining(), 1e-3))
            except subprocess.TimeoutExpired:
                _stop(proc)
                raise deadline.exceeded() from None
            except BaseException:
                _stop(proc)
                raise
            if code != 0 or not os.path.isfile(out) or not os.path.getsize(out):
                err.seek(0)
                detail = err.read().decode(errors="replace").strip()
                why = f"exited with {code}" if code != 0 else "exited 0 without writing the file"
                raise AnimationError([issue("", "encoder_failed",
                                            f"ffmpeg {why}{': ' + detail if detail else ''}.")])
        with open(out, "rb") as fh:
            return fh.read()


def _stop(proc: subprocess.Popen) -> None:
    """Kill and reap ffmpeg."""
    if proc.poll() is None:
        proc.kill()
    proc.wait()
