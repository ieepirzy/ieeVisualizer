# ruff: noqa: N999 - the package name predates this module
"""Animations: a FigureSpec that changes along a timeline.

``AnimationSpec`` (renderer-neutral, JSON) = base figure + timeline (a
parameter sweep or a frame column). ``render_animation`` picks an adapter by
capability: the Matplotlib adapter (GIF always, MP4 when ffmpeg exists) or,
when ``manim`` imports, a Manim adapter for a narrow subset.
``export_animation`` stores the video, a poster PNG, the canonical spec and a
manifest. Importing this package imports neither matplotlib nor manim.
See ``docs/ANIMATION.md``.
"""

from .base import (
    ANIMATION_MEDIA_TYPES,
    AnimationAdapter,
    AnimationCapabilities,
    AnimationError,
    AnimationRegistry,
    AnimationRequest,
    AnimationResult,
    default_animation_registry,
    render_animation,
)
from .export import AnimationArtifact, export_animation
from .frames import FramePlan, FrameState, plan
from .spec import (
    ANIMATION_SCHEMA_VERSION,
    MAX_DURATION_S,
    MAX_FPS,
    MAX_FRAMES,
    AnimationSpec,
    FrameColumn,
    MarkTarget,
    ParameterSweep,
    TemplateTarget,
    animation_hash,
    from_dict,
    from_json,
    to_dict,
    to_json,
)
from .validate import validate_animation

__all__ = [
    "ANIMATION_MEDIA_TYPES",
    "ANIMATION_SCHEMA_VERSION",
    "MAX_DURATION_S",
    "MAX_FPS",
    "MAX_FRAMES",
    "AnimationAdapter",
    "AnimationArtifact",
    "AnimationCapabilities",
    "AnimationError",
    "AnimationRegistry",
    "AnimationRequest",
    "AnimationResult",
    "AnimationSpec",
    "FrameColumn",
    "FramePlan",
    "FrameState",
    "MarkTarget",
    "ParameterSweep",
    "TemplateTarget",
    "animation_hash",
    "default_animation_registry",
    "export_animation",
    "from_dict",
    "from_json",
    "plan",
    "render_animation",
    "to_dict",
    "to_json",
    "validate_animation",
]
