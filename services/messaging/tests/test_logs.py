import logging
import sys

import pytest

from app.logs import MASK, mask_secrets


@pytest.mark.parametrize(
    ("line", "leaked", "kept"),
    [
        ("message from 8801700001234", "8801700001234", "****1234"),
        ("message from +880 1700 001234", "1700 001234", "****1234"),
        ("Authorization: Bearer EAAGm0PX4ZCpsBA", "EAAGm0PX4ZCpsBA", "Bearer ****"),
        (
            "GET /webhook?hub.mode=subscribe&hub.verify_token=abc123&hub.challenge=9",
            "abc123",
            "hub.verify_token=****",
        ),
        ("api_key=sk-ant-xyz and password=hunter2", "hunter2", "api_key=****"),
        ("link https://id.test/approve/tok_secret here", "tok_secret", "https://id.test/****"),
    ],
)
def test_secrets_are_masked(line, leaked, kept):
    masked = mask_secrets(line)
    assert leaked not in masked
    assert kept in masked


def test_normal_text_is_kept():
    line = "transfer abc failed (RuntimeError), retry 1/2, amount 10000"
    assert mask_secrets(line) == line


def test_filter_masks_args_and_tracebacks():
    try:
        raise ValueError("bad phone 8801700001234")
    except ValueError:
        record = logging.LogRecord(
            "x", logging.ERROR, __file__, 1, "from %s", ("8801700001234",), sys.exc_info()
        )
    assert MASK.filter(record)
    text = logging.Formatter().format(record)
    assert "8801700001234" not in text
    assert "from ****1234" in text and "bad phone ****1234" in text


def test_real_uvicorn_access_log_line_is_masked_and_does_not_crash():
    # uvicorn's access formatter reads record.args, so the filter must keep the tuple
    from uvicorn.logging import AccessFormatter

    args = ("127.0.0.1:5000", "GET", "/webhook?hub.verify_token=abc123", "1.1", 200)
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d', args, None
    )
    assert MASK.filter(record)
    line = AccessFormatter(use_colors=False).format(record)
    assert "abc123" not in line and "hub.verify_token=****" in line
