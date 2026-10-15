"""TemplateRegistry: versioned lookup and the agent-facing catalogue.

Templates are keyed by ``(name, version)``; ``version`` is a semantic version
string ``MAJOR.MINOR.PATCH[-prerelease][+build]``. A name+version pair can be
registered once: re-registering it is refused (a published template version
must never change meaning under a stored spec that names it).

``get(name)`` without a version returns the *latest* version by semver
precedence, preferring releases: a pre-release (``1.1.0-rc.1``) is returned
only when the name has no release version at all. Build metadata (``+...``)
is ignored for ordering, as semver specifies; two versions differing only in
build metadata are therefore duplicates.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..spec import FigureSpec, ValidationIssue
from ..spec._common import suggest
from .base import ComponentTemplate, TemplateRegistryError

_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def parse_semver(version: str) -> tuple[Any, ...]:
    """Return a sort key implementing semver 2.0.0 precedence. Raises
    :class:`TemplateRegistryError` for a malformed version."""
    m = _SEMVER.match(version) if isinstance(version, str) else None
    if m is None:
        raise TemplateRegistryError([ValidationIssue(
            "/version", "invalid_version",
            f"version {version!r} is not a semantic version; use MAJOR.MINOR.PATCH, "
            "e.g. '1.0.0'.")])
    major, minor, patch, pre = int(m[1]), int(m[2]), int(m[3]), m[4]
    if pre is None:
        return (major, minor, patch, 1, ())  # a release sorts after its pre-releases
    ids = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return (major, minor, patch, 0, ids)


def is_prerelease(version: str) -> bool:
    return parse_semver(version)[3] == 0


class TemplateRegistry:
    """A set of templates addressable by name and version."""

    def __init__(self) -> None:
        self._by_name: dict[str, dict[tuple[Any, ...], ComponentTemplate]] = {}

    def register(self, template: ComponentTemplate) -> ComponentTemplate:
        """Add ``template``. Refuses a duplicate name+version. Returns the
        template so it can be used as ``registry.register(MyTemplate())``."""
        if not isinstance(template, ComponentTemplate):
            raise TypeError(f"expected a ComponentTemplate, got {type(template).__name__}")
        if not template.name:
            raise TemplateRegistryError([ValidationIssue(
                "/name", "invalid_name", "a template needs a non-empty name.")])
        key = parse_semver(template.version)
        versions = self._by_name.setdefault(template.name, {})
        if key in versions:
            raise TemplateRegistryError([ValidationIssue(
                "/version", "duplicate_template",
                f"template {template.name!r} version {template.version!r} is already "
                f"registered (as {versions[key].version!r}); publish a new version instead of "
                "replacing one that stored specs may refer to.")])
        versions[key] = template
        return template

    def versions(self, name: str) -> list[str]:
        """All registered versions of ``name``, oldest first."""
        entries = self._entries(name)
        return [entries[k].version for k in sorted(entries)]

    def get(self, name: str, version: str | None = None) -> ComponentTemplate:
        """The template ``name`` at ``version``, or the latest version."""
        entries = self._entries(name)
        if version is None:
            releases = [k for k in entries if k[3] == 1]
            return entries[max(releases or entries)]
        key = parse_semver(version)
        if key not in entries:
            raise TemplateRegistryError([ValidationIssue(
                "/version", "unknown_template_version",
                f"template {name!r} has no version {version!r}; available: "
                f"{', '.join(self.versions(name))}.")])
        return entries[key]

    def list(self) -> list[dict[str, Any]]:
        """One JSON-serializable summary per template name, sorted by name:
        ``{"name", "version" (latest), "versions", "title", "description"}``."""
        out = []
        for name in sorted(self._by_name):
            t = self.get(name)
            out.append({
                "name": name,
                "version": t.version,
                "versions": self.versions(name),
                "title": t.title,
                "description": t.description,
            })
        return out

    def describe(self, name: str, version: str | None = None) -> dict[str, Any]:
        """Full catalogue entry (schema, parameters, example payload)."""
        entry = self.get(name, version).describe()
        entry["versions"] = self.versions(name)
        return entry

    def catalog(self) -> dict[str, Any]:
        """Catalogue of the latest version of every template."""
        return {"templates": [self.describe(n) for n in sorted(self._by_name)]}

    def build(self, name: str, data: Any = None, params: Mapping[str, Any] | None = None,
              version: str | None = None) -> FigureSpec:
        """``get(name, version).build(data, **params)``."""
        return self.get(name, version).build(data, **dict(params or {}))

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def _entries(self, name: str) -> dict[tuple[Any, ...], ComponentTemplate]:
        entries = self._by_name.get(name)
        if not entries:
            raise TemplateRegistryError([ValidationIssue(
                "/name", "unknown_template",
                f"no template named {name!r}.{suggest(str(name), self._by_name)} Available: "
                f"{', '.join(sorted(self._by_name)) or '(none)'}.")])
        return entries
