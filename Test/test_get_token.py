import requests
import json
import configparser
import os

# Construct the absolute path to the config.ini file in the Test directory
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, 'config.ini')

# Read the configuration
config = configparser.ConfigParser()
config.read(config_path, encoding='utf-8')

# Get the API password from the [SECRETS] section
try:
    api_password = config['SECRETS']['API_PASSWORD']
except KeyError:
    print("[ERROR] 'API_PASSWORD' not found in the [SECRETS] section of config.ini")
    exit()

if not api_password or api_password == 'YOUR_API_PASSWORD_HERE':
    print("[ERROR] API password is not set in config.ini")
    exit()

# Define the API endpoint from config
api_protocol = config.get('API_SETTINGS', 'PROTOCOL', fallback='http')
api_port = config.get('API_SETTINGS', 'PORT', fallback='18080')
api_url = f"{api_protocol}://localhost:{api_port}/kabusapi"
token_url = f"{api_url}/token"
payload = {"APIPassword": api_password}

print(f"Attempting to get token from: {token_url}")

try:
    # HTTPS接続の場合、証明書検証を無効にする (自己署名証明書対策)
    verify_ssl = api_protocol != 'https'
    response = requests.post(token_url, data=json.dumps(payload), headers={'Content-Type': 'application/json'}, verify=verify_ssl)
    
    response.raise_for_status()
    
    token = response.json().get("Token")
    
    if token:
        print(f"Successfully retrieved token: {token}")
    else:
        print(f"Failed to retrieve token. Response: {response.json()}")

except requests.exceptions.RequestException as e:
    print(f"[ERROR] Failed to get token: {e}")