import requests
import json
import websocket
import threading
import configparser
import logging

class KabuAPI:
    def __init__(self, config_path='config.ini', logger=None):
        config = configparser.ConfigParser()
        config.read(config_path, encoding='utf-8')
        self.password = config['SECRETS']['API_PASSWORD']
        self.api_url = "http://localhost:18080/kabusapi"
        self.ws_url = "ws://localhost:18080/kabusapi/websocket"
        self.token = None
        self.ws = None
        self.ws_thread = None

        if logger is None:
            self.logger = logging.getLogger(__name__)
            self.logger.setLevel(logging.INFO)
            # Add a default handler if no logger is provided
            if not self.logger.handlers:
                handler = logging.StreamHandler()
                formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
                handler.setFormatter(formatter)
                self.logger.addHandler(handler)
        else:
            self.logger = logger

    def get_token(self):
        """APIトークンを取得する"""
        if not self.password or self.password == 'YOUR_API_PASSWORD_HERE':
            self.logger.error("[ERROR] APIパスワードがconfig.iniに設定されていません。")
            return False

        url = f"{self.api_url}/token"
        payload = {"APIPassword": self.password}
        try:
            response = requests.post(url, data=json.dumps(payload), headers={'Content-Type': 'application/json'})
            response.raise_for_status()
            self.token = response.json()["Token"]
            self.logger.info(f"[API] トークンの取得に成功しました。")
            return True
        except requests.RequestException as e:
            self.logger.error(f"[ERROR] トークンの取得に失敗しました: {e}")
            if e.response is not None:
                self.logger.error(f"Response content: {e.response.text}")
            return False

    def register_symbol(self, ticker, exchange):
        """PUSH通知用の銘柄を登録する"""
        url = f"{self.api_url}/register"
        headers = {'Content-Type': 'application/json', 'X-API-KEY': self.token}
        payload = {"Symbols": [{"Symbol": str(ticker), "Exchange": exchange}]}
        try:
            response = requests.put(url, data=json.dumps(payload), headers=headers)
            response.raise_for_status()
            self.logger.info(f"[API] 銘柄登録に成功しました: {ticker}")
            return True
        except requests.RequestException as e:
            self.logger.error(f"[ERROR] 銘柄登録に失敗しました: {e}")
            return False

    def register_board(self, ticker, exchange, product=1): # product=1 for現物, assuming it's needed for board
        """PUSH通知用の板情報を登録する"""
        url = f"{self.api_url}/register"
        headers = {'Content-Type': 'application/json', 'X-API-KEY': self.token}
        # Assuming 'Product' is needed to specify board type, and 'Board' is the key
        payload = {"Symbols": [{"Symbol": str(ticker), "Exchange": exchange, "Product": product, "Board": True}]}
        try:
            response = requests.put(url, data=json.dumps(payload), headers=headers)
            response.raise_for_status()
            self.logger.info(f"[API] 板情報登録に成功しました: {ticker}")
            return True
        except requests.RequestException as e:
            self.logger.error(f"[ERROR] 板情報登録に失敗しました: {e}")
            return False

    def get_board_snapshot(self, ticker, exchange):
        """指定銘柄の板情報スナップショットを取得する"""
        url = f"{self.api_url}/board/{ticker}@{exchange}"
        headers = {'X-API-KEY': self.token}
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            board_data = response.json()
            self.logger.info(f"[API] 板情報スナップショット取得成功: {ticker}")
            return board_data
        except requests.RequestException as e:
            self.logger.error(f"[ERROR] 板情報スナップショット取得失敗: {e}")
            return None

    def connect_websocket(self, on_message_callback, on_error_callback, on_close_callback, on_open_callback):
        """WebSocketに接続し、受信スレッドを開始する"""
        self.logger.info("[INFO] WebSocketに接続します...")
        self.ws = websocket.WebSocketApp(self.ws_url,
                                         on_message=on_message_callback,
                                         on_error=on_error_callback,
                                         on_close=on_close_callback)
        self.ws.on_open = on_open_callback
        self.ws_thread = threading.Thread(target=self.ws.run_forever)
        self.ws_thread.daemon = True # メインスレッドが終了したら、このスレッドも終了する
        self.ws_thread.start()

    def close_websocket(self):
        """WebSocket接続を閉じる"""
        if self.ws:
            self.ws.close()

    def send_short_sell_order(self, ticker, exchange, qty, trade_password):
        """空売り注文を送信する"""
        url = f"{self.api_url}/sendorder"
        headers = {'Content-Type': 'application/json', 'X-API-KEY': self.token}
        payload = {
            'Password': trade_password,
            'Symbol': str(ticker),
            'Exchange': exchange,
            'SecurityType': 1,
            'Side': '1',
            'CashMargin': 3,
            'DelivType': 0,
            'AccountType': 4,
            'Qty': qty,
            'FrontOrderType': 20,
            'Price': 0,
            'ExpireDay': 0
        }
        try:
            response = requests.post(url, data=json.dumps(payload), headers=headers)
            response.raise_for_status()
            order_response = response.json()
            self.logger.info(f"[API] 注文送信成功: {order_response}")
            return True, order_response
        except requests.RequestException as e:
            self.logger.error(f"[ERROR] 注文送信失敗: {e}")
            if e.response is not None:
                self.logger.error(f"Response content: {e.response.text}")
            return False, str(e)

    def send_buy_order(self, ticker, exchange, qty, trade_password, price):
        """買い注文を送信する"""
        url = f"{self.api_url}/sendorder"
        headers = {'Content-Type': 'application/json', 'X-API-KEY': self.token}
        payload = {
            'Password': trade_password,
            'Symbol': str(ticker),
            'Exchange': exchange,
            'SecurityType': 1,
            'Side': '2', # 2 for Buy
            'CashMargin': 1, # 1 for 現物
            'DelivType': 2,
            'FundType': 'AA',
            'AccountType': 4,
            'Qty': qty,
            'FrontOrderType': 20,
            'Price': price,
            'ExpireDay': 0
        }
        try:
            response = requests.post(url, data=json.dumps(payload), headers=headers)
            response.raise_for_status()
            order_response = response.json()
            self.logger.info(f"[API] 買い注文送信成功: {order_response}")
            return True, order_response
        except requests.RequestException as e:
            self.logger.error(f"[ERROR] 買い注文送信失敗: {e}")
            if e.response is not None:
                self.logger.error(f"Response content: {e.response.text}")
            return False, str(e)

    def send_sell_order(self, ticker, exchange, qty, trade_password):
        """売り注文を送信する"""
        url = f"{self.api_url}/sendorder"
        headers = {'Content-Type': 'application/json', 'X-API-KEY': self.token}
        payload = {
            'Password': trade_password,
            'Symbol': str(ticker),
            'Exchange': exchange,
            'SecurityType': 1,
            'Side': '1',  # 1 for Sell
            'CashMargin': 1,  # 1 for 現物
            'DelivType': 2,
            'FundType': 'AA',
            'AccountType': 4,
            'Qty': qty,
            'FrontOrderType': 10,  # 10 for Market Order
            'Price': 0,
            'ExpireDay': 0
        }
        try:
            response = requests.post(url, data=json.dumps(payload), headers=headers)
            response.raise_for_status()
            order_response = response.json()
            self.logger.info(f"[API] 売り注文送信成功: {order_response}")
            return True, order_response
        except requests.RequestException as e:
            self.logger.error(f"[ERROR] 売り注文送信失敗: {e}")
            if e.response is not None:
                self.logger.error(f"Response content: {e.response.text}")
            return False, str(e)

    def send_stop_loss_sell_order(self, ticker, exchange, qty, trade_password, trigger_price):
        """逆指値売り注文（損切り）を送信する"""
        url = f"{self.api_url}/sendorder"
        headers = {'Content-Type': 'application/json', 'X-API-KEY': self.token}
        payload = {
            'Password': trade_password,
            'Symbol': str(ticker),
            'Exchange': exchange,
            'SecurityType': 1,
            'Side': '1',  # 1 for Sell
            'CashMargin': 1,  # 1 for 現物
            'DelivType': 2,
            'FundType': 'AA',
            'AccountType': 4,
            'Qty': qty,
            'FrontOrderType': 30,  # 30 for Stop-loss order (逆指値)
            'Price': 0,  # 成行なので0
            'ExpireDay': 0,
            'ReverseLimitOrder': {
                'TriggerSec': 1,  # 注文銘柄自身でトリガー
                'TriggerPrice': trigger_price,
                'UnderOver': 1,  # 以下でトリガー
                'AfterHitOrderType': 1,  # トリガー後、成行で発注
                'AfterHitPrice': 0  # 成行なので0
            }
        }
        try:
            response = requests.post(url, data=json.dumps(payload), headers=headers)
            response.raise_for_status()
            order_response = response.json()
            self.logger.info(f"[API] 逆指値売り注文送信成功: {order_response}")
            return True, order_response
        except requests.RequestException as e:
            self.logger.error(f"[ERROR] 逆指値売り注文送信失敗: {e}")
            if e.response is not None:
                self.logger.error(f"Response content: {e.response.text}")
            return False, str(e)

    
