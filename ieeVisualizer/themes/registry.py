"""ThemeRegistry: themes by name and version, checked on the way in.

``register`` refuses a theme with a structural error or a failing
accessibility check (:class:`ThemeError` with every issue); warnings and
info findings are kept and reported by :meth:`ThemeRegistry.describe`. A
name+version pair is registered once: publish a new version to change a
theme, so a stored figure that names ``name@version`` keeps its meaning.

A theme reference is ``"name"`` (the highest version) or
``"name@version"`` (exactly that one).
"""

from __future__ import annotations

from typing import Any

from ..spec import ValidationIssue
from ..spec._common import suggest
from .accessibility import check_theme, report
from .theme import Theme, ThemeError, structure_issues


def _vkey(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


class ThemeRegistry:
    def __init__(self) -> None:
        self._themes: dict[str, dict[str, Theme]] = {}
        self._findings: dict[str, list[ValidationIssue]] = {}

    def register(self, theme: Theme) -> Theme:
        if not isinstance(theme, Theme):
            raise TypeError(f"expected a Theme, got {type(theme).__name__}")
        issues = structure_issues(theme)
        if not issues:
            issues = check_theme(theme)
        if any(i.severity == "error" for i in issues):
            raise ThemeError(issues)
        versions = self._themes.setdefault(theme.name, {})
        if theme.version in versions:
            raise ThemeError([ValidationIssue(
                "/version", "duplicate_theme",
                f"theme {theme.ref!r} is already registered; publish a new version instead of "
                "replacing one that stored figures may name.")])
        versions[theme.version] = theme
        self._findings[theme.ref] = issues
        return theme

    def resolve(self, ref: str) -> Theme:
        """``"name"`` -> latest version; ``"name@version"`` -> that version."""
        if not isinstance(ref, str) or not ref:
            raise ThemeError([ValidationIssue("/theme", "unknown_theme",
                                              f"{ref!r} is not a theme reference.")])
        name, _, version = ref.partition("@")
        versions = self._themes.get(name)
        if not versions:
            raise ThemeError([ValidationIssue(
                "/theme", "unknown_theme",
                f"no theme named {name!r}.{suggest(name, self._themes)} Known: "
                f"{', '.join(sorted(self._themes))}.")])
        if not version:
            return versions[max(versions, key=_vkey)]
        if version not in versions:
            raise ThemeError([ValidationIssue(
                "/theme", "unknown_theme_version",
                f"theme {name!r} has no version {version!r}; available: "
                f"{', '.join(sorted(versions, key=_vkey))}.")])
        return versions[version]

    get = resolve

    def __contains__(self, ref: object) -> bool:
        try:
            self.resolve(ref)  # type: ignore[arg-type]
        except ThemeError:
            return False
        return True

    def names(self) -> list[str]:
        return sorted(self._themes)

    def list(self) -> list[dict[str, Any]]:
        out = []
        for name in self.names():
            t = self.resolve(name)
            out.append({"name": name, "version": t.version,
                        "versions": sorted(self._themes[name], key=_vkey), "title": t.title,
                        "description": t.description, "pending": list(t.pending)})
        return out

    def findings(self, ref: str) -> list[ValidationIssue]:
        """Warnings/info recorded when ``ref`` was registered."""
        return list(self._findings.get(self.resolve(ref).ref, []))

    def describe(self, ref: str) -> dict[str, Any]:
        t = self.resolve(ref)
        return {"theme": t.to_dict(), "sha256": t.sha256, "accessibility": report(t)}
