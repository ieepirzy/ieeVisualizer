import datetime as dt
import math

import numpy as np
import pytest

from ieeVisualizer.spec import Column, DataSnapshot, Provenance


def test_missing_values_stay_null_never_zero():
    c = Column([1.0, None, float("nan"), 4])
    assert c.values == [1.0, None, None, 4.0]
    assert c.dtype == "float"
    assert 0 not in c.values and 0.0 not in c.values


def test_numpy_inputs_are_converted_to_plain_json_values():
    c = Column(np.array([1, 2, 3], dtype=np.int64))
    assert c.values == [1, 2, 3] and all(type(v) is int for v in c.values)
    assert c.dtype == "int"
    f = Column(np.array([1.5, np.nan]))
    assert f.values == [1.5, None] and f.dtype == "float"


def test_numpy_datetime64_becomes_iso_strings_and_nat_null():
    arr = np.array(["2026-01-01T00:00", "NaT"], dtype="datetime64[m]")
    c = Column(arr)
    assert c.dtype == "datetime"
    assert c.values == ["2026-01-01T00:00", None]


def test_python_datetimes_become_iso_strings():
    c = Column([dt.date(2026, 1, 1), dt.datetime(2026, 1, 2, 3, 4)])
    assert c.values == ["2026-01-01", "2026-01-02T03:04:00"]
    assert c.dtype == "datetime"


def test_int_values_in_float_column_are_floats():
    assert Column([1, 2], dtype="float").values == [1.0, 2.0]


def test_all_null_column_dtype_is_not_guessed():
    assert Column([None, None]).dtype is None


@pytest.mark.parametrize(
    "values, dtype, fragment",
    [
        ([1.0, math.inf], None, "infinities"),
        ([1, "a"], None, "mixes value types"),
        ([1.5], "int", "cannot hold"),
        ([1], "money", "unknown dtype"),
        ([[1, 2]], None, "unsupported type"),
    ],
)
def test_bad_values_are_rejected_with_a_fix_hint(values, dtype, fragment):
    with pytest.raises(ValueError, match=fragment):
        Column(values, dtype=dtype)


def test_scalar_values_rejected():
    with pytest.raises(TypeError):
        Column(5)


def _snap(order):
    cols = {"a": [1.0, 2.0], "b": ["x", "y"]}
    units = {"a": "EUR"}
    return DataSnapshot.from_columns({k: cols[k] for k in order}, units=units)


def test_content_hash_is_independent_of_column_order():
    h1 = _snap(["a", "b"]).content_hash()
    h2 = _snap(["b", "a"]).content_hash()
    assert h1 == h2
    assert h1.startswith("sha256:") and len(h1) == len("sha256:") + 64


def test_content_hash_changes_with_value_unit_or_null():
    base = _snap(["a", "b"])
    changed_value = DataSnapshot.from_columns({"a": [1.0, 2.5], "b": ["x", "y"]},
                                              units={"a": "EUR"})
    changed_unit = DataSnapshot.from_columns({"a": [1.0, 2.0], "b": ["x", "y"]},
                                             units={"a": "kEUR"})
    nulled = DataSnapshot.from_columns({"a": [1.0, None], "b": ["x", "y"]}, units={"a": "EUR"})
    hashes = {s.content_hash() for s in (base, changed_value, changed_unit, nulled)}
    assert len(hashes) == 4


def test_null_and_zero_hash_differently():
    assert (DataSnapshot.from_columns({"a": [None]}).content_hash()
            != DataSnapshot.from_columns({"a": [0]}).content_hash())


def test_content_hash_excludes_provenance():
    a = _snap(["a", "b"])
    b = _snap(["a", "b"])
    b.provenance = Provenance(source="finance", retrieved_at="2026-10-14T00:00:00Z")
    assert a.content_hash() == b.content_hash()


def test_int_and_equal_float_input_hash_the_same_in_float_column():
    a = DataSnapshot.from_columns({"a": [1, 2]}, dtypes={"a": "float"})
    b = DataSnapshot.from_columns({"a": [1.0, 2.0]})
    assert a.content_hash() == b.content_hash()


def test_num_rows_and_unknown_unit_column():
    s = DataSnapshot.from_columns({"a": [1, 2], "b": [1]})
    assert s.num_rows is None
    assert DataSnapshot.from_columns({"a": [1, 2]}).num_rows == 2
    with pytest.raises(ValueError, match="unknown column"):
        DataSnapshot.from_columns({"a": [1]}, units={"z": "m"})
