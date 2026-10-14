"""Shared pre-render step for every adapter: validate, then resolve references.

Order matters:

1. :func:`~ieeVisualizer.spec.validate` the spec as given. Any error refuses
   the render with :class:`~ieeVisualizer.spec.SpecValidationError` carrying
   the issues; nothing is drawn from a spec the validator rejects.
2. Every :class:`DatasetRef` a layer is bound to is resolved through the
   caller's ``resolver``. Without one the render fails with
   :class:`UnresolvedDatasetError`; a reference is never drawn as empty.
   The resolved snapshot must agree with any columns the reference declared
   (same dtype and unit) so a spec checked against a declared schema cannot
   silently be drawn from different data.
3. The resolved spec is validated again (the validator could not see the
   referenced values before), and its warnings become render warnings.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from ..spec import (
    Column,
    DatasetRef,
    DataSnapshot,
    FigureSpec,
    SpecValidationError,
    validate,
)
from ..spec._common import ptr
from .base import RenderWarning, Resolver, UnresolvedDatasetError, issue


@dataclass
class PreparedSpec:
    """A validated spec whose layer data is all inline."""

    spec: FigureSpec
    warnings: list[RenderWarning] = field(default_factory=list)
    #: Names of datasets that were references and are now resolved snapshots.
    resolved: dict[str, DataSnapshot] = field(default_factory=dict)


def _errors(spec: FigureSpec):
    issues = validate(spec)
    return issues, [i for i in issues if i.severity == "error"]


def bound_refs(spec: FigureSpec) -> dict[str, DatasetRef]:
    """References that at least one layer draws from, in layer order."""
    out: dict[str, DatasetRef] = {}
    for m in spec.layers:
        ds = spec.datasets.get(m.data) if m.data is not None else None
        if isinstance(ds, DatasetRef):
            out.setdefault(m.data, ds)
    return out


def prepare(spec: FigureSpec, resolver: Resolver | None = None) -> PreparedSpec:
    """Validate ``spec`` and resolve the references its layers use."""
    first_issues, errors = _errors(spec)
    if errors:
        raise SpecValidationError(errors)

    refs = bound_refs(spec)
    if refs and resolver is None:
        raise UnresolvedDatasetError([
            issue(ptr("datasets", name), "unresolved_dataset",
                  f"dataset {name!r} is a reference ({ref.kind} {ref.id!r}) and no resolver was "
                  "given; pass resolver=callable(DatasetRef) -> DataSnapshot, or inline the "
                  "data as a snapshot.")
            for name, ref in refs.items()
        ])

    resolved: dict[str, DataSnapshot] = {}
    for name, ref in refs.items():
        resolved[name] = _resolve_one(name, ref, resolver)

    if not resolved:
        return PreparedSpec(spec=spec, warnings=_as_warnings(first_issues))

    datasets = dict(spec.datasets)
    datasets.update(resolved)
    out = dataclasses.replace(spec, datasets=datasets)
    issues, errors = _errors(out)
    if errors:
        raise SpecValidationError(errors)
    return PreparedSpec(spec=out, warnings=_as_warnings(issues), resolved=resolved)


def _as_warnings(issues) -> list[RenderWarning]:
    return [RenderWarning(i.code, i.message, i.path) for i in issues if i.severity == "warning"]


def _resolve_one(name: str, ref: DatasetRef, resolver: Resolver) -> DataSnapshot:
    p = ptr("datasets", name)
    try:
        snap = resolver(ref)
    except Exception as e:  # noqa: BLE001 - any resolver failure is reported, chained
        raise UnresolvedDatasetError([
            issue(p, "resolver_failed",
                  f"resolving dataset {name!r} ({ref.kind} {ref.id!r}) failed: "
                  f"{type(e).__name__}: {e}")
        ]) from e
    if not isinstance(snap, DataSnapshot):
        raise UnresolvedDatasetError([
            issue(p, "resolver_failed",
                  f"the resolver returned {type(snap).__name__} for dataset {name!r}; it must "
                  "return a DataSnapshot.")
        ])
    if not ref.columns:
        return snap

    problems = []
    columns = dict(snap.columns)
    for col, declared in ref.columns.items():
        cp = p + "/columns/" + ptr(col)[1:]
        got = columns.get(col)
        if got is None:
            problems.append(issue(cp, "resolved_schema_mismatch",
                                  f"dataset {name!r} declares column {col!r} but the resolved "
                                  f"data has none (columns: {', '.join(sorted(columns))})."))
            continue
        for attr in ("dtype", "unit"):
            want, have = getattr(declared, attr), getattr(got, attr)
            if want is not None and have is not None and want != have:
                problems.append(issue(cp + "/" + attr, "resolved_schema_mismatch",
                                      f"column {col!r} of {name!r} is declared with {attr} "
                                      f"{want!r} but the resolved data has {have!r}; units and "
                                      "types are never converted implicitly."))
        if declared.unit is not None and got.unit is None:
            # The reference declared the unit; the resolved column carries none.
            # Adopt the declared meaning rather than draw an unlabelled axis.
            columns[col] = Column(values=list(got.values), dtype=got.dtype, unit=declared.unit,
                                  description=got.description or declared.description,
                                  denominator=got.denominator or declared.denominator,
                                  aggregation=got.aggregation or declared.aggregation)
    if problems:
        raise UnresolvedDatasetError(problems)
    return DataSnapshot(columns=columns, provenance=snap.provenance)
