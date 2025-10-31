import configparser
import time
import json
import pandas as pd
import os
import sqlite3
from pathlib import Path
from logging.handlers import RotatingFileHandler
from datetime import datetime, time as dt_time, timedelta
import logging
import threading

from kabu_api import KabuAPI
from line_messaging_api_notifier import line_notify

class IntradayDipBuyBot:
    def __init__(self):
        self._setup_logger()
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, 'config.ini')
        config = configparser.ConfigParser()
        config.read(config_path, encoding='utf-8')
        
        self.logger.info("--- Loading Configuration ---")
        # --- Config Parameters ---
        self.ticker = config['TRADE_SETTINGS']['TICKER']
        self.exchange = int(config['TRADE_SETTINGS']['EXCHANGE'])
        self.qty = int(config['TRADE_SETTINGS']['QTY'])
        self.auto_trade_enabled = config.getboolean('TRADE_SETTINGS', 'AUTO_TRADE_ENABLED')
        self.trade_password = config['SECRETS']['TRADE_PASSWORD']

        self.setup_timeframe_mins = int(config['INTRADAY_DIP_BUY_PARAMS']['SETUP_TIMEFRAME_MINS'])
        self.trigger_timeframe_mins = int(config['INTRADAY_DIP_BUY_PARAMS']['TRIGGER_TIMEFRAME_MINS'])
        self.stop_loss_percent = float(config['INTRADAY_DIP_BUY_PARAMS']['STOP_LOSS_PERCENT'])
        self.take_profit_percent = float(config['INTRADAY_DIP_BUY_PARAMS']['TAKE_PROFIT_PERCENT'])

        # Notification settings
        self.enable_start_stop_notifications = config.getboolean('NOTIFICATION_SETTINGS', 'ENABLE_START_STOP_NOTIFICATIONS', fallback=True)
        
        self.logger.info(f"TICKER: {self.ticker}, QTY: {self.qty}")
        self.logger.info(f"SETUP_TIMEFRAME: {self.setup_timeframe_mins}min, TRIGGER_TIMEFRAME: {self.trigger_timeframe_mins}min")
        self.logger.info(f"SL: {self.stop_loss_percent}%, TP: {self.take_profit_percent}%")
        self.logger.info(f"AUTO_TRADE_ENABLED: {self.auto_trade_enabled}")

        self.db_path = Path("C:/share/MorinoFolder/Python/KabuRadar/DB/KabuRadar.db")
        self.db_table_name = f"tbl_{self.ticker}_min"

        self.api = KabuAPI(config_path, logger=self.logger)

        # --- State & Data Variables ---
        self.state = 'IDLE'
        self.is_bot_running = True
        self.current_price = 0
        self.entry_order_id = None
        self.stop_loss_order_id = None
        self.entry_price = 0
        self.entry_time = None
        self.stop_loss_price = 0
        self.take_profit_price = 0
        
        # --- Strategy Specific State ---
        self.df_1min = pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])
        self.ticks_in_current_bar = []
        self.last_bar_timestamp = None
        self.dip_flag_on = False
        self.lowest_price_value = float('inf')
        self.lowest_price_bar_index = -1
        self.reversal_point = None
        self.dip_start_timestamp = None
        self.ticks_lock = threading.Lock()
        self.entry_order_check_retries = 0
        self.logger.info("--- Bot Initialized ---")

        self.last_market_status_logged = None
        self.initial_price_wait_logged = False
        self.last_price_log_time = time.time()

    def _setup_logger(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(logging.DEBUG)
        if self.logger.hasHandlers():
            self.logger.handlers.clear()
        log_dir = os.path.join(os.path.dirname(__file__), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file_path = os.path.join(log_dir, "intraday_dip_buy_bot.log")
        file_handler = RotatingFileHandler(log_file_path, maxBytes=1024000, backupCount=5, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        self.logger.propagate = False

    def _send_line_notification(self, message_lines, subject):
        try:
            self.logger.info(f"Sending LINE notification: Subject='{subject}', Message='{message_lines}'")
            line_notify(message_lines, subject, logger=self.logger)
        except TypeError:
            self.logger.error(f"Failed to send LINE notification (TypeError): Subject='{subject}', Message='{message_lines}'")
            line_notify(message_lines, subject)

    def _load_historical_data(self):
        self.logger.info("Loading recent historical data for initial setup...")
        try:
            conn = sqlite3.connect(self.db_path)
            query = f"SELECT * FROM {self.db_table_name} WHERE Datetime >= '{(datetime.now() - timedelta(minutes=120)).strftime('%Y-%m-%d %H:%M:%S')}'"
            self.logger.debug(f"Executing historical data query: {query}")
            df = pd.read_sql_query(query, conn, index_col='Datetime', parse_dates=['Datetime'])
            conn.close()
            df.sort_index(inplace=True)
            self.df_1min = df
            self.logger.info(f"Loaded {len(self.df_1min)} rows of recent 1-min data.")
        except Exception as e:
            self.logger.error(f"Failed to load historical data: {e}", exc_info=True)

    def _save_bar_to_db(self, bar_df):
        """Saves a new 1-minute bar to the SQLite database."""
        try:
            conn = sqlite3.connect(self.db_path)
            bar_df.index.name = 'Datetime'
            bar_df.to_sql(self.db_table_name, conn, if_exists='append', index=True)
            conn.close()
            self.logger.info(f"Saved new bar to database: {self.db_table_name}")
        except Exception as e:
            self.logger.error(f"Failed to save bar to database: {e}", exc_info=True)

    def _aggregate_ticks(self):
        with self.ticks_lock:
            ticks_to_process = self.ticks_in_current_bar
            self.ticks_in_current_bar = []

        if not ticks_to_process:
            self.logger.debug("No ticks in current bar to aggregate.")
            return False
        self.logger.debug("Aggregating ticks to new 1-min bar...")
        df_ticks = pd.DataFrame(ticks_to_process, columns=['Timestamp', 'Price'])
        df_ticks['Timestamp'] = pd.to_datetime(df_ticks['Timestamp'])
        
        bar_open = df_ticks['Price'].iloc[0]
        bar_high = df_ticks['Price'].max()
        bar_low = df_ticks['Price'].min()
        bar_close = df_ticks['Price'].iloc[-1]
        
        new_bar = pd.DataFrame([{
            'Open': bar_open, 'High': bar_high, 'Low': bar_low, 'Close': bar_close, 'Volume': 0
        }], index=[self.last_bar_timestamp])
        
        self.df_1min = pd.concat([self.df_1min, new_bar])
        self._save_bar_to_db(new_bar)
        self.logger.info(f"New 1-min bar aggregated: O={new_bar['Open'].iloc[0]} H={new_bar['High'].iloc[0]} L={new_bar['Low'].iloc[0]} C={new_bar['Close'].iloc[0]}")
        return True

    def _update_setup_signal(self):
        self.logger.debug("Updating setup signal...")
        if len(self.df_1min) < self.setup_timeframe_mins * 3:
            self.logger.debug("Not enough data to check for setup signal.")
            return

        setup_resample_period = f'{self.setup_timeframe_mins}T'
        df_setup = self.df_1min.resample(setup_resample_period, label='right', closed='right').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last'
        }).dropna()

        # 9:00のバーは不完全なデータなので除外する
        df_setup = df_setup[df_setup.index.time != dt_time(9, 0)]

        if len(df_setup) < 3:
            self.logger.debug(f"Not enough resampled bars to check for setup signal (need 3, have {len(df_setup)}).")
            return

        self.logger.debug(f"Checking for setup signal on {len(df_setup)} resampled bars.")

        for j in range(len(df_setup)):
            # 1. Detect dip condition
            if j >= 2:
                close_j = df_setup.iloc[j]['Close']
                close_j_1 = df_setup.iloc[j-1]['Close']
                close_j_2 = df_setup.iloc[j-2]['Close']
                self.logger.debug(f"Setup signal check: C={close_j}, C-1={close_j_1}, C-2={close_j_2}")
                if (close_j < close_j_1) and (close_j_1 < close_j_2):
                    if not self.dip_flag_on:
                        self.dip_flag_on = True
                        self.dip_start_timestamp = df_setup.index[j]
                        self.logger.info(f"DIP FLAG ON at {self.dip_start_timestamp.time()}. Initiating search for lowest price.")
                        # Reset lowest price search state whenever a new dip sequence starts
                        self.lowest_price_value = float('inf')
                        self.lowest_price_bar_index = -1
            
            # 2. If dip mode is active, find the lowest price and set reversal point
            if self.dip_flag_on:
                # Ensure we only process bars at or after the dip started
                if self.dip_start_timestamp and df_setup.index[j] >= self.dip_start_timestamp:
                    self.logger.debug(f"Dip flag is ON. Checking lowest price: current Low={df_setup.iloc[j]['Low']}, stored lowest={self.lowest_price_value}")
                    if df_setup.iloc[j]['Low'] < self.lowest_price_value:
                        self.lowest_price_value = df_setup.iloc[j]['Low']
                        self.lowest_price_bar_index = j
                        self.logger.info(f"New lowest price bar found at {df_setup.index[j].time()}, Low: {self.lowest_price_value}")
                
                # Check to set reversal point. This can happen on any iteration 'j' after a low is found.
                if self.lowest_price_bar_index != -1 and j >= self.lowest_price_bar_index + 2:
                    if self.lowest_price_bar_index >= 2:
                        reversal_point_candidate = df_setup.iloc[self.lowest_price_bar_index - 2]['High']
                        if self.reversal_point != reversal_point_candidate:
                            self.reversal_point = reversal_point_candidate
                            self.logger.info(f"REVERSAL POINT SET: {self.reversal_point} (High of bar 2 bars before lowest. Lowest bar time: {df_setup.index[self.lowest_price_bar_index].time()})")
                    else:
                        # This warning should now only appear if a valid dip happens but there aren't 2 prior bars in the whole dataset.
                        self.logger.warning(f"Cannot set reversal point: not enough bars before the lowest price bar (index: {self.lowest_price_bar_index}).")
    
    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            price = data.get("CurrentPrice")
            self.logger.debug(f"Received raw message: {message}")
            if price is None:
                price = data.get("CalcPrice")
                if price is not None:
                    self.logger.debug(f"CurrentPrice is null, using CalcPrice: {price}")
                else:
                    self.logger.debug(f"CurrentPrice and CalcPrice are both null in message: {message}")

            if price:
                self.current_price = float(price)
                with self.ticks_lock:
                    self.ticks_in_current_bar.append((datetime.now(), self.current_price))
                self.logger.debug(f"Received price: {self.current_price} at {datetime.now().strftime('%H:%M:%S.%f')}")
                now = time.time()
                if now - self.last_price_log_time > 10:
                    self.logger.info(f"Price updated to {self.current_price}")
                    self.last_price_log_time = now
                self.initial_price_wait_logged = False
            else:
                self.logger.debug(f"Message received but no valid price (CurrentPrice or CalcPrice): {message}")
        except Exception as e:
            self.logger.error(f"Error in on_message: {e}", exc_info=True)

    def on_error(self, ws, error):
        self.logger.error(f"WebSocket error: {error}", exc_info=True)
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
        self.logger.info(f"--- DayTraderBot STARTED for {self.ticker} ---")
        exit_reason = "Unknown"
        try:
            if not self.auto_trade_enabled:
                self.logger.warning("Auto trading is disabled in config.ini. Exiting.")
                if self.enable_start_stop_notifications:
                    self._send_line_notification([f"{self.ticker} の自動取引ボットは無効のため起動しません。"], "通知")
                exit_reason = "Auto trade disabled in config."
                return
            
            if self.enable_start_stop_notifications:
                self._send_line_notification([f"{self.ticker} の日中取引ボットを起動します。"], "起動")
            
            if not self.api.get_token():
                self.logger.error("Failed to get API token.")
                exit_reason = "Failed to get API token."
                return
            self._load_historical_data()
            self.api.connect_websocket(self.on_message, self.on_error, self.on_close, self.on_open)
            self.logger.info("Waiting for WebSocket connection to receive first price data...")
            while self.current_price == 0 and self.is_bot_running:
                if not self.initial_price_wait_logged:
                    self.logger.debug("Waiting for initial price data...")
                    self.initial_price_wait_logged = True
                time.sleep(1)
            if not self.is_bot_running:
                self.logger.error("WebSocket connection failed or closed during startup.")
                exit_reason = "WebSocket connection failed or closed during startup."
                return
            self.logger.info(f"First price received: {self.current_price}. Starting main loop.")
            self.last_bar_timestamp = datetime.now().replace(second=0, microsecond=0)
            while self.is_bot_running:
                now = datetime.now()
                now_time = now.time()
                market_open_am = dt_time(9, 0)
                market_close_am = dt_time(11, 30)
                market_open_pm = dt_time(12, 30)
                market_close_pm = dt_time(15, 0)

                is_market_open = (
                    (market_open_am <= now_time <= market_close_am) or
                    (market_open_pm <= now_time <= market_close_pm)
                )

                current_market_status = 'open' if is_market_open else 'closed'

                if current_market_status != self.last_market_status_logged:
                    if is_market_open:
                        self.logger.info(f"Market is now OPEN. Starting active monitoring. Current time: {now_time.strftime('%H:%M:%S')}")
                    else:
                        self.logger.info(f"Market is now CLOSED. Pausing state machine. Current time: {now_time.strftime('%H:%M:%S')}")
                    self.last_market_status_logged = current_market_status

                if now_time >= dt_time(15, 30) and self.state == 'IDLE':
                    self.logger.info(f"Market close time (15:30) reached and no open position. Shutting down.")
                    exit_reason = "Market close and no open position."
                    self.is_bot_running = False
                    break

                if not is_market_open:
                    if self.state != 'IDLE':
                        self.logger.info(f"Market is closed, but position is open. Continuing to monitor. Current time: {now_time.strftime('%H:%M:%S')}")
                    time.sleep(30)
                    continue
                
                if now.replace(second=0, microsecond=0) > self.last_bar_timestamp:
                    self.logger.debug(f"New minute detected: {now.strftime('%H:%M')}. Aggregating ticks.")
                    is_new_bar = self._aggregate_ticks()
                    if is_new_bar:
                        self.logger.info(f"New 1-min bar processed at {now.strftime('%H:%M')}. Current 1-min data length: {len(self.df_1min)}")
                        self._update_setup_signal()
                        
                        if self.state == 'IDLE':
                            if self.reversal_point is not None:
                                trigger_resample_period = f'{self.trigger_timeframe_mins}T'
                                df_trigger = self.df_1min.resample(trigger_resample_period, label='right', closed='right').agg({
                                    'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last'
                                }).dropna()
                                
                                if not df_trigger.empty:
                                    last_trigger_bar_close = df_trigger.iloc[-1]['Close']
                                    self.logger.info(f"Checking entry trigger: Last trigger bar close ({last_trigger_bar_close}) vs Reversal Point ({self.reversal_point})")
                                    if last_trigger_bar_close > self.reversal_point:
                                        self.logger.info(f"Entry trigger condition met! Last close ({last_trigger_bar_close}) > Reversal Point ({self.reversal_point})")
                                        self._trigger_entry(last_trigger_bar_close)
                                    else:
                                        self.logger.debug("Entry trigger condition NOT met.")
                                else:
                                    self.logger.debug("df_trigger is empty, cannot check entry trigger.")
                            else:
                                self.logger.debug("Reversal point not set yet, cannot check entry trigger.")
                        elif self.state == 'POSITION_OPEN':
                            self.logger.debug("Position is open. Monitoring for exit conditions.")
                    self.last_bar_timestamp = now.replace(second=0, microsecond=0)
                
                if self.state == 'WAITING_FOR_ENTRY':
                    self._handle_state_waiting_for_entry()
                elif self.state == 'POSITION_OPEN':
                    self._handle_state_position_open()
                elif self.state == 'WAITING_FOR_CANCEL':
                    self._handle_state_waiting_for_cancel()
                elif self.state == 'CLOSING':
                    self._handle_state_closing()
                
                time.sleep(1)
            exit_reason = "is_bot_running flag became false (e.g. WebSocket error or critical API failure)."
        except KeyboardInterrupt:
            self.logger.info("Manual interruption detected.")
            exit_reason = "Manual interruption (KeyboardInterrupt)."
        except Exception as e:
            self.logger.error(f"An unexpected error occurred in the run loop: {e}", exc_info=True)
            exit_reason = f"Unexpected error: {e}"
        finally:
            self.logger.info(f"EXIT REASON: {exit_reason}")
            self.logger.info("--- Bot shutting down... ---")
            if self.entry_order_id and self.state == 'WAITING_FOR_ENTRY':
                self.logger.info(f"Cleaning up pending entry order: {self.entry_order_id}")
                self.api.cancel_order(self.entry_order_id, self.trade_password)
            if self.stop_loss_order_id:
                self.logger.info(f"Cleaning up stop loss order: {self.stop_loss_order_id}")
                self.api.cancel_order(self.stop_loss_order_id, self.trade_password)
            self.api.close_websocket()
            self.logger.info("--- DayTraderBot STOPPED ---")
            if self.enable_start_stop_notifications:
                self._send_line_notification([f"{self.ticker} の日中取引ボットを停止します。理由: {exit_reason}"], "停止")

    def _trigger_entry(self, trigger_price):
        self.logger.info(f"ENTRY SIGNAL: 1-min bar close {trigger_price} crossed reversal point {self.reversal_point}")
        self.logger.info(f"Attempting to place MARKET BUY order for {self.ticker}")
        success, order_info = self.api.send_market_order(
            self.ticker, self.exchange, self.qty, "2" # side 2: BUY
        )
        if success:
            self.entry_order_id = order_info['OrderId']
            self.logger.info(f"Market buy order placed successfully. Order ID: {self.entry_order_id}")
            self._send_line_notification([f"【エントリー注文】{self.ticker} 押し目買い (成行)"], "注文")
            self.state = 'WAITING_FOR_ENTRY'
            self.entry_order_check_retries = 0 # Reset retry counter
            self.logger.info("==> STATE: WAITING_FOR_ENTRY")
            time.sleep(2) # 約定情報がAPIに反映されるのを待つ
            self.reversal_point = None
            self.dip_flag_on = False
            self.lowest_price_value = float('inf')
            self.lowest_price_bar_index = -1
            self.dip_start_timestamp = None
        else:
            self.logger.error(f"Failed to place entry order: {order_info}", exc_info=True)
            self._send_line_notification([f"【エラー】{self.ticker}のエントリー注文に失敗しました。", f"エラー: {order_info}"], "エラー")
            self.is_bot_running = False

    def _handle_state_waiting_for_entry(self):
        self.logger.debug(f"Checking execution for order {self.entry_order_id} by fetching all orders...")
        success, orders = self.api.get_orders_list()

        if not success:
            self.logger.error(f"Failed to get orders list. Stopping bot. Error: {orders}")
            self.is_bot_running = False
            return

        found_order = None
        for order in orders:
            if order.get('ID') == self.entry_order_id:
                found_order = order
                break

        if found_order:
            # 注文が見つかった場合、その状態を確認する
            order_state = found_order.get('State')
            if order_state in [5, 6]: # 5:終了(全約定/取消済), 6:約定
                # 約定済みの場合、約定価格を取得する
                execution_price = 0
                if found_order.get('Details'):
                    for detail in found_order['Details']:
                        if detail.get('RecType') == 8: # 8:約定
                            execution_price = detail.get('Price', 0)
                            break
                
                if execution_price > 0:
                    self.entry_price = execution_price
                    self.entry_time = datetime.now()
                    self.logger.info(f"Entry order {self.entry_order_id} executed at {self.entry_price}!")
                    self._send_line_notification([f"【エントリー約定】{self.ticker}", f"価格: {self.entry_price}"], "約定")

                    # SL/TPを計算し、損切り注文を出す
                    self.stop_loss_price = round(self.entry_price * (1 - self.stop_loss_percent / 100))
                    self.take_profit_price = round(self.entry_price * (1 + self.take_profit_percent / 100))
                    self.logger.info(f"SL set to {self.stop_loss_price}, TP set to {self.take_profit_price}")
                    
                    sl_success, sl_order_info = self.api.send_stop_sell_order(
                        self.ticker, self.exchange, self.qty, self.trade_password, self.stop_loss_price
                    )
                    
                    if sl_success:
                        self.stop_loss_order_id = sl_order_info['OrderId']
                        self.logger.info(f"Stop loss order placed successfully. Order ID: {self.stop_loss_order_id}")
                        self.state = 'POSITION_OPEN'
                        self.logger.info("==> STATE: POSITION_OPEN")
                    else:
                        self.logger.error(f"CRITICAL: Failed to place stop loss order after entry! {sl_order_info}", exc_info=True)
                        self._send_line_notification(["【緊急エラー】エントリー後に損切り注文の発注に失敗しました。手動対応が必要です。"], "エラー")
                        self.is_bot_running = False
                    return
                else:
                    self.logger.warning(f"Order {self.entry_order_id} is executed but execution price is zero. Retrying...")

            elif order_state in [3, 5]: # 3:待機, 5:終了(注文失敗)
                 self.logger.warning(f"Entry order {self.entry_order_id} failed or was cancelled. State: {order_state}")
                 self.state = 'IDLE'
                 self.logger.info("==> STATE: IDLE")
                 self.entry_order_id = None
                 return

        # 注文が見つからない場合のリトライ処理
        if self.entry_order_check_retries < 10:
            self.entry_order_check_retries += 1
            self.logger.info(f"Order {self.entry_order_id} not yet found in orders list. Retrying... ({self.entry_order_check_retries}/10)")
            time.sleep(1)
        else:
            self.logger.error(f"CRITICAL: Order {self.entry_order_id} not found after 10 retries. Assuming order failed.")
            self._send_line_notification([f"【緊急エラー】{self.ticker}の注文がリストに見つかりませんでした。手動確認が必要です。"], "エラー")
            self.state = 'IDLE'
            self.logger.info("==> STATE: IDLE")

    def _handle_state_position_open(self):
        self.logger.debug(f"Position open. SL={self.stop_loss_price}, TP={self.take_profit_price}. Current={self.current_price}")
        
        # 1. 利確価格に達したかチェック
        if self.current_price >= self.take_profit_price:
            self.logger.info(f"Take profit price {self.take_profit_price} reached! Current price: {self.current_price}")
            self.logger.info(f"Cancelling stop loss order {self.stop_loss_order_id} before taking profit.")
            cancel_success, cancel_info = self.api.cancel_order(self.stop_loss_order_id, self.trade_password)
            if cancel_success:
                self.logger.info("Stop loss cancellation request sent successfully.")
                self.state = 'WAITING_FOR_CANCEL'
                self.logger.info("==> STATE: WAITING_FOR_CANCEL")
            else:
                self.logger.error(f"CRITICAL: Failed to send cancellation for stop loss order {self.stop_loss_order_id}. {cancel_info}", exc_info=True)
                self._send_line_notification(["【緊急エラー】損切り注文のキャンセルに失敗しました。手動対応が必要です。"], "エラー")
                self.is_bot_running = False
            return # 次のループでキャンセル状態を処理する

        # 2. 損切りが約定したかチェック（全注文リストから確認）
        success, orders = self.api.get_orders_list()
        if not success:
            self.logger.warning("Could not get orders list to check for stop loss execution. Will retry on next tick.")
            return

        for order in orders:
            if order.get('ID') == self.stop_loss_order_id:
                if order.get('State') in [5, 6]: # 5:終了, 6:約定
                    execution_price = 0
                    if order.get('Details'):
                        for detail in order['Details']:
                            if detail.get('RecType') == 8: # 8:約定
                                execution_price = detail.get('Price', 0)
                                break
                    
                    if execution_price > 0:
                        self.logger.warning(f"Stop loss order {self.stop_loss_order_id} was executed at {execution_price}.")
                        profit = (execution_price - self.entry_price) * self.qty
                        self._send_line_notification([f"【決済：損切り】{self.ticker}", f"価格: {execution_price}", f"損益: {profit}"], "決済")
                        self.state = 'CLOSING'
                        self.logger.info("==> STATE: CLOSING")
                break # 該当注文を見つけたらループを抜ける

    def _handle_state_waiting_for_cancel(self):
        self.logger.debug(f"Checking status of cancelled stop loss order {self.stop_loss_order_id}...")
        success, orders = self.api.get_orders_list()

        if not success:
            self.logger.error(f"Failed to get orders list while waiting for cancel confirmation. Stopping.", exc_info=True)
            self.is_bot_running = False
            return

        found_order = None
        for order in orders:
            if order.get('ID') == self.stop_loss_order_id:
                found_order = order
                break
        
        if not found_order:
            self.logger.error(f"Could not find stop loss order {self.stop_loss_order_id} in list. Stopping.", exc_info=True)
            self.is_bot_running = False
            return

        order_state = found_order.get('State')
        if order_state == 5: # 5:終了(取消済)
            self.logger.info(f"Stop loss order {self.stop_loss_order_id} confirmed cancelled. State: {order_state}")
            self.stop_loss_order_id = None
            self.logger.info(f"Placing limit sell order to take profit at {self.take_profit_price}.")
            tp_success, tp_order_info = self.api.send_limit_sell_order(
                self.ticker, self.exchange, self.qty, self.trade_password, self.take_profit_price
            )
            if tp_success:
                profit = (self.take_profit_price - self.entry_price) * self.qty
                self.logger.info(f"Take profit limit order sent successfully. Order ID: {tp_order_info.get('OrderId')}. Approx Profit: {profit}")
                self._send_line_notification([f"【決済：利確(指値)】{self.ticker}", f"価格: {self.take_profit_price}"], "決済")
                self.state = 'CLOSING'
                self.logger.info("==> STATE: CLOSING")
            else:
                self.logger.error(f"CRITICAL: Failed to place take profit order! {tp_order_info}", exc_info=True)
                self._send_line_notification(["【緊急エラー】利確注文の発注に失敗しました。手動対応が必要です。"], "エラー")
                self.is_bot_running = False
        
        elif order_state == 6: # 6:約定
            self.logger.warning(f"Stop loss order {self.stop_loss_order_id} was executed before it could be cancelled. State: {order_state}")
            execution_price = 0
            if found_order.get('Details'):
                for detail in found_order['Details']:
                    if detail.get('RecType') == 8:
                        execution_price = detail.get('Price', 0)
                        break
            profit = (execution_price - self.entry_price) * self.qty
            self._send_line_notification([f"【決済：損切り】{self.ticker}", f"価格: {execution_price}", f"損益: {profit}"], "決済")
            self.state = 'CLOSING'
            self.logger.info("==> STATE: CLOSING")
        else:
            self.logger.info(f"Stop loss order {self.stop_loss_order_id} is still in state {order_state}. Waiting for cancellation to complete...")

    def _handle_state_closing(self):
        self.logger.info("Trade cycle complete. Resetting for next opportunity.")
        self.state = 'IDLE'
        self.logger.info("==> STATE: IDLE")
        self.entry_order_id = None
        self.stop_loss_order_id = None
        self.entry_price = 0
        self.entry_time = None
        self.reversal_point = None
        self.dip_flag_on = False
        self.lowest_price_value = float('inf')
        self.lowest_price_bar_index = -1
        self.dip_start_timestamp = None

if __name__ == "__main__":
    bot = None
    try:
        bot = IntradayDipBuyBot()
        bot.run()
    except Exception as e:
        if bot and hasattr(bot, 'logger'):
            bot.logger.error("Script terminated due to an unhandled exception", exc_info=True)
        else:
            logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
            logging.error("Script terminated due to an unhandled exception during initialization", exc_info=True)