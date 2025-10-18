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
        if not self.ticks_in_current_bar:
            self.logger.debug("No ticks in current bar to aggregate.")
            return False
        self.logger.debug("Aggregating ticks to new 1-min bar...")
        df_ticks = pd.DataFrame(self.ticks_in_current_bar, columns=['Timestamp', 'Price'])
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
        self.ticks_in_current_bar = []
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

        if len(df_setup) < 3:
            self.logger.debug(f"Not enough resampled bars to check for setup signal (need 3, have {len(df_setup)}).")
            return

        self.logger.debug(f"Checking for setup signal on {len(df_setup)} resampled bars.")

        for j in range(len(df_setup)):
            if j >= 2:
                close_j = df_setup.iloc[j]['Close']
                close_j_1 = df_setup.iloc[j-1]['Close']
                close_j_2 = df_setup.iloc[j-2]['Close']
                self.logger.debug(f"Setup signal check: C={close_j}, C-1={close_j_1}, C-2={close_j_2}")
                if (close_j < close_j_1) and (close_j_1 < close_j_2):
                    if not self.dip_flag_on:
                        self.logger.info(f"DIP FLAG ON at {df_setup.index[j].time()} based on 2 lower closes.")
                        self.dip_flag_on = True
            
            if self.dip_flag_on:
                self.logger.debug(f"Dip flag is ON. Checking lowest price: current Low={df_setup.iloc[j]['Low']}, stored lowest={self.lowest_price_value}")
                if df_setup.iloc[j]['Low'] < self.lowest_price_value:
                    self.lowest_price_value = df_setup.iloc[j]['Low']
                    self.lowest_price_bar_index = j
                    self.logger.info(f"New lowest price bar found at {df_setup.index[j].time()}, Low: {self.lowest_price_value}")
                
                if self.lowest_price_bar_index != -1 and j >= self.lowest_price_bar_index + 2:
                    reversal_point_candidate = df_setup.iloc[self.lowest_price_bar_index]['High']
                    if self.reversal_point != reversal_point_candidate:
                        self.reversal_point = reversal_point_candidate
                        self.logger.info(f"REVERSAL POINT SET: {self.reversal_point} (High of bar at {df_setup.index[self.lowest_price_bar_index].time()}) ")
    
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
            self.logger.info("==> STATE: WAITING_FOR_ENTRY")
            self.reversal_point = None
            self.dip_flag_on = False
            self.lowest_price_value = float('inf')
            self.lowest_price_bar_index = -1
        else:
            self.logger.error(f"Failed to place entry order: {order_info}", exc_info=True)
            self._send_line_notification([f"【エラー】{self.ticker}のエントリー注文に失敗しました。", f"エラー: {order_info}"], "エラー")
            self.is_bot_running = False

    def _handle_state_waiting_for_entry(self):
        self.logger.debug(f"Checking status of entry order {self.entry_order_id}...")
        success, order_info = self.api.get_order(self.entry_order_id)
        if not success:
            self.logger.error(f"Failed to get order info for {self.entry_order_id}. Stopping.", exc_info=True)
            self.is_bot_running = False
            return
        # State 6: 約定, State 5: 終了（全約定含む）. Price > 0 で約定と判断
        if order_info and order_info.get('State') in [5, 6] and order_info.get('Price', 0) > 0:
            self.entry_price = float(order_info.get('Price'))
            self.entry_time = datetime.now()
            self.logger.info(f"Entry order {self.entry_order_id} executed at {self.entry_price}! (State: {order_info.get('State')})")
            self._send_line_notification([f"【エントリー約定】{self.ticker}", f"価格: {self.entry_price}"], "約定")
            self.stop_loss_price = round(self.entry_price * (1 - self.stop_loss_percent / 100))
            self.take_profit_price = round(self.entry_price * (1 + self.take_profit_percent / 100))
            self.logger.info(f"SL set to {self.stop_loss_price}, TP set to {self.take_profit_price}")
            self.logger.info(f"Placing stop loss order at {self.stop_loss_price}")
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
        # State 3: 待機（発注待機・訂正待機・取消待機）, State 5: 失敗・キャンセル
        elif order_info and order_info.get('State') in [3, 5]:
            self.logger.warning(f"Entry order {self.entry_order_id} failed or was cancelled. State: {order_info.get('State')}")
            self.state = 'IDLE'
            self.logger.info("==> STATE: IDLE")
            self.entry_order_id = None

    def _handle_state_position_open(self):
        self.logger.debug(f"Position open. SL={self.stop_loss_price}, TP={self.take_profit_price}. Current={self.current_price}")
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
        success, order_info = self.api.get_order(self.stop_loss_order_id)
        if success and order_info and order_info.get('State') == 6:
            self.logger.warning(f"Stop loss order {self.stop_loss_order_id} was executed. State: {order_info.get('State')}")
            profit = (float(order_info.get('Price')) - self.entry_price) * self.qty
            self._send_line_notification([f"【決済：損切り】{self.ticker}", f"価格: {order_info.get('Price')}", f"損益: {profit}"], "決済")
            self.state = 'CLOSING'
            self.logger.info("==> STATE: CLOSING")

    def _handle_state_waiting_for_cancel(self):
        self.logger.debug(f"Checking status of cancelled stop loss order {self.stop_loss_order_id}...")
        success, order_info = self.api.get_order(self.stop_loss_order_id)
        if not success:
            self.logger.error(f"Failed to get order info for {self.stop_loss_order_id}. Stopping.", exc_info=True)
            self.is_bot_running = False
            return
        if order_info and order_info.get('State') == 5:
            self.logger.info(f"Stop loss order {self.stop_loss_order_id} confirmed cancelled. State: {order_info.get('State')}")
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
        elif order_info and order_info.get('State') == 6:
            self.logger.warning(f"Stop loss order {self.stop_loss_order_id} was executed before it could be cancelled. State: {order_info.get('State')}")
            profit = (float(order_info.get('Price')) - self.entry_price) * self.qty
            self._send_line_notification([f"【決済：損切り】{self.ticker}", f"価格: {order_info.get('Price')}", f"損益: {profit}"], "決済")
            self.state = 'CLOSING'
            self.logger.info("==> STATE: CLOSING")

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