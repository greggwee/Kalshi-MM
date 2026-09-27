"""Entry point for the market data recorder.

Usage:
    python record.py                          # uses recorder_config.yaml
    python record.py --mode rest --duration 30   # smoke test: 30s of REST polling
    python record.py --config myconfig.yaml

rest mode needs no API keys (public prod endpoints). ws mode signs the
connection with DEMO_KEYID/DEMO_KEYFILE or PROD_KEYID/PROD_KEYFILE from .env
depending on `environment` in the config.
"""
import argparse
import asyncio
import logging
import os

import yaml
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization

from src.recorder import WSRecorder, RestPoller, expand_markets, store_market_metadata
from src.storage import RecorderDB


def main():
    parser = argparse.ArgumentParser(description="Kalshi market data recorder")
    parser.add_argument("--config", default="recorder_config.yaml")
    parser.add_argument("--mode", choices=["ws", "rest"], default=None,
                        help="override mode from config")
    parser.add_argument("--duration", type=float, default=None,
                        help="stop after N seconds (default: run forever)")
    args = parser.parse_args()

    with open(args.config) as f:
        config = yaml.safe_load(f)
    mode = args.mode or config.get("mode", "rest")
    environment = config.get("environment", "prod")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("recorder.log")])
    logger = logging.getLogger("recorder")

    db = RecorderDB(config.get("database", "data/kalshi_recorder.db"))
    tickers = expand_markets(config, environment, logger)
    if not tickers:
        logger.error("No markets to record; add tickers or series_tickers to the config")
        return
    store_market_metadata(db, tickers, environment, logger)

    try:
        if mode == "rest":
            poller = RestPoller(environment, tickers, db, logger,
                                poll_interval=float(config.get("poll_interval", 2.0)))
            logger.info(f"REST poller starting ({environment}, {poller.poll_interval}s interval)")
            poller.run(duration=args.duration)
        else:
            load_dotenv()
            prefix = "DEMO" if environment == "demo" else "PROD"
            key_id = os.getenv(f"{prefix}_KEYID")
            keyfile = os.getenv(f"{prefix}_KEYFILE")
            if not key_id or not keyfile:
                logger.error(f"ws mode on {environment} needs {prefix}_KEYID and "
                             f"{prefix}_KEYFILE in .env (Kalshi requires an authenticated "
                             f"WS connection even for public channels)")
                return
            with open(keyfile, "rb") as kf:
                private_key = serialization.load_pem_private_key(kf.read(), password=None)
            rec = WSRecorder(key_id, private_key, environment, tickers, db, logger,
                             snapshot_interval=float(config.get("snapshot_interval", 60.0)))
            logger.info(f"WS recorder starting ({environment})")
            asyncio.run(rec.run(duration=args.duration))
    except KeyboardInterrupt:
        logger.info("stopped by user")
    finally:
        db.commit()
        logger.info(f"final row counts: {db.counts()}")
        db.close()


if __name__ == "__main__":
    main()
