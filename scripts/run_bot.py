#!/usr/bin/env python3
"""Run the bot.

    python scripts/run_bot.py                 # dry run unless config arms live orders
    python scripts/run_bot.py --cycles 3      # a few cycles, then exit (smoke test)

Reads config/config.yaml. With ``dry_run: true`` (the shipped default) the full
pipeline runs — phases, signals, the Risk Warden, paper positions marked on
live ticks, Telegram alerts — and no order is ever sent. Live orders need both
``dry_run: false`` and the exact ``live_confirm`` phrase.

Stop it with Ctrl-C or ``/flat`` then Ctrl-C. Live positions keep their
server-side stops after the bot exits; that is the point of server-side stops.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goldbot.calendar import Calendar  # noqa: E402
from goldbot.config import load_config  # noqa: E402
from goldbot.engine import Engine  # noqa: E402
from goldbot.journal import Journal  # noqa: E402
from goldbot.mt5_client import MT5Client, MT5Error, MT5UnavailableError  # noqa: E402
from goldbot.notifier import Telegram  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--cycles", type=int, default=None, help="Stop after N cycles (smoke test)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    Path(cfg.reporting.log_dir).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, cfg.reporting.log_level),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(Path(cfg.reporting.log_dir) / "goldbot.log", encoding="utf-8"),
        ],
    )
    log = logging.getLogger("run_bot")

    if cfg.live_orders_armed:
        log.warning("=" * 70)
        log.warning("LIVE ORDERS ARMED. This will trade real money on account %s.", cfg.mt5.login)
        log.warning("=" * 70)
    else:
        log.info("dry run: no orders will be sent")

    journal = Journal(cfg.reporting.journal_db)
    notifier = Telegram(cfg.telegram)
    calendar = Calendar(cfg.calendar, cache_path=Path(cfg.reporting.journal_db).parent / "calendar.json")

    try:
        client = MT5Client(cfg.mt5)
    except MT5UnavailableError as exc:
        log.error("%s", exc)
        return 2

    engine = Engine(cfg, client, journal, notifier, calendar)
    try:
        engine.start()
    except MT5Error as exc:
        log.error("could not start: %s", exc)
        return 1

    if args.cycles is not None:
        # A smoke test should take seconds, not a minute per cycle.
        import time

        engine.run(max_cycles=args.cycles, sleep=lambda s: time.sleep(min(s, 1.0)))
    else:
        engine.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
