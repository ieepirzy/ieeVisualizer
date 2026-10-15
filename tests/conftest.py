import matplotlib

matplotlib.use("Agg")  # headless; must run before pyplot is imported anywhere

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _close_figures():
    yield
    plt.close("all")


def surface_vertices(fig):
    """All (x, y, z) vertices of the single plot_surface collection in ``fig``.

    Poly3DCollection keeps its 3D faces in private attributes whose layout
    differs between Matplotlib versions; this reads either layout.
    """
    (ax,) = fig.axes
    (coll,) = ax.collections
    if hasattr(coll, "_faces"):  # Matplotlib >= 3.10
        faces = np.ma.getdata(coll._faces)
        return faces.reshape(-1, 3)
    vec = coll._vec  # Matplotlib <= 3.9: homogeneous (4, n) coordinates
    return (vec[:3] / vec[3]).T
