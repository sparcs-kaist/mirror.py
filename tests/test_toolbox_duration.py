import pytest

from mirror.toolbox import parse_iso_duration


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
