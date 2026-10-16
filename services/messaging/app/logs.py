"""One logging setup for the api, worker and notifier, with a filter that masks every line.

The code already avoids logging phones and secrets; this filter is the safety net in case
something slips through (including uvicorn's access log, which prints query strings).
"""

import logging
import re

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

PHONE = re.compile(r"\+?\d(?:[ -]?\d){7,}")  # 8+ digits, maybe split by spaces or dashes
BEARER = re.compile(r"(?i)\bbearer\s+\S+")
SECRET_PARAM = re.compile(r"(?i)\b([\w.]*(?:token|secret|key|password)[\w.]*)=[^\s&]+")
URL_PATH = re.compile(r"(https?://[^\s/]+)/\S*")  # approval/upload links carry tokens


def mask_secrets(text: str) -> str:
    text = URL_PATH.sub(r"\1/****", text)
    text = BEARER.sub("Bearer ****", text)
    text = SECRET_PARAM.sub(r"\1=****", text)
    return PHONE.sub(lambda m: "****" + re.sub(r"\D", "", m.group())[-4:], text)


class MaskFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_secrets(record.getMessage())
        record.args = None  # already merged into msg
        if record.exc_info:
            # Tracebacks can contain values too: mask the formatted text instead
            record.exc_text = mask_secrets(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


MASK = MaskFilter()


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=FORMAT)
    for handler in logging.getLogger().handlers:
        handler.addFilter(MASK)
    # uvicorn's loggers don't pass through the root handler, so filter them directly
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).addFilter(MASK)
