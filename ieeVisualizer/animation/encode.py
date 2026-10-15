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

import contextvars
import io
import itertools
import os
import shutil
import subprocess
import tempfile
import threading
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
            deadline.check("encode")
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


def ffmpeg_version(exe: str) -> str:
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=20)
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


#: How long a stopped encode waits for the frame-writer thread after killing
#: ffmpeg. A blocked pipe write ends at once; a frame being drawn finishes
#: (the drawing checks the deadline before the next one) or, past this, is
#: abandoned (a daemon thread: its write then fails on the closed pipe).
STOP_JOIN_S = 2.0


def encode_mp4(frames: Iterable[np.ndarray], fps: int, exe: str,
               deadline: Deadline | float = 300.0) -> bytes:
    """Frames are streamed to ffmpeg's stdin one at a time (never joined into
    one buffer); stderr goes to a file so a chatty ffmpeg cannot block.

    ``deadline`` (a Deadline or seconds) bounds the whole encode: the encoder
    probe, drawing the frames (a generator's frames are drawn while they are
    encoded) and writing them. Frames are produced and written by a helper
    thread (in a copy of the caller's context), because a write to a pipe
    that ffmpeg does not read blocks without any timeout. When the deadline
    passes, ffmpeg is killed and reaped, the helper is waited for at most
    :data:`STOP_JOIN_S`, the temporary directory is removed and
    ``render_deadline_exceeded`` is raised, its phase saying whether a frame
    was still being drawn, ffmpeg had stopped reading, or ffmpeg had all
    frames but did not finish. ffmpeg failing, or exiting 0 without writing
    the file, is ``encoder_failed``."""
    deadline = Deadline.of(deadline)
    it = iter(frames)
    failed: list[BaseException] = []
    state = {"phase": "frame", "index": 0}
    first_box: list[np.ndarray] = []

    def stopped(writer: threading.Thread | None, phase: str, k: int) -> AnimationError:
        if phase == "frame":
            why = (f"encode: frame {k} was still being drawn (frame generation is too slow; "
                   "ffmpeg was waiting for input)"
                   + ("; the drawing thread was abandoned" if writer is not None
                      and writer.is_alive() else ""))
        elif phase == "write":
            why = f"encode: ffmpeg stopped reading input at frame {k}"
        elif phase == "probe":
            why = "encode: probing ffmpeg's encoders, before encoding started"
        else:
            why = "encode: ffmpeg received every frame but did not finish encoding"
        deadline.phase = why
        return deadline.exceeded()

    def take_first() -> None:
        try:
            first_box.append(next(it))
        except BaseException as e:  # noqa: BLE001 - re-raised in the caller
            failed.append(e)

    ctx = contextvars.copy_context()
    getter = threading.Thread(target=ctx.run, args=(take_first,),
                              name="ieeVisualizer-mp4-frame0", daemon=True)
    getter.start()
    getter.join(deadline.remaining())
    if getter.is_alive():
        raise stopped(getter, "frame", 0)
    if failed:
        raise failed[0]
    first = first_box[0]
    h, w = first.shape[:2]
    x264 = _has_x264(exe, min(20.0, deadline.remaining()))
    if not deadline.remaining():
        raise stopped(None, "probe", 0)
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

            def feed() -> None:
                try:
                    stream = itertools.chain([first], it)
                    for k in itertools.count():
                        state["phase"], state["index"] = "frame", k
                        f = next(stream, None)
                        if f is None:
                            break
                        data = np.ascontiguousarray(f).tobytes()
                        state["phase"] = "write"
                        try:
                            proc.stdin.write(data)
                        except (BrokenPipeError, ValueError):
                            return  # ffmpeg exited (or was killed); its exit code says why
                    state["phase"] = "done"
                except BaseException as e:  # noqa: BLE001 - a frame failed; re-raised below
                    failed.append(e)
                finally:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass

            writer = threading.Thread(target=contextvars.copy_context().run, args=(feed,),
                                      name="ieeVisualizer-mp4-writer", daemon=True)
            try:
                writer.start()
                writer.join(deadline.remaining())
                if writer.is_alive():
                    raise subprocess.TimeoutExpired(cmd, deadline.seconds)
                if failed:  # a frame failed or its deadline check fired: stop at once
                    _stop(proc, writer)
                    raise failed[0]
                code = proc.wait(timeout=deadline.remaining())
            except subprocess.TimeoutExpired:
                phase, k = state["phase"], state["index"]  # where it was at the deadline
                _stop(proc, writer)
                raise stopped(writer, phase, k) from None
            except BaseException:
                _stop(proc, writer)
                raise
            if code != 0 or not os.path.isfile(out) or not os.path.getsize(out):
                err.seek(0)
                detail = err.read().decode(errors="replace").strip()
                why = f"exited with {code}" if code != 0 else "exited 0 without writing the file"
                raise AnimationError([issue("", "encoder_failed",
                                            f"ffmpeg {why}{': ' + detail if detail else ''}.")])
        with open(out, "rb") as fh:
            return fh.read()


def _stop(proc: subprocess.Popen, writer: threading.Thread) -> None:
    """Kill and reap ffmpeg; its closed pipe then ends a blocked write. A
    frame still being drawn is not waited for beyond :data:`STOP_JOIN_S`."""
    if proc.poll() is None:
        proc.kill()
    proc.wait()
    writer.join(STOP_JOIN_S)
