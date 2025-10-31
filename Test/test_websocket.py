
import websocket
import requests
import json
import configparser
import threading
import time
import os

# --- Configuration ---
try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.ini')
    config = configparser.ConfigParser()
    config.read(config_path, encoding='utf-8')

    API_PASSWORD = config['SECRETS']['API_PASSWORD']
    TICKER = config['TRADE_SETTINGS']['TICKER']
    EXCHANGE = int(config['TRADE_SETTINGS']['EXCHANGE'])
    API_URL = "http://localhost:18080/kabusapi"
    WS_URL = "ws://localhost:18080/kabusapi/websocket"
except Exception as e:
    print(f"Error reading config file: {e}")
    exit()

# Global variable to hold the WebSocketApp instance
ws_app = None

# --- WebSocket Callbacks ---
def on_message(ws, message):
    """Callback executed when a message is received."""
    print(f"--- MESSAGE RECEIVED ---")
    print(message)
    print(f"----------------------")

def on_error(ws, error):
    """Callback executed when an error occurs."""
    print(f"--- WebSocket Error ---")
    print(error)
    print(f"-----------------------")

def on_close(ws, close_status_code, close_msg):
    """Callback executed when the connection is closed."""
    print("--- WebSocket Connection Closed ---")
    if close_status_code or close_msg:
        print(f"Code: {close_status_code}")
        print(f"Message: {close_msg}")
    print("---------------------------------")

def on_open(ws):
    """Callback executed when the connection is opened."""
    print("--- WebSocket Connection Opened ---")
    # This test script registers the symbol *before* connecting,
    # so no action is needed here.

# --- Main Functions ---
def get_token():
    """Gets an API token."""
    print("1. Getting API token...")
    url = f"{API_URL}/token"
    payload = {"APIPassword": API_PASSWORD}
    try:
        response = requests.post(url, data=json.dumps(payload), headers={'Content-Type': 'application/json'})
        response.raise_for_status()
        token = response.json()["Token"]
        print(f"   => Token acquired successfully.")
        return token
    except requests.RequestException as e:
        print(f"   => Failed to get token: {e}")
        return None

def register_symbol(token):
    """Registers the symbol for PUSH notifications."""
    print(f"2. Registering symbol {TICKER} for PUSH notifications...")
    url = f"{API_URL}/register"
    headers = {'Content-Type': 'application/json', 'X-API-KEY': token}
    payload = {"Symbols": [{"Symbol": str(TICKER), "Exchange": EXCHANGE}]}
    try:
        response = requests.put(url, data=json.dumps(payload), headers=headers)
        response.raise_for_status()
        print(f"   => Symbol {TICKER} registered successfully.")
        return True
    except requests.RequestException as e:
        print(f"   => Failed to register symbol: {e}")
        if e.response:
            print(f"      Response: {e.response.text}")
        return False

def main():
    """Main execution logic."""
    global ws_app
    
    token = get_token()
    if not token:
        return

    if not register_symbol(token):
        return

    print("3. Connecting to WebSocket...")
    ws_app = websocket.WebSocketApp(WS_URL,
                                  on_open=on_open,
                                  on_message=on_message,
                                  on_error=on_error,
                                  on_close=on_close)

    # Run the WebSocket connection in a separate thread
    ws_thread = threading.Thread(target=ws_app.run_forever)
    ws_thread.daemon = True
    ws_thread.start()

    print("\n--- Waiting for messages for 60 seconds ---")
    print("If the environment is correct, you should see price data below.")
    print("If nothing appears, the problem is with the kabu station environment.")
    
    try:
        # Wait for 60 seconds to see if any messages arrive
        time.sleep(60)
    except KeyboardInterrupt:
        print("Interrupted by user.")

    print("\n--- Test finished. Closing connection. ---")
    if ws_app:
        ws_app.close()

if __name__ == "__main__":
    main()
