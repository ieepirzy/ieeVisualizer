"""JSON round-trip for FigureSpec.

Canonical form rules (so equal specs serialize to identical bytes):

* keys sorted, compact separators, no NaN/Infinity (:func:`to_json` with
  ``indent=None``);
* optional fields whose value is ``None`` are omitted; every other field,
  including defaults such as ``role: "observed"``, is written explicitly, so a
  stored spec keeps its meaning even if a library default changes later;
* encoding shorthand (``"y": "col"``) is accepted on input and normalised to
  ``{"field": "col"}``.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from ._common import canonical_json, fail, sha256_of
from .figure import FigureSpec, figure_from_dict, figure_to_dict


def to_dict(spec: FigureSpec) -> dict[str, Any]:
    """Plain-JSON dict form of ``spec``. Deep-copied, so mutating the result
    (e.g. while patching) can never reach back into ``spec``."""
    return copy.deepcopy(figure_to_dict(spec))


def from_dict(d: Any) -> FigureSpec:
    """Parse a dict into a FigureSpec. Raises :class:`SpecParseError` on
    structural problems (first one found). Semantic checks are done by
    :func:`validate`, not here."""
    return figure_from_dict(d)


def to_json(spec: FigureSpec, indent: int | None = None) -> str:
    """Canonical JSON. ``indent`` is for humans; only ``indent=None`` is the
    hashing form."""
    return canonical_json(to_dict(spec), indent=indent)


def from_json(s: str) -> FigureSpec:
    try:
        d = json.loads(s)
    except json.JSONDecodeError as e:
        raise fail("", "invalid_json", f"not valid JSON: {e}") from None
    return from_dict(d)


def spec_hash(spec: FigureSpec) -> str:
    """``sha256:<hex>`` of the canonical JSON of the whole spec (datasets
    included, so the hash pins both the figure and its inline data)."""
    return sha256_of(to_dict(spec))
