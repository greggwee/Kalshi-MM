import argparse
import logging
from concurrent.futures import ThreadPoolExecutor
import yaml
from dotenv import load_dotenv
import os
from typing import Dict
from src.clients import KalshiHttpClient, Environment
from helper import AvellanedaMarketMaker
from cryptography.hazmat.primitives import serialization

def load_private_key(KEYFILE: str):
    with open(KEYFILE, "rb") as key_file:
        return serialization.load_pem_private_key(
            key_file.read(),
            password=None  # Provide the password if your key is encrypted
        )
    
# Create shared HTTP client (in main block, before thread pool)
load_dotenv()
env = Environment.DEMO # toggle environment here
KEYID = os.getenv('DEMO_KEYID') if env == Environment.DEMO else os.getenv('PROD_KEYID')
KEYFILE = os.getenv('DEMO_KEYFILE') if env == Environment.DEMO else os.getenv('PROD_KEYFILE')
private_key = load_private_key(KEYFILE)

shared_http_client = KalshiHttpClient(
    key_id=KEYID,
    private_key=private_key,
    environment=env
)

def load_config(config_file):
    with open(config_file, 'r') as f:
        return yaml.safe_load(f)

DEPRECATED_KEYS = ("sigma", "T", "inventory_skew_factor", "position_limit_buffer", "k")


def create_market_maker(mm_config, api_config, client, logger):
    for key in DEPRECATED_KEYS:
        if key in mm_config:
            logger.warning(f"config key '{key}' is no longer used "
                           f"(sigma/k are estimated from data, T comes from the "
                           f"market's close_time); ignoring")
    if 'trade_side' in api_config:
        logger.warning("config key 'trade_side' is no longer used: the bot quotes "
                       "both sides on the yes-price axis via V2 bid/ask orders")
    return AvellanedaMarketMaker(
        logger=logger,
        client=client,
        market_ticker=api_config['market_ticker'],
        gamma=mm_config.get('gamma', 1.0),
        default_k=mm_config.get('default_k', 40.0),
        base_order_size=mm_config.get('base_order_size', 5),
        max_position=mm_config.get('max_position', 20),
        order_expiration=mm_config.get('order_expiration', 300),
        min_spread=mm_config.get('min_spread', 0.02),
        premium_scale=mm_config.get('premium_scale', 0.02),
        min_time_to_resolution_h=mm_config.get('min_time_to_resolution_h', 0.05),
        min_quote_mid=mm_config.get('min_quote_mid', 0.05),
        max_quote_mid=mm_config.get('max_quote_mid', 0.95),
        gamma_mode=mm_config.get('gamma_mode', 'constant'),
        calibration_constant=mm_config.get('calibration_constant', 0.04),
        sigma_floor=mm_config.get('sigma_floor', 0.01),
        fair_value_adjustment=mm_config.get('fair_value_adjustment', 0.0),
        max_runtime=mm_config.get('max_runtime'),
    )

def run_strategy(config_name: str, config: Dict, http_client: KalshiHttpClient):
    # Create a logger for this specific strategy
    logger = logging.getLogger(f"Strategy_{config_name}")
    logger.setLevel(config.get('log_level', 'INFO'))

    # Create file handler
    fh = logging.FileHandler(f"{config_name}.log")
    fh.setLevel(config.get('log_level', 'INFO'))
    
    # Create console handler
    ch = logging.StreamHandler()
    ch.setLevel(config.get('log_level', 'INFO'))
    
    # Create formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)
    
    # Add handlers to logger
    logger.addHandler(fh)
    logger.addHandler(ch)

    logger.info(f"Starting strategy: {config_name}")

    # Create market maker (no separate API object needed)
    market_maker = create_market_maker(
        config['market_maker'],
        config['api'],
        http_client,
        logger
    )

    try:
        # Run market maker
        market_maker.run(config.get('dt', 1.0))
    except KeyboardInterrupt:
        logger.info("Market maker stopped by user")
    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
        raise
    finally:
        logger.info(f"Strategy {config_name} stopped")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kalshi Market Making Algorithm")
    parser.add_argument("--config", type=str, default="config.yaml", help="Path to config file")
    args = parser.parse_args()

    # Load all configurations
    configs = load_config(args.config)

    # Load environment variables
    load_dotenv()

    # Print the name of every strategy being run
    print("Starting the following strategies:")
    for config_name in configs:
        print(f"- {config_name}")

    # Run all strategies in parallel using ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(configs)) as executor:
        for config_name, config in configs.items():
            executor.submit(run_strategy, config_name, config, shared_http_client)