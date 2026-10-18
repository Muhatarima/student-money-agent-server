"""Never log tokens. Our page URLs carry one-time tokens (?t=...), and uvicorn's access log
prints every URL, so this filter masks them (and Bearer/key=value secrets) in every line."""

import logging
import re

TOKEN_PARAM = re.compile(r"([?&](?:t|token|[\w.]*key|[\w.]*secret)=)[^&\s\"]+", re.IGNORECASE)
BEARER = re.compile(r"(?i)\bbearer\s+\S+")


def mask_secrets(text: str) -> str:
    return BEARER.sub("Bearer ****", TOKEN_PARAM.sub(r"\1****", text))


class MaskFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Mask the message and each argument, but keep their shape: uvicorn's access-log
        # formatter needs the original args tuple
        record.msg = mask_secrets(str(record.msg))
        if isinstance(record.args, tuple):
            record.args = tuple(mask_secrets(a) if isinstance(a, str) else a for a in record.args)
        return True


MASK = MaskFilter()


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(MASK)
    # uvicorn's loggers don't pass through the root handler, so filter them directly
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).addFilter(MASK)
