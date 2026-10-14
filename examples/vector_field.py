"""Plot 2D and 3D vector fields.

Run: python examples/vector_field.py  (writes PNGs to examples/out/)
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import sympy as sp  # noqa: E402

from ieeVisualizer import Visualizer  # noqa: E402

OUT = Path(__file__).parent / "out"


def main():
    OUT.mkdir(exist_ok=True)
    viz = Visualizer(colormap="plasma")

    x, y, z = sp.symbols("x y z")
    # Van der Pol oscillator phase field, mu = 1
    fig = viz.visualize_vf([y, (1 - x**2) * y - x], dim=2, domain=(-3, 3), streamplot=True)
    fig.axes[0].set_title("Van der Pol phase field")
    fig.savefig(OUT / "vector_field_2d.png", dpi=120)

    fig = viz.visualize_vf(lambda x, y, z: (-y, x, 0.3 * z), dim=3, domain=(-2, 2), n=6)
    fig.axes[0].set_title("rotating, expanding 3D field")
    fig.savefig(OUT / "vector_field_3d.png", dpi=120)

    print(f"wrote {OUT / 'vector_field_2d.png'} and {OUT / 'vector_field_3d.png'}")


if __name__ == "__main__":
    main()
