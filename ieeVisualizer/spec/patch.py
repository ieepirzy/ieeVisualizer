"""Edit a FigureSpec with RFC-6902-style operations instead of regenerating it.

Supported ops: ``add``, ``replace``, ``remove`` and ``test`` (assert a value,
useful for optimistic concurrency between agent and human edits). Paths are
RFC-6901 JSON pointers into the serialized form (:func:`to_dict`) - the same
pointers :func:`validate` reports. ``add`` to an array index inserts; the
index ``-`` appends; ``add`` on an existing object member replaces it, as in
RFC 6902. ``replace``/``remove`` require the target to exist.

The ops are applied atomically to a deep copy of the dict form, which is then
re-parsed and (by default) re-validated. The input spec is never mutated.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

from ._common import PatchError, SpecValidationError, ValidationIssue
from .figure import FigureSpec
from .serialize import from_dict, to_dict
from .validate import validate

OPS = ("add", "replace", "remove", "test")


def _parse_pointer(path: Any, where: str) -> list[str]:
    if not isinstance(path, str) or (path and not path.startswith("/")):
        raise _err(where, "invalid_pointer",
                   f"path must be a JSON pointer starting with '/', got {path!r}.")
    if path == "":
        return []
    return [p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/")]


def _err(where: str, code: str, message: str) -> PatchError:
    return PatchError([ValidationIssue(where, code, message)])


def _resolve(doc: Any, parts: list[str], path: str, where: str) -> tuple[Any, str]:
    """Return (container, last_key) for ``parts``."""
    cur = doc
    for depth, key in enumerate(parts[:-1]):
        cur = _child(cur, key, "/" + "/".join(parts[: depth + 1]), where)
    return cur, parts[-1]


def _child(cur: Any, key: str, at: str, where: str) -> Any:
    if isinstance(cur, dict):
        if key not in cur:
            raise _err(where, "path_not_found", f"{at} does not exist.")
        return cur[key]
    if isinstance(cur, list):
        idx = _index(cur, key, at, where, allow_end=False)
        return cur[idx]
    raise _err(where, "path_not_found", f"{at} goes through a scalar value.")


def _index(lst: list[Any], key: str, at: str, where: str, allow_end: bool) -> int:
    if key == "-" and allow_end:
        return len(lst)
    if not key.isdigit() or (len(key) > 1 and key.startswith("0")):
        raise _err(where, "invalid_pointer", f"{at}: {key!r} is not an array index.")
    idx = int(key)
    limit = len(lst) if allow_end else len(lst) - 1
    if idx > limit:
        raise _err(where, "path_not_found", f"{at}: index {idx} is out of range (len {len(lst)}).")
    return idx


def _apply_one(doc: Any, op: dict[str, Any], where: str) -> Any:
    if not isinstance(op, dict) or op.get("op") not in OPS:
        raise _err(where, "invalid_op",
                   f"each operation must be an object with op one of {', '.join(OPS)}.")
    kind = op["op"]
    if "path" not in op:
        raise _err(where, "invalid_op", f"{kind} operation needs a 'path'.")
    path = op["path"]
    parts = _parse_pointer(path, where)
    if kind in ("add", "replace", "test") and "value" not in op:
        raise _err(where, "invalid_op", f"{kind} operation needs a 'value'.")
    value = copy.deepcopy(op.get("value"))
    if not parts:  # whole document
        if kind == "remove":
            raise _err(where, "invalid_op", "cannot remove the whole spec.")
        if kind == "test":
            if doc != value:
                raise _err(where, "test_failed", "the document does not equal the test value.")
            return doc
        return value
    parent, key = _resolve(doc, parts, path, where)
    if isinstance(parent, dict):
        exists = key in parent
        if kind in ("replace", "remove", "test") and not exists:
            raise _err(where, "path_not_found", f"{path} does not exist; use 'add' to create it.")
        if kind == "remove":
            del parent[key]
        elif kind == "test":
            if parent[key] != value:
                raise _err(where, "test_failed", f"{path} is {parent[key]!r}, not {value!r}.")
        else:
            parent[key] = value
    elif isinstance(parent, list):
        idx = _index(parent, key, path, where, allow_end=(kind == "add"))
        if kind == "add":
            parent.insert(idx, value)
        elif kind == "replace":
            parent[idx] = value
        elif kind == "remove":
            del parent[idx]
        elif parent[idx] != value:
            raise _err(where, "test_failed", f"{path} is {parent[idx]!r}, not {value!r}.")
    else:
        raise _err(where, "path_not_found", f"the parent of {path} is a scalar value.")
    return doc


def apply_patch(spec: FigureSpec, ops: Sequence[dict[str, Any]], *, check: bool = True
                ) -> FigureSpec:
    """Apply ``ops`` and return a **new** FigureSpec; ``spec`` is untouched.

    Raises :class:`PatchError` if an op cannot be applied (issue path is
    ``/ops/<n>``), :class:`SpecParseError` if the result is not a FigureSpec,
    and - when ``check`` is true - :class:`SpecValidationError` if
    :func:`validate` reports any error (warnings/info do not block). Pass
    ``check=False`` to allow an intermediate invalid state.
    """
    if isinstance(ops, dict) or not isinstance(ops, (list, tuple)):
        raise _err("/ops", "invalid_op", "ops must be a list of operation objects.")
    doc = to_dict(spec)
    for n, op in enumerate(ops):
        doc = _apply_one(doc, op, f"/ops/{n}")
    new = from_dict(doc)  # SpecParseError propagates unchanged
    if check:
        errors = [i for i in validate(new) if i.severity == "error"]
        if errors:
            raise SpecValidationError(errors)
    return new
