import pytest

from mirror.toolbox import format_iso_duration, parse_iso_duration


@pytest.mark.parametrize(
    ("duration", "seconds"),
    [
        ("", 0),
        ("PUSH", -1),
        ("P1D", 86400),
        ("PT1H", 3600),
        ("PT30M", 1800),
        ("PT10S", 10),
        ("P1DT2H3M4S", 93784),
    ],
)
def test_parse_iso_duration_valid(duration, seconds):
    assert parse_iso_duration(duration) == seconds


@pytest.mark.parametrize(
    "duration",
    [
        "P1Dxxx",
        "P",
        "PT",
        "P1DT",
        "P1Y",
        "P1M",
        "P1W",
        "push",
        " P1D",
        "P1D ",
    ],
)
def test_parse_iso_duration_rejects_invalid(duration):
    with pytest.raises(ValueError):
        parse_iso_duration(duration)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, ""),
        (-1, "PUSH"),
        (86400, "P1D"),
        (3600, "PT1H"),
        (1800, "PT30M"),
        (10, "PT10S"),
        (93784, "P1DT2H3M4S"),
        (2678400, "P31D"),
        (5184000, "P60D"),
        (31536000, "P365D"),
    ],
)
def test_format_iso_duration_valid(seconds, expected):
    assert format_iso_duration(seconds) == expected


def test_format_iso_duration_rejects_negative():
    with pytest.raises(ValueError):
        format_iso_duration(-2)


@pytest.mark.parametrize(
    "iso8601",
    ["P1D", "PT1H", "P31D", "P60D", "P365D", "P1DT2H3M4S"],
)
def test_iso_duration_round_trip(iso8601):
    assert format_iso_duration(parse_iso_duration(iso8601)) == iso8601
