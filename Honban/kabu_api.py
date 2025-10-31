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
        self.trade_password = config['SECRETS']['TRADE_PASSWORD']
        self.trade_type = config.get('TRADE_SETTINGS', 'TRADE_TYPE', fallback='physical')
        
        self.api_protocol = config.get('API_SETTINGS', 'PROTOCOL', fallback='http')
        self.api_port = config.get('API_SETTINGS', 'PORT', fallback='18080')
        self.api_url = f"{self.api_protocol}://localhost:{self.api_port}/kabusapi"
        
        ws_protocol = 'wss' if self.api_protocol == 'https' else 'ws'
        self.ws_url = f"{ws_protocol}://localhost:{self.api_port}/kabusapi/websocket"
        
        self.token = None
        self.ws = None
        self.ws_thread = None

        if logger is None:
            self.logger = logging.getLogger(__name__)
            self.logger.setLevel(logging.INFO)
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
            # HTTPS接続の場合、証明書検証を無効にする (自己署名証明書対策)
            verify_ssl = self.api_protocol != 'https'
            response = requests.post(url, data=json.dumps(payload), headers={'Content-Type': 'application/json'}, verify=verify_ssl)
            response.raise_for_status()
            self.token = response.json()["Token"]
            self.logger.info(f"[API] トークンの取得に成功しました。")
            return True
        except requests.RequestException as e:
            self.logger.error(f"[ERROR] トークンの取得に失敗しました: {e}")
            if hasattr(e, 'response') and e.response is not None:
                self.logger.error(f"Response content: {e.response.text}")
            return False

    def _send_order(self, payload):
        """注文送信の共通ロジック"""
        url = f"{self.api_url}/sendorder"
        headers = {'Content-Type': 'application/json', 'X-API-KEY': self.token}
        try:
            verify_ssl = self.api_protocol != 'https'
            response = requests.post(url, json=payload, headers=headers, verify=verify_ssl)
            response.raise_for_status()
            order_response = response.json()
            self.logger.info(f"[API] 注文送信成功: {order_response}")
            return True, order_response
        except requests.RequestException as e:
            self.logger.error(f"[ERROR] 注文送信失敗: {e}")
            if hasattr(e, 'response') and e.response is not None:
                self.logger.error(f"Response content: {e.response.text}")
                try:
                    return False, e.response.json()
                except json.JSONDecodeError:
                    return False, e.response.text
            return False, str(e)

    def send_short_sell_order(self, symbol, exchange, qty, password):
        """空売り注文を送信する"""
        payload = {
            "Password": password,
            "Symbol": str(symbol),
            "Exchange": exchange,
            "SecurityType": 1,
            "Side": "1",
            "CashMargin": 3,
            "DelivType": 2,
            "FundType": self.trade_type,
            "AccountType": 4,
            "Qty": qty,
            "FrontOrderType": 10,
        }
        return self._send_order(payload)

    def send_market_order(self, symbol, exchange, qty, side):
        """成行注文を送信する"""
        # 売付の場合はFundTypeを'  '（スペース2つ）に、買付の場合は'AA'に設定
        fund_type = "  " if side == "1" else "AA"

        payload = {
            "Password": self.trade_password,
            "Symbol": str(symbol),
            "Exchange": exchange,
            "SecurityType": 1,
            "Side": side,
            "CashMargin": 1,
            "DelivType": 2,
            "FundType": fund_type,
            "AccountType": 4,
            "Qty": qty,
            "FrontOrderType": 10,
            "ExpireDay": 0,
            "Price": 0
        }
        return self._send_order(payload)

    def send_stop_sell_order(self, symbol, exchange, qty, password, trigger_price):
        """逆指値の売り注文を送信する（参考ファイルベースの修正版）"""
        payload = {
            "Password": password,
            "Symbol": str(symbol),
            "Exchange": exchange,
            "SecurityType": 1,
            "Side": "1",
            "CashMargin": 1,
            "DelivType": 0, # 参考ファイルでは0
            "FundType": "  ",
            "AccountType": 4,
            "Qty": qty,
            "FrontOrderType": 30, # 参考ファイルでは30
            "Price": 0, # トリガー後は成行のため0
            "ExpireDay": 0,
            "ReverseLimitOrder": {
                "TriggerSec": 1,
                "TriggerPrice": trigger_price,
                "UnderOver": 1, # 1: 以下
                "AfterHitOrderType": 1, # 1: 成行
                "AfterHitPrice": 0
            }
        }
        return self._send_order(payload)

    def send_limit_sell_order(self, symbol, exchange, qty, password, price):
        """指値の売り注文を送信する"""
        payload = {
            "Password": password,
            "Symbol": str(symbol),
            "Exchange": exchange,
            "SecurityType": 1,
            "Side": "1",
            "CashMargin": 1,
            "DelivType": 2,
            "FundType": "  ",
            "AccountType": 4,
            "Qty": qty,
            "FrontOrderType": 20,
            "Price": price
        }
        return self._send_order(payload)

    def get_orders_list(self, product=None):
        """注文一覧を取得する"""
        url = f"{self.api_url}/orders"
        headers = {'Content-Type': 'application/json', 'X-API-KEY': self.token}
        params = {}
        if product:
            params['product'] = product

        try:
            verify_ssl = self.api_protocol != 'https'
            response = requests.get(url, headers=headers, params=params, verify=verify_ssl)
            response.raise_for_status()
            orders = response.json()
            self.logger.debug(f"[API] 注文一覧取得成功")
            return True, orders
        except requests.RequestException as e:
            self.logger.error(f"[ERROR] 注文一覧取得失敗: {e}")
            if hasattr(e, 'response') and e.response is not None:
                self.logger.error(f"Response content: {e.response.text}")
                try:
                    return False, e.response.json()
                except json.JSONDecodeError:
                    return False, {"Message": e.response.text}
            return False, {"Message": str(e)}

    def get_order(self, order_id):
        """注文情報を取得する"""
        url = f"{self.api_url}/orders"
        headers = {'Content-Type': 'application/json', 'X-API-KEY': self.token}
        params = {'orderid': order_id}
        try:
            verify_ssl = self.api_protocol != 'https'
            response = requests.get(url, headers=headers, params=params, verify=verify_ssl)
            response.raise_for_status()
            order_info_list = response.json()
            
            if not order_info_list:
                self.logger.warning(f"[API] 注文情報取得失敗: OrderID {order_id} が見つかりません。")
                return False, {"Code": 4001012, "Message": "注文が見つかりません"} # 互換性のために元のエラーコードに似せる

            order_info = order_info_list[0]
            self.logger.info(f"[API] 注文情報取得成功: {order_info}")
            return True, order_info
        except requests.RequestException as e:
            self.logger.error(f"[ERROR] 注文情報取得失敗: {e}")
            if hasattr(e, 'response') and e.response is not None:
                self.logger.error(f"Response content: {e.response.text}")
                try:
                    return False, e.response.json()
                except json.JSONDecodeError:
                    return False, e.response.text
            return False, str(e)

    def get_symbol_info(self, symbol, exchange):
        """銘柄情報を取得する"""
        url = f"{self.api_url}/symbol/{symbol}@{exchange}"
        headers = {'Content-Type': 'application/json', 'X-API-KEY': self.token}
        try:
            verify_ssl = self.api_protocol != 'https'
            response = requests.get(url, headers=headers, verify=verify_ssl)
            response.raise_for_status()
            symbol_info = response.json()
            self.logger.info(f"[API] 銘柄情報取得成功: {symbol_info}")
            return True, symbol_info
        except requests.RequestException as e:
            self.logger.error(f"[ERROR] 銘柄情報取得失敗: {e}")
            if hasattr(e, 'response') and e.response is not None:
                self.logger.error(f"Response content: {e.response.text}")
            return False, str(e)

    def get_board_info(self, symbol, exchange):
        """板情報を取得する"""
        url = f"{self.api_url}/board/{symbol}@{exchange}"
        headers = {'Content-Type': 'application/json', 'X-API-KEY': self.token}
        try:
            verify_ssl = self.api_protocol != 'https'
            response = requests.get(url, headers=headers, verify=verify_ssl)
            response.raise_for_status()
            board_info = response.json()
            self.logger.info(f"[API] 板情報取得成功: {board_info}")
            return True, board_info
        except requests.RequestException as e:
            self.logger.error(f"[ERROR] 板情報取得失敗: {e}")
            if hasattr(e, 'response') and e.response is not None:
                self.logger.error(f"Response content: {e.response.text}")
            return False, str(e)

    def get_physical_positions(self):
        """現物保有銘柄一覧を取得する"""
        url = f"{self.api_url}/wallet/physical"
        headers = {'Content-Type': 'application/json', 'X-API-KEY': self.token}
        try:
            verify_ssl = self.api_protocol != 'https'
            response = requests.get(url, headers=headers, verify=verify_ssl)
            response.raise_for_status()
            positions = response.json()
            self.logger.debug(f"[API] 現物保有銘柄一覧の取得成功")
            return True, positions
        except requests.RequestException as e:
            self.logger.error(f"[ERROR] 現物保有銘柄一覧の取得失敗: {e}")
            if hasattr(e, 'response') and e.response is not None:
                self.logger.error(f"Response content: {e.response.text}")
                try:
                    return False, e.response.json()
                except json.JSONDecodeError:
                    return False, {"Message": e.response.text}
            return False, {"Message": str(e)}

    def register_symbol(self, ticker, exchange):
        """PUSH通知用の銘柄を登録する"""
        url = f"{self.api_url}/register"
        headers = {'Content-Type': 'application/json', 'X-API-KEY': self.token}
        payload = {"Symbols": [{"Symbol": str(ticker), "Exchange": exchange}]}
        try:
            verify_ssl = self.api_protocol != 'https'
            response = requests.put(url, data=json.dumps(payload), headers=headers, verify=verify_ssl)
            response.raise_for_status()
            self.logger.info(f"[API] 銘柄登録に成功しました: {ticker}")
            return True
        except requests.RequestException as e:
            self.logger.error(f"[ERROR] 銘柄登録に失敗しました: {e}")
            return False

    def cancel_order(self, order_id, trade_password):
        """注文をキャンセルする"""
        url = f"{self.api_url}/cancelorder"
        headers = {'Content-Type': 'application/json', 'X-API-KEY': self.token}
        payload = {
            'OrderId': order_id,
            'Password': trade_password
        }
        try:
            verify_ssl = self.api_protocol != 'https'
            response = requests.put(url, data=json.dumps(payload), headers=headers, verify=verify_ssl)
            response.raise_for_status()
            cancel_response = response.json()
            self.logger.info(f"[API] 注文キャンセル成功: {cancel_response}")
            return True, cancel_response
        except requests.RequestException as e:
            self.logger.error(f"[ERROR] 注文キャンセル失敗: {e}")
            if hasattr(e, 'response') and e.response is not None:
                self.logger.error(f"Response content: {e.response.text}")
                try:
                    return False, e.response.json()
                except json.JSONDecodeError:
                    return False, e.response.text
            return False, str(e)

    def connect_websocket(self, on_message_callback, on_error_callback, on_close_callback, on_open_callback):
        """WebSocketに接続し、受信スレッドを開始する"""
        self.logger.info("[INFO] WebSocketに接続します...")
        # wssの場合、証明書検証を無効にする
        ws_options = {"sslopt": {"cert_reqs": 0}} if self.api_protocol == 'https' else {}
        self.ws = websocket.WebSocketApp(self.ws_url,
                                         on_message=on_message_callback,
                                         on_error=on_error_callback,
                                         on_close=on_close_callback,
                                         **ws_options)
        self.ws.on_open = on_open_callback
        self.ws_thread = threading.Thread(target=self.ws.run_forever)
        self.ws_thread.daemon = True
        self.ws_thread.start()

    def close_websocket(self):
        """WebSocket接続を閉じる"""
        if self.ws:
            self.ws.close()
