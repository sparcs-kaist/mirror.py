import pytest

from mirror.toolbox import parse_file_mode


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0770", 0o770),
        ("0o770", 0o770),
        ("770", 0o770),
        ("600", 0o600),
        ("0", 0),
        ("7777", 0o7777),
    ],
)
def test_parse_file_mode_valid(value, expected):
    assert parse_file_mode(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "0o",
        "789",
        "garbage",
        "0o778",
        "10000",
    ],
)
def test_parse_file_mode_rejects_invalid_strings(value):
    with pytest.raises(ValueError):
        parse_file_mode(value)


@pytest.mark.parametrize(
    "value",
    [
        0o770,
        504,
        True,
        False,
        None,
        7.0,
        ["0770"],
    ],
)
def test_parse_file_mode_rejects_non_strings(value):
    with pytest.raises(ValueError):
        parse_file_mode(value)
