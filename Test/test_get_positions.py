
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
    logging.info("--- Testing Physical Positions Inquiry ---")
    
    # --- Config ---
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.ini')
    
    # --- Main Logic ---
    api = KabuAPI(config_path=config_path)
    
    if api.get_token():
        logging.info("Querying physical positions...")
        success, positions = api.get_physical_positions()
        
        if success:
            logging.info("--- API Response: Physical Positions ---")
            print(json.dumps(positions, indent=2, ensure_ascii=False))
        else:
            logging.error(f"--- API Call Failed ---")
            print(json.dumps(positions, indent=2, ensure_ascii=False))
