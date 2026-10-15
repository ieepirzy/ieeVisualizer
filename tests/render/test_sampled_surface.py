"""A sampled ``function`` mark in cartesian3d is z = f(x, y): a surface over
its grid, like the symbolic form, never a polyline through the grid rows."""

import numpy as np
import pytest

from ieeVisualizer.render import RenderError, build_figure
from ieeVisualizer.spec import DataSnapshot, FigureSpec, FunctionMark


def _spec(xs, ys, zs, **enc):
    data = DataSnapshot.from_columns({"a": xs, "b": ys, "c": zs, "s": ["p"] * len(xs)})
    return FigureSpec(coordinate_system="cartesian3d", datasets={"d": data},
                      layers=[FunctionMark(data="d", encoding={"x": "a", "y": "b", "z": "c",
                                                               **enc})])


def _grid_rows(order):
    gx, gy = np.meshgrid([0.0, 1.0, 3.0], [10.0, 20.0], indexing="xy")
    xs, ys = gx.ravel()[order], gy.ravel()[order]
    return xs.tolist(), ys.tolist(), (xs * 100 + ys).tolist()


def _surface_points(built):
    (surf,) = built.layer_artists[0]
    assert type(surf).__name__ == "Poly3DCollection"
    faces = np.ma.getdata(surf._faces) if hasattr(surf, "_faces") else None
    if faces is None:  # matplotlib <= 3.9
        vec = surf._vec
        return (vec[:3] / vec[3]).T
    return faces.reshape(-1, 3)


def test_grid_rows_in_any_order_become_a_surface_on_the_function():
    order = np.array([5, 0, 3, 1, 4, 2])
    built = build_figure(_spec(*_grid_rows(order)))
    pts = _surface_points(built)
    np.testing.assert_allclose(pts[:, 2], pts[:, 0] * 100 + pts[:, 1])
    assert set(np.round(pts[:, 0], 9)) == {0.0, 1.0, 3.0}
    assert set(np.round(pts[:, 1], 9)) == {10.0, 20.0}


def test_null_z_is_a_hole_with_a_warning_not_a_zero():
    xs, ys, zs = _grid_rows(np.arange(6))
    zs[4] = None
    built = build_figure(_spec(xs, ys, zs))
    pts = _surface_points(built)
    assert not np.any(pts[:, 2] == 0)
    assert "null_values_omitted" in [w.code for w in built.warnings]


def test_rows_that_do_not_form_a_grid_are_refused():
    xs, ys, zs = _grid_rows(np.arange(6))
    with pytest.raises(RenderError) as e:  # a missing cell
        build_figure(_spec(xs[:-1], ys[:-1], zs[:-1]))
    assert e.value.issues[0].code == "not_a_grid"
    with pytest.raises(RenderError) as e:  # a duplicated cell
        build_figure(_spec(xs + [0.0], ys + [10.0], zs + [1.0]))
    assert e.value.issues[0].code == "not_a_grid"


def test_null_position_and_series_are_refused():
    xs, ys, zs = _grid_rows(np.arange(6))
    xs[0] = None
    with pytest.raises(RenderError) as e:
        build_figure(_spec(xs, ys, zs))
    assert e.value.issues[0].code == "null_in_key_column"
    with pytest.raises(RenderError) as e:
        build_figure(_spec(*_grid_rows(np.arange(6)), series="s"))
    assert e.value.issues[0].code == "unsupported_channel"
