"""GIF encoding with Pillow. Frames are consumed one at a time (a generator
is never materialized) with a :class:`~ieeVisualizer.animation.deadline.Deadline`
checked between them; one palette byte per pixel is kept (a frame repeated
back to back is quantized once). Bytes are deterministic (median cut, no
dithering, no timestamp; delays in 10 ms steps).
"""

from __future__ import annotations

import io
from collections.abc import Iterable

import numpy as np

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
