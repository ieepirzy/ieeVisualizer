"""Adapter registry and capability-based selection.

:func:`requirements_for` derives what a spec + request *actually* need
(coordinate system, mark types, annotation types, output format, 3D, vector
export, number of points). :func:`select_adapter` returns the first registered
adapter that meets all of them, or raises :class:`CapabilityError` listing the
requirements and, per adapter, the ones it does not meet. Nothing is
downgraded silently: an adapter that cannot draw a mark is not chosen.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from ..spec import DataSnapshot, FigureSpec, FunctionMark
from .base import (
    VECTOR_FORMATS,
    Capabilities,
    CapabilityError,
    RendererAdapter,
    RenderRequest,
    RenderResult,
    Resolver,
    issue,
)


@dataclass(frozen=True)
class Requirements:
    """What rendering one spec into one format needs from an adapter."""

    coordinate_system: str
    mark_types: frozenset[str]
    annotation_types: frozenset[str]
    output_format: str
    three_d: bool
    vector_export: bool
    #: Data points known before resolution (inline rows + function samples).
    points: int
    #: Datasets whose size is unknown until resolved (references).
    unknown_size: tuple[str, ...] = field(default_factory=tuple)

    def describe(self) -> list[str]:
        out = [
            f"coordinate system {self.coordinate_system!r}",
            "mark types " + ", ".join(sorted(self.mark_types)) if self.mark_types else
            "no marks",
            f"output format {self.output_format!r}",
        ]
        if self.annotation_types:
            out.append("annotation types " + ", ".join(sorted(self.annotation_types)))
        if self.three_d:
            out.append("3D rendering")
        if self.vector_export:
            out.append("vector export")
        out.append(f"{self.points} data points"
                   + (f" (+ unresolved: {', '.join(self.unknown_size)})"
                      if self.unknown_size else ""))
        return out

    def unmet(self, caps: Capabilities) -> list[str]:
        """Human-readable list of requirements ``caps`` does not satisfy."""
        out = []
        if self.coordinate_system not in caps.coordinate_systems:
            out.append(f"coordinate system {self.coordinate_system!r} (supports "
                       f"{', '.join(sorted(caps.coordinate_systems))})")
        missing = sorted(self.mark_types - caps.mark_types)
        if missing:
            out.append(f"mark types {', '.join(missing)} (supports "
                       f"{', '.join(sorted(caps.mark_types))})")
        blocked = sorted(self.mark_types & caps.unsupported.get(self.coordinate_system,
                                                                frozenset()))
        if blocked:
            out.append(f"mark types {', '.join(blocked)} in {self.coordinate_system}")
        missing_ann = sorted(self.annotation_types - caps.annotation_types)
        if missing_ann:
            out.append(f"annotation types {', '.join(missing_ann)}")
        if self.output_format not in caps.output_formats:
            out.append(f"output format {self.output_format!r} (supports "
                       f"{', '.join(sorted(caps.output_formats))})")
        if self.three_d and not caps.three_d:
            out.append("3D rendering")
        if self.vector_export and not caps.vector_export:
            out.append("vector export")
        if caps.max_points is not None and self.points > caps.max_points:
            out.append(f"{self.points} data points (max {caps.max_points} without "
                       "downsampling)")
        return out


def requirements_for(spec: FigureSpec, request: RenderRequest) -> Requirements:
    points = 0
    unknown: list[str] = []
    counted: set[str] = set()
    for m in spec.layers:
        if isinstance(m, FunctionMark) and m.expression is not None:
            points += m.samples ** (2 if spec.coordinate_system == "cartesian3d" else 1)
            continue
        if m.data is None or m.data in counted:
            continue
        counted.add(m.data)
        ds = spec.datasets.get(m.data)
        if isinstance(ds, DataSnapshot):
            points += max(ds.lengths().values(), default=0)
        elif ds is not None:
            unknown.append(m.data)
    return Requirements(
        coordinate_system=spec.coordinate_system,
        mark_types=frozenset(m.type for m in spec.layers),
        annotation_types=frozenset(a.type for a in spec.annotations),
        output_format=request.format,
        three_d=spec.coordinate_system == "cartesian3d",
        vector_export=request.format in VECTOR_FORMATS,
        points=points,
        unknown_size=tuple(unknown),
    )


class AdapterRegistry:
    """Ordered name -> adapter map. Earlier registrations win ties."""

    def __init__(self) -> None:
        self._adapters: dict[str, RendererAdapter] = {}

    def register(self, adapter: RendererAdapter, *, replace: bool = False) -> RendererAdapter:
        if not isinstance(adapter, RendererAdapter):
            raise TypeError(f"{adapter!r} does not implement the RendererAdapter protocol.")
        if adapter.name in self._adapters and not replace:
            raise ValueError(f"an adapter named {adapter.name!r} is already registered; pass "
                             "replace=True to swap it.")
        self._adapters[adapter.name] = adapter
        return adapter

    def unregister(self, name: str) -> None:
        self._adapters.pop(name, None)

    def get(self, name: str) -> RendererAdapter:
        try:
            return self._adapters[name]
        except KeyError:
            raise KeyError(f"no renderer adapter named {name!r}; registered: "
                           f"{', '.join(self._adapters) or '(none)'}.") from None

    def names(self) -> list[str]:
        return list(self._adapters)

    def __iter__(self) -> Iterator[RendererAdapter]:
        return iter(list(self._adapters.values()))

    def __len__(self) -> int:
        return len(self._adapters)

    def select(self, spec: FigureSpec, request: RenderRequest) -> RendererAdapter:
        reqs = requirements_for(spec, request)
        if request.adapter is not None:
            candidates = [self.get(request.adapter)]
        else:
            candidates = list(self)
        unmet: dict[str, list[str]] = {}
        for a in candidates:
            missing = reqs.unmet(a.capabilities)
            if not missing:
                return a
            unmet[a.name] = missing
        needed = reqs.describe()
        if not candidates:
            detail = "no renderer adapters are registered"
        else:
            detail = "; ".join(f"{n} cannot provide {', '.join(m)}" for n, m in unmet.items())
        raise CapabilityError(
            [issue("", "no_capable_adapter",
                   f"no renderer can draw this spec as {request.format}: {detail}. The spec "
                   f"needs: {'; '.join(needed)}.")],
            needed, unmet,
        )


def _make_default() -> AdapterRegistry:
    from .mpl import MatplotlibAdapter  # imports matplotlib lazily, at render time

    reg = AdapterRegistry()
    reg.register(MatplotlibAdapter())
    return reg


default_registry: AdapterRegistry = _make_default()


def select_adapter(
    spec: FigureSpec, request: RenderRequest, registry: AdapterRegistry | None = None
) -> RendererAdapter:
    """Pick an adapter able to render ``spec`` as ``request`` asks."""
    return (registry or default_registry).select(spec, request)


def render(
    spec: FigureSpec,
    request: RenderRequest | None = None,
    *,
    resolver: Resolver | None = None,
    registry: AdapterRegistry | None = None,
) -> RenderResult:
    """Validate, resolve, select an adapter by capability and render.

    References are resolved before selection so the point count used for the
    capability check includes resolved rows.
    """
    from .prepare import prepare

    request = request or RenderRequest()
    prepared = prepare(spec, resolver)
    adapter = select_adapter(prepared.spec, request, registry)
    return adapter.render(prepared.spec, request)
