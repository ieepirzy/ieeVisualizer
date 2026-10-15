"""One validation and one template build per state within a call chain.

``render_animation``, ``export_animation`` and the adapters' ``render`` /
``plan`` each validate the spec and build its states, and they call one
another: an export renders every format and plans the poster, so the same
spec was validated three to five times and every template state was built
once per format plus once for the poster. Inside :func:`call_scope` (entered by
each of those entry points; nested entries share the outermost scope),
:func:`validate_animation` runs once per ``animation_hash`` and each template
state is built once. Outside any scope nothing is cached, so a direct call
always sees the spec as it is now.
"""

from __future__ import annotations

import contextlib
import functools
from contextvars import ContextVar
from typing import Any, Callable

_SCOPE: ContextVar[dict[str, dict[Any, Any]] | None] = ContextVar("ieev_anim_scope",
                                                                  default=None)


@contextlib.contextmanager
def call_scope():
    if _SCOPE.get() is not None:
        yield
        return
    token = _SCOPE.set({"validate": {}, "build": {}})
    try:
        yield
    finally:
        _SCOPE.reset(token)


def scoped(fn: Callable) -> Callable:
    """Run ``fn`` inside :func:`call_scope`."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with call_scope():
            return fn(*args, **kwargs)

    return wrapper


def cache(kind: str) -> dict[Any, Any] | None:
    """The current scope's cache for ``kind`` (None outside a scope)."""
    scope = _SCOPE.get()
    return None if scope is None else scope[kind]


def anim_key(anim: Any) -> str | None:
    """``animation_hash(anim)``, or None when the spec cannot be hashed
    (then nothing is cached for it)."""
    from .spec import animation_hash

    try:
        return animation_hash(anim)
    except Exception:  # noqa: BLE001 - an unserializable spec is validated every time
        return None
