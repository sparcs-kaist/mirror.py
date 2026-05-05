"""PromptHandler must fall back to plain text on non-TTY / TERM=dumb."""
import io
import logging
import os
import sys
from unittest.mock import patch

import pytest

from mirror.logger.handler import PromptHandler


def _make_record(msg: str = "hello") -> logging.LogRecord:
    return logging.LogRecord(
        name="mirror",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )


def test_falls_back_to_plain_when_not_a_tty(monkeypatch, capsys):
    handler = PromptHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))

    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)

    handler.emit(_make_record("plain output"))

    captured = capsys.readouterr().out
    assert "plain output" in captured
    # No raw escape codes
    assert "\x1b[" not in captured


def test_falls_back_to_plain_when_term_is_dumb(monkeypatch, capsys):
    handler = PromptHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))

    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setenv("TERM", "dumb")

    handler.emit(_make_record("dumb terminal"))

    captured = capsys.readouterr().out
    assert "dumb terminal" in captured
    assert "\x1b[" not in captured


def test_strips_ansi_codes_in_plain_output(monkeypatch, capsys):
    handler = PromptHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))

    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)

    record = _make_record("\x1b[31mRED\x1b[0m text")
    handler.emit(record)

    captured = capsys.readouterr().out
    assert "RED text" in captured
    assert "\x1b[31m" not in captured
    assert "\x1b[0m" not in captured
