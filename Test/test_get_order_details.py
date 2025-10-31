
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
    # --- Target Order ID ---
    TARGET_ORDER_ID = "20251031A02N19891805"
    
    logging.info(f"--- Searching for Order ID: {TARGET_ORDER_ID} in all orders ---")
    
    # --- Config ---
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.ini')
    
    # --- Main Logic ---
    api = KabuAPI(config_path=config_path)
    
    if api.get_token():
        logging.info("Querying all orders...")
        success, orders = api.get_orders_list()
        
        if success:
            found_order = None
            for order in orders:
                if order.get('ID') == TARGET_ORDER_ID:
                    found_order = order
                    break
            
            if found_order:
                logging.info(f"--- Found Order Details for {TARGET_ORDER_ID} ---")
                print(json.dumps(found_order, indent=2, ensure_ascii=False))
            else:
                logging.warning(f"--- Order {TARGET_ORDER_ID} not found in today's order list. ---")
                logging.info("Listing all orders found today:")
                print(json.dumps(orders, indent=2, ensure_ascii=False))

        else:
            logging.error(f"--- API Call to get orders list failed ---")
            print(json.dumps(orders, indent=2, ensure_ascii=False))
