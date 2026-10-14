"""Plot a 1-D function and a surface, from a callable and from SymPy.

Run: python examples/function_plot.py  (writes PNGs to examples/out/)
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import numpy as np  # noqa: E402
import sympy as sp  # noqa: E402

from ieeVisualizer import Visualizer  # noqa: E402

OUT = Path(__file__).parent / "out"


def main():
    OUT.mkdir(exist_ok=True)
    viz = Visualizer(N=300, colormap="viridis")

    fig = viz.visualize(lambda x: np.sin(x) * np.exp(-0.1 * x**2), dim=2, domain=(-8, 8))
    fig.axes[0].set_title("damped sine (plain callable)")
    fig.savefig(OUT / "function_2d.png", dpi=120)

    x, y = sp.symbols("x y")
    fig = viz.visualize(sp.sin(sp.sqrt(x**2 + y**2)), dim=3, n=80, domain=(-6, 6))
    fig.axes[0].set_title(r"$\sin\sqrt{x^2+y^2}$ (SymPy)")
    fig.savefig(OUT / "function_3d.png", dpi=120)

    print(f"wrote {OUT / 'function_2d.png'} and {OUT / 'function_3d.png'}")


if __name__ == "__main__":
    main()
