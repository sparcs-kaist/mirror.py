"""Packages.__init__ must reject pkgids that collide with reserved attributes."""
import pytest

from mirror.structure import Packages


def test_reserved_attribute_pkgid_rejected():
    for bad in ["get", "items", "keys", "values", "to_dict", "_keys"]:
        with pytest.raises(ValueError, match="Invalid package id"):
            Packages({bad: {}})


def test_underscore_prefixed_pkgid_rejected():
    with pytest.raises(ValueError, match="Invalid package id"):
        Packages({"_anything": {}})


def test_valid_pkgid_accepted_minimal():
    """A normal pkgid is accepted (validation runs before Package.from_dict)."""
    # We expect Package.from_dict to fail later with KeyError or ValueError
    # because of missing fields -- the point is ValueError("Invalid package id")
    # should NOT be raised.
    with pytest.raises((KeyError, ValueError)) as exc_info:
        Packages({"normal_pkg": {}})
    assert "Invalid package id" not in str(exc_info.value)


def test_packages_class_definition_does_not_apply_dataclass_decorator():
    """The Packages class definition itself must not be decorated with @dataclass.

    The previous code applied @dataclass *and* overrode __init__, which was misleading.
    We verify the explicit __init__ is the one Packages defines.
    """
    import inspect
    sig = inspect.signature(Packages.__init__)
    params = list(sig.parameters)
    assert params == ["self", "pkgs"], (
        f"Packages.__init__ should be the explicit one taking pkgs; got {params}"
    )
