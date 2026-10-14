# ruff: noqa: N999 - the package name predates the lint config
"""ieeVisualizer: plotting helpers and a renderer-independent figure spec.

``Visualizer`` is resolved lazily (PEP 562) so that ``import ieeVisualizer.spec``
does not pull in matplotlib/sympy; ``from ieeVisualizer import Visualizer`` and
``from ieeVisualizer.core import Visualizer`` both keep working.
"""

__all__ = ["Visualizer", "__version__"]

#: Keep in sync with ``version`` in pyproject.toml (recorded in render manifests).
__version__ = "0.1.0"


def __getattr__(name):
    if name == "Visualizer":
        from .core import Visualizer

        return Visualizer
    raise AttributeError(f"module 'ieeVisualizer' has no attribute {name!r}")
