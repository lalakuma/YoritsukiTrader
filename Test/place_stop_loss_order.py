
import sys
import os
import json
import logging

# Add Honban directory to path to import KabuAPI
honban_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'Honban'))
if honban_path not in sys.path:
    sys.path.append(honban_path)

try:
    from kabu_api import KabuAPI
except ImportError:
    logging.error("kabu_api.pyが見つかりません。Honbanフォルダに存在することを確認してください。")
    sys.exit(1)

# --- Logger Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

if __name__ == "__main__":
    logging.info("--- Placing Stop-Loss Sell Order ---")
    
    # --- Order Details ---
    TICKER = "4751"
    EXCHANGE = 1
    QTY = 100
    TRIGGER_PRICE = 1530.0

    # --- Config ---
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.ini')
    
    # --- Main Logic ---
    api = KabuAPI(config_path=config_path)
    
    if api.get_token():
        logging.info(f"Sending Stop-Loss SELL order for {QTY} shares of {TICKER} at trigger price {TRIGGER_PRICE}...")
        
        success, order_info = api.send_stop_sell_order(
            symbol=TICKER,
            exchange=EXCHANGE,
            qty=QTY,
            password=api.trade_password, # Use the password from the api instance
            trigger_price=TRIGGER_PRICE
        )
        
        if success:
            logging.info("--- Order Submission Successful ---")
            print(json.dumps(order_info, indent=2, ensure_ascii=False))
        else:
            logging.error(f"--- Order Submission Failed ---")
            print(json.dumps(order_info, indent=2, ensure_ascii=False))
