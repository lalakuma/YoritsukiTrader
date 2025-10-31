
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
    logging.info("--- Placing Market Sell Order ---")
    
    # --- Order Details ---
    TICKER = "4751"
    EXCHANGE = 1
    QTY = 100
    SIDE = "1" # 1: 売付

    # --- Config ---
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.ini')
    
    # --- Main Logic ---
    api = KabuAPI(config_path=config_path)
    
    if api.get_token():
        logging.info(f"Sending Market SELL order for {QTY} shares of {TICKER}...")
        
        # send_market_order を使用するが、sideを'1'(売付)に設定
        success, order_info = api.send_market_order(
            symbol=TICKER,
            exchange=EXCHANGE,
            qty=QTY,
            side=SIDE
        )
        
        if success:
            logging.info("--- Order Submission Successful ---")
            print(json.dumps(order_info, indent=2, ensure_ascii=False))
        else:
            logging.error(f"--- Order Submission Failed ---")
            print(json.dumps(order_info, indent=2, ensure_ascii=False))
