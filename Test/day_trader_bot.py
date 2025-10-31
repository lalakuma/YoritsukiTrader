import configparser
import time
import json
import pandas as pd
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime, time as dt_time
import logging

from kabu_api import KabuAPI
from line_messaging_api_notifier import line_notify

class DayTraderBot:
    def __init__(self):
        self._setup_logger()
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, 'config.ini')
        config = configparser.ConfigParser()
        config.read(config_path, encoding='utf-8')
        
        self.ticker = config['TRADE_SETTINGS']['TICKER']
        self.exchange = int(config['TRADE_SETTINGS']['EXCHANGE'])
        self.qty = int(config['TRADE_SETTINGS']['QTY'])
        self.auto_trade_enabled = config.getboolean('TRADE_SETTINGS', 'AUTO_TRADE_ENABLED')
        self.trade_password = config['SECRETS']['TRADE_PASSWORD']

        # --- Strategy Parameters ---
        self.stop_loss_percent = 1.5
        self.take_profit_percent = 2.0
        self.entry_offset_ticks = 1

        self.api = KabuAPI(config_path, logger=self.logger)

        # --- State & Data Variables ---
        self.state = 'IDLE'  # IDLE, WAITING_FOR_ENTRY, POSITION_OPEN, WAITING_FOR_CANCEL, CLOSING
        self.is_bot_running = True
        self.current_price = 0
        self.entry_order_id = None
        self.stop_loss_order_id = None
        self.entry_price = 0
        self.entry_time = None
        self.stop_loss_price = 0
        self.take_profit_price = 0
        
        # --- Simplified Signal ---
        self.reversal_point = None # Using a simple price point for signal generation

    def _setup_logger(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(logging.INFO)
        if self.logger.hasHandlers():
            self.logger.handlers.clear()

        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        log_file_path = os.path.join(log_dir, "day_trader_bot.log")
        file_handler = RotatingFileHandler(log_file_path, maxBytes=102400, backupCount=1, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        self.logger.propagate = False

    def _send_line_notification(self, message_lines, subject):
        try:
            line_notify(message_lines, subject, logger=self.logger)
        except TypeError:
            line_notify(message_lines, subject)

    def on_message(self, ws, message):
        data = json.loads(message)
        price = data.get("CurrentPrice")
        if price:
            self.current_price = float(price)

    def on_error(self, ws, error):
        self.logger.error(f"WebSocket error: {error}")
        self.is_bot_running = False

    def on_close(self, ws, close_status_code, close_msg):
        self.logger.info("WebSocket connection closed.")
        self.is_bot_running = False

    def on_open(self, ws):
        self.logger.info("WebSocket connection opened. Registering for price data...")
        if not self.api.register_symbol(self.ticker, self.exchange):
            self.logger.error("Failed to register for price data. Closing WebSocket.")
            self.api.close_websocket()

    def run(self):
        self.logger.info(f"Starting DayTraderBot for {self.ticker}.")
        if not self.auto_trade_enabled:
            self.logger.warning("Auto trading is disabled. Exiting.")
            self._send_line_notification([f"{self.ticker} の自動取引ボットは無効のため起動しません。"], "通知")
            return
            
        self._send_line_notification([f"{self.ticker} の日中取引ボットを起動します。"], "起動")
        
        if not self.api.get_token(): return
        
        self.api.connect_websocket(self.on_message, self.on_error, self.on_close, self.on_open)
        self.logger.info("Waiting for WebSocket connection to receive first price data...")
        while self.current_price == 0 and self.is_bot_running:
            time.sleep(1)
        
        self.logger.info(f"First price received: {self.current_price}. Starting main loop.")

        try:
            while self.is_bot_running:
                now_time = datetime.now().time()
                market_open = dt_time(9, 0)
                market_close_am = dt_time(11, 30)
                
                if not (market_open <= now_time <= market_close_am):
                    self.logger.info(f"Outside market hours ({now_time.strftime('%H:%M:%S')}). Pausing state machine.")
                    time.sleep(60)
                    continue

                if self.state == 'IDLE':
                    self._handle_state_idle()
                elif self.state == 'WAITING_FOR_ENTRY':
                    self._handle_state_waiting_for_entry()
                elif self.state == 'POSITION_OPEN':
                    self._handle_state_position_open()
                elif self.state == 'WAITING_FOR_CANCEL':
                    self._handle_state_waiting_for_cancel()
                elif self.state == 'CLOSING':
                    self._handle_state_closing()
                
                time.sleep(2)

        except KeyboardInterrupt:
            self.logger.info("Manual interruption detected.")
        finally:
            if self.stop_loss_order_id:
                self.logger.info(f"Cleaning up stop loss order: {self.stop_loss_order_id}")
                self.api.cancel_order(self.stop_loss_order_id, self.trade_password)

            self.api.close_websocket()
            self.logger.info("DayTraderBot stopped.")
            self._send_line_notification([f"{self.ticker} の日中取引ボットを停止します。"], "停止")

    def _handle_state_idle(self):
        # Simplified signal detection for demonstration.
        # It sets a reversal point once, then waits for the price to cross it.
        if self.reversal_point is None and self.current_price > 0:
            self.reversal_point = self.current_price + 5 # Set a dummy reversal point 5 ticks above current
            self.logger.info(f"SIMULATED SIGNAL: Reversal point set to {self.reversal_point}")

        if self.reversal_point is not None and self.current_price > self.reversal_point:
            self.logger.info(f"ENTRY SIGNAL: Current price {self.current_price} crossed reversal point {self.reversal_point}")
            
            entry_order_price = self.reversal_point
            self.stop_loss_price = round(entry_order_price * (1 - self.stop_loss_percent / 100))
            self.take_profit_price = round(entry_order_price * (1 + self.take_profit_percent / 100))

            self.logger.info(f"Attempting to place entry order for {self.ticker} at {entry_order_price}")
            
            success, order_info = self.api.send_limit_buy_order(
                self.ticker, self.exchange, self.qty, self.trade_password, entry_order_price
            )

            if success:
                self.entry_order_id = order_info['OrderID']
                self.logger.info(f"Entry order placed successfully. Order ID: {self.entry_order_id}")
                self._send_line_notification([f"【エントリー注文】{self.ticker} 買い", f"価格: {entry_order_price}"], "注文")
                self.state = 'WAITING_FOR_ENTRY'
            else:
                self.logger.error(f"Failed to place entry order: {order_info}")
                self._send_line_notification([f"【エラー】{self.ticker}のエントリー注文に失敗しました。", f"エラー: {order_info}"], "エラー")
                self.is_bot_running = False

    def _handle_state_waiting_for_entry(self):
        self.logger.info(f"Checking status of entry order {self.entry_order_id}...")
        success, order_info = self.api.get_order(self.entry_order_id)
        if not success:
            self.logger.error(f"Failed to get order info for {self.entry_order_id}. Stopping.")
            self.is_bot_running = False
            return

        if order_info and order_info.get('State') == 6: # 6:約定済
            self.entry_price = float(order_info.get('Price'))
            self.entry_time = datetime.now()
            self.logger.info(f"Entry order {self.entry_order_id} executed at {self.entry_price}!")
            self._send_line_notification([f"【エントリー約定】{self.ticker}", f"価格: {self.entry_price}"], "約定")

            self.logger.info(f"Placing stop loss order at {self.stop_loss_price}")
            sl_success, sl_order_info = self.api.send_stop_sell_order(
                self.ticker, self.exchange, self.qty, self.trade_password, self.stop_loss_price
            )
            if sl_success:
                self.stop_loss_order_id = sl_order_info['OrderID']
                self.logger.info(f"Stop loss order placed successfully. Order ID: {self.stop_loss_order_id}")
                self.state = 'POSITION_OPEN'
            else:
                self.logger.error(f"CRITICAL: Failed to place stop loss order after entry! {sl_order_info}")
                self._send_line_notification(["【緊急エラー】エントリー後に損切り注文の発注に失敗しました。手動対応が必要です。"], "エラー")
                self.is_bot_running = False

        elif order_info and order_info.get('State') in [3, 5]: # 3:処理済（発注エラー), 5:取消済
            self.logger.warning(f"Entry order {self.entry_order_id} failed or was cancelled. State: {order_info.get('State')}")
            self.state = 'IDLE'
            self.entry_order_id = None
            self.reversal_point = None # Reset signal

    def _handle_state_position_open(self):
        self.logger.info(f"Position open. SL at {self.stop_loss_price}. Monitoring for TP at {self.take_profit_price}. Current: {self.current_price}")
        
        if self.current_price >= self.take_profit_price:
            self.logger.info(f"Take profit price {self.take_profit_price} reached!")
            self.logger.info(f"Cancelling stop loss order {self.stop_loss_order_id} before taking profit.")
            
            cancel_success, cancel_info = self.api.cancel_order(self.stop_loss_order_id, self.trade_password)
            if cancel_success:
                self.logger.info("Stop loss cancellation request sent successfully.")
                self.state = 'WAITING_FOR_CANCEL'
            else:
                self.logger.error(f"CRITICAL: Failed to send cancellation for stop loss order {self.stop_loss_order_id}. {cancel_info}")
                self._send_line_notification(["【緊急エラー】損切り注文のキャンセルに失敗しました。手動対応が必要です。"], "エラー")
                self.is_bot_running = False

    def _handle_state_waiting_for_cancel(self):
        self.logger.info(f"Checking status of cancelled stop loss order {self.stop_loss_order_id}...")
        success, order_info = self.api.get_order(self.stop_loss_order_id)
        if not success:
            self.logger.error(f"Failed to get order info for {self.stop_loss_order_id}. Stopping.")
            self.is_bot_running = False
            return
        
        if order_info and order_info.get('State') == 5: # 5:取消済
            self.logger.info(f"Stop loss order {self.stop_loss_order_id} confirmed cancelled.")
            self.stop_loss_order_id = None

            self.logger.info("Placing market sell order to take profit.")
            tp_success, tp_order_info = self.api.send_market_sell_order(
                self.ticker, self.exchange, self.qty, self.trade_password
            )
            if tp_success:
                profit = (self.current_price - self.entry_price) * self.qty
                self.logger.info(f"Take profit market order sent successfully. Approx Profit: {profit}")
                self._send_line_notification([f"【決済：利確】{self.ticker}", f"価格: {self.current_price}", f"想定利益: {profit}"], "決済")
                self.state = 'CLOSING'
            else:
                self.logger.error(f"CRITICAL: Failed to place take profit order! {tp_order_info}")
                self._send_line_notification(["【緊急エラー】利確注文の発注に失敗しました。手動対応が必要です。"], "エラー")
                self.is_bot_running = False

        elif order_info and order_info.get('State') == 6: # 6:約定済
            self.logger.warning(f"Stop loss order {self.stop_loss_order_id} was executed before it could be cancelled.")
            profit = (float(order_info.get('Price')) - self.entry_price) * self.qty
            self._send_line_notification([f"【決済：損切り】{self.ticker}", f"価格: {order_info.get('Price')}", f"損益: {profit}"], "決済")
            self.state = 'CLOSING'

    def _handle_state_closing(self):
        self.logger.info("Trade cycle complete. Stopping bot.")
        self.is_bot_running = False

if __name__ == "__main__":
    bot = None
    try:
        bot = DayTraderBot()
        bot.run()
    except Exception as e:
        if 'bot' in locals() and hasattr(bot, 'logger'):
            bot.logger.error("Script terminated due to an unhandled exception", exc_info=True)
        else:
            # Basic logging if logger wasn't even set up
            logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
            logging.error("Script terminated due to an unhandled exception during initialization", exc_info=True)