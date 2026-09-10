"""Compatibility entry point retained for existing schedulers.

Automatic decision e-mails are intentionally disabled.  The only automatic
mail trigger is ``submission_received`` and it runs directly after a valid
submission is stored.
"""

from __future__ import annotations

import logging


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Automatic decision e-mails are disabled; nothing to process.")


if __name__ == "__main__":
    main()
