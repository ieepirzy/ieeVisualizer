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
import itertools
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterable

import numpy as np


def gif_delay_ms(fps: int) -> int:
    return max(10, int(round(100.0 / fps)) * 10)


def encode_gif(frames: Iterable[np.ndarray], fps: int, loop: bool) -> bytes:
    """``frames`` are HxWx3 uint8 arrays (repeated arrays are fine), from a
    sequence or a generator. Each frame is quantized to a 1-byte-per-pixel
    palette image as it arrives, so a generator's RGB frames are not all
    held at once (Pillow keeps the palette images until it writes)."""
    from PIL import Image

    cache: dict[int, tuple[np.ndarray, Image.Image]] = {}
    images = []
    for f in frames:
        hit = cache.get(id(f))
        if hit is None or hit[0] is not f:
            hit = (f, Image.fromarray(f, "RGB").quantize(
                colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
            if isinstance(frames, (list, tuple)):  # repeated arrays: quantize once
                cache[id(f)] = hit
        images.append(hit[1])
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


def _has_x264(exe: str, timeout: float = 20) -> bool:
    try:
        out = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True,
                             text=True, timeout=max(0.001, timeout))
    except (OSError, subprocess.SubprocessError):
        return False
    return " libx264 " in out.stdout


#: How long a timed-out encode waits for the frame-writer thread after
#: killing ffmpeg. A blocked pipe write ends at once; a frame that is still
#: being drawn cannot be interrupted, so its thread is abandoned (a daemon:
#: its write then fails on the closed pipe and it ends).
STOP_JOIN_S = 2.0


def encode_mp4(frames: Iterable[np.ndarray], fps: int, exe: str, timeout: float = 300) -> bytes:
    """Frames are streamed to ffmpeg's stdin one at a time (never joined into
    one buffer); stderr goes to a file so a chatty ffmpeg cannot block.

    ``timeout`` (seconds) bounds the whole encode, from the call: the
    encoder probe, drawing the frames (a generator's frames are drawn while
    they are encoded) and writing them included. Frames are produced and
    written by a helper thread, because a write to a pipe that ffmpeg does
    not read blocks without any timeout. When the deadline passes, ffmpeg is
    killed and reaped, the helper is waited for at most :data:`STOP_JOIN_S`
    and ``encoder_timeout`` is raised, saying whether a frame was still being
    drawn, ffmpeg had stopped reading, or ffmpeg had all frames but did not
    finish."""
    from ..render.base import issue
    from .base import AnimationError

    deadline = time.monotonic() + timeout
    it = iter(frames)
    failed: list[BaseException] = []
    state = {"phase": "frame", "index": 0}
    first_box: list[np.ndarray] = []

    def remaining() -> float:
        return max(0.0, deadline - time.monotonic())

    def timed_out(writer: threading.Thread | None, phase: str, k: int) -> AnimationError:
        if phase == "frame":
            why = (f"frame {k} was still being drawn when the deadline passed: frame "
                   "generation is too slow (ffmpeg was waiting for input). ffmpeg was stopped"
                   + ("; the drawing thread was abandoned" if writer is not None
                      and writer.is_alive() else "") + ".")
        elif phase == "write":
            why = f"ffmpeg stopped reading input at frame {k} and was stopped."
        elif phase == "probe":
            why = "the time ran out while probing ffmpeg's encoders, before encoding started."
        else:
            why = "ffmpeg received every frame but did not finish encoding and was stopped."
        return AnimationError([issue("", "encoder_timeout",
                                     f"the MP4 encode did not finish within {timeout:g} s: {why}")])

    def take_first() -> None:
        try:
            first_box.append(next(it))
        except BaseException as e:  # noqa: BLE001 - re-raised in the caller
            failed.append(e)

    getter = threading.Thread(target=take_first, name="ieeVisualizer-mp4-frame0", daemon=True)
    getter.start()
    getter.join(remaining())
    if getter.is_alive():
        raise timed_out(getter, "frame", 0)
    if failed:
        raise failed[0]
    first = first_box[0]
    h, w = first.shape[:2]
    x264 = _has_x264(exe, min(20.0, remaining()))
    if not remaining():
        raise timed_out(None, "probe", 0)
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

            writer = threading.Thread(target=feed, name="ieeVisualizer-mp4-writer", daemon=True)
            try:
                writer.start()
                writer.join(remaining())
                if writer.is_alive():
                    raise subprocess.TimeoutExpired(cmd, timeout)
                code = proc.wait(timeout=remaining())
            except subprocess.TimeoutExpired:
                phase, k = state["phase"], state["index"]  # where it was at the deadline
                _stop(proc, writer)
                raise timed_out(writer, phase, k) from None
            except BaseException:
                _stop(proc, writer)
                raise
            if failed:
                raise failed[0]
            if code != 0:
                err.seek(0)
                raise AnimationError([issue("", "encoder_failed",
                                            f"ffmpeg exited with {code}: "
                                            f"{err.read().decode(errors='replace').strip()}")])
        with open(out, "rb") as fh:
            return fh.read()


def _stop(proc: subprocess.Popen, writer: threading.Thread) -> None:
    """Kill and reap ffmpeg; its closed pipe then ends a blocked write. A
    frame still being drawn is not waited for beyond :data:`STOP_JOIN_S`."""
    proc.kill()
    proc.wait()
    writer.join(STOP_JOIN_S)
