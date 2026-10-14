import numpy as np
import pytest

from ieeVisualizer._domain import make_grid, normalize_bounds, sample_axis


def test_normalize_bounds_broadcasts_single_pair():
    assert normalize_bounds((-1, 2), 3) == ((-1.0, 2.0),) * 3


def test_normalize_bounds_accepts_per_axis_pairs():
    assert normalize_bounds([(0, 1), (2, 5)], 2) == ((0.0, 1.0), (2.0, 5.0))


@pytest.mark.parametrize("bad", [(1, 0), (0, np.nan), [(0, 1)] * 3, "ab"])
def test_normalize_bounds_rejects(bad):
    with pytest.raises(ValueError):
        normalize_bounds(bad, 2)


@pytest.mark.parametrize("n", [0, 1, 2.5])
def test_sample_axis_rejects_bad_n(n):
    with pytest.raises(ValueError):
        sample_axis((0, 1), n, linspace=True)


def test_random_axis_reproducible_with_seeded_generator():
    a = sample_axis((0, 1), 10, linspace=False, rng=np.random.default_rng(5))
    b = sample_axis((0, 1), 10, linspace=False, rng=np.random.default_rng(5))
    np.testing.assert_array_equal(a, b)


def test_make_grid_shapes_and_xy_indexing():
    X, Y = make_grid([(0, 1), (10, 20)], 4, linspace=True)
    assert X.shape == Y.shape == (4, 4)
    np.testing.assert_allclose(X[0], np.linspace(0, 1, 4))
    np.testing.assert_allclose(Y[:, 0], np.linspace(10, 20, 4))
    (x,) = make_grid([(0, 1)], 5, linspace=True)
    assert x.shape == (5,)
