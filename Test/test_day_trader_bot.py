import configparser
import time
import json
import sqlite3
import pandas as pd
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
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

        # --- Optimized Strategy Parameters ---
        self.setup_timeframe_mins = 2
        self.trigger_timeframe_mins = 1
        self.stop_loss_percent = 1.5
        self.take_profit_percent = 2.0

        self.api = KabuAPI(config_path, logger=self.logger)

        # --- State Variables ---
        self.position = None # 'long' or None
        self.entry_price = 0
        self.entry_time = None
        self.fixed_stop_loss_price = 0
        self.fixed_take_profit_price = 0
        self.is_running = True
        self.has_entered_today = False

        # Data buffers for real-time bar building
        self.one_min_data_buffer = pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])
        self.last_processed_1min_time = None

        # Setup-timeframe specific variables
        self.dip_flag_on = False
        self.lowest_price_bar_index = -1
        self.lowest_price_value = float('inf')
        self.reversal_point = None
        self.df_setup_current_day = pd.DataFrame() # To store setup bars for current day

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

    def _build_resampled_bar(self, df_1min_slice, timeframe_mins):
        if df_1min_slice.empty:
            return None
        
        # Ensure the slice is within a single timeframe_mins window
        # This is a simplified aggregation for real-time, assuming data comes in order
        resampled_bar = {
            'Open': df_1min_slice['Open'].iloc[0],
            'High': df_1min_slice['High'].max(),
            'Low': df_1min_slice['Low'].min(),
            'Close': df_1min_slice['Close'].iloc[-1],
            'Volume': df_1min_slice['Volume'].sum()
        }
        return resampled_bar

    def on_message(self, ws, message):
        data = json.loads(message)
        current_price = data.get("CurrentPrice")
        if not current_price:
            return

        

        # Only process during market hours (9:00 to 11:30)
        now_dt = datetime.now()
        now_time = now_dt.time()
        market_open = dt_time(9, 0)
        market_close_am = dt_time(11, 30)

        if not (market_open <= now_time <= market_close_am):
            if self.position is None: # Only log if no position
                self.logger.info(f"Outside market hours: {now_time.strftime('%H:%M:%S')}")
            return

        # --- Real-time 1-min bar building ---
        # Assuming messages come frequently, we need to aggregate into 1-min bars first
        current_1min_time = now_dt.replace(second=0, microsecond=0)

        if self.last_processed_1min_time is None or current_1min_time > self.last_processed_1min_time:
            # New 1-min bar starts
            if self.last_processed_1min_time is not None: # Aggregate previous 1-min bar if exists
                # This is where the previous 1-min bar would be finalized and added to buffer
                # For simplicity, we'll assume current_price is the close of the current 1-min bar
                # and build 1-min bars directly from ticks. This is a simplification.
                pass # Complex real-time 1-min bar building is out of scope for this example
            
            # For this example, we'll just use the current price as the 1-min close
            # and assume it represents a new 1-min bar for simplicity.
            # In a real bot, you'd aggregate ticks into 1-min OHLCV.
            new_1min_bar = pd.Series({
                'Open': current_price, 
                'High': current_price, 
                'Low': current_price, 
                'Close': current_price, 
                'Volume': 1 # Placeholder
            }, name=current_1min_time)
            self.one_min_data_buffer = pd.concat([self.one_min_data_buffer, pd.DataFrame([new_1min_bar])])
            self.last_processed_1min_time = current_1min_time
            self.logger.info(f"New 1-min bar at {current_1min_time.time()}: C={current_price}")

            # --- Check for Setup Timeframe Bar Completion ---
            # Check if a new setup-timeframe bar has completed
            if len(self.one_min_data_buffer) >= self.setup_timeframe_mins:
                # Resample the last 'setup_timeframe_mins' 1-min bars
                # This is a simplified real-time resample. In production, use a proper rolling window.
                temp_df_setup = self.one_min_data_buffer.iloc[-self.setup_timeframe_mins:].resample(f'{self.setup_timeframe_mins}min', label='right', closed='right').agg({
                    'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
                }).dropna()
                
                if not temp_df_setup.empty:
                    current_setup_bar = temp_df_setup.iloc[-1]
                    self.df_setup_current_day = pd.concat([self.df_setup_current_day, pd.DataFrame([current_setup_bar])])
                    self.logger.info(f"Setup {self.setup_timeframe_mins}min bar completed at {current_setup_bar.name.time()}: C={current_setup_bar['Close']}")

                    # --- Signal Detection (Setup Timeframe) ---
                    if len(self.df_setup_current_day) >= 3: # Need at least 3 bars for dip check
                        close_j = self.df_setup_current_day.iloc[-1]['Close']
                        close_j_1 = self.df_setup_current_day.iloc[-2]['Close']
                        close_j_2 = self.df_setup_current_day.iloc[-3]['Close']

                        dip_condition_check = (close_j < close_j_1) and (close_j_1 < close_j_2)
                        
                        if dip_condition_check:
                            if not self.dip_flag_on:
                                self.dip_flag_on = True
                                self.logger.info(f"Dip flag ON at {current_setup_bar.name.time()}")

                        if self.dip_flag_on:
                            if self.df_setup_current_day.iloc[-1]['Low'] < self.lowest_price_value:
                                self.lowest_price_value = self.df_setup_current_day.iloc[-1]['Low']
                                self.lowest_price_bar_index = len(self.df_setup_current_day) - 1
                                self.logger.info(f"Lowest price bar updated at {current_setup_bar.name.time()}")
                            
                            if self.lowest_price_bar_index != -1 and (len(self.df_setup_current_day) - 1) >= self.lowest_price_bar_index + 2:
                                reversal_point_bar_index = self.lowest_price_bar_index - 2
                                if reversal_point_bar_index >= 0:
                                    self.reversal_point = self.df_setup_current_day.iloc[reversal_point_bar_index]['High']
                                    self.logger.info(f"Reversal point set at {self.reversal_point} from {self.df_setup_current_day.iloc[reversal_point_bar_index].name.time()}")

        # --- Position Management ---
        if self.position is None: # No position, look for entry
            if self.has_entered_today: # Add this check
                self.logger.info("Already entered today. Skipping further entries.")
                return # Skip entry logic if already entered today

            if self.reversal_point is not None: # Reversal point identified
                # --- Check for Entry on Trigger Timeframe ---
                if len(self.one_min_data_buffer) >= self.trigger_timeframe_mins:
                    temp_df_trigger = self.one_min_data_buffer.iloc[-self.trigger_timeframe_mins:].resample(f'{self.trigger_timeframe_mins}min', label='right', closed='right').agg({
                        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
                    }).dropna()

                    if not temp_df_trigger.empty:
                        current_trigger_bar = temp_df_trigger.iloc[-1]
                        
                        # Entry Condition: Trigger bar's Close breaks above reversal_point
                        if current_trigger_bar['Close'] > self.reversal_point:
                            self.position = 'long'
                            self.entry_price = current_trigger_bar['Open']
                            self.entry_time = current_trigger_bar.name
                            self.fixed_stop_loss_price = self.entry_price * (1 - self.stop_loss_percent / 100)
                            self.fixed_take_profit_price = self.entry_price * (1 + self.take_profit_percent / 100)
                            self.logger.info(f"ENTRY: {self.ticker} at {self.entry_price} (Time: {self.entry_time.time()})")
                            self.logger.info(f"SL: {self.fixed_stop_loss_price:.2f}, TP: {self.fixed_take_profit_price:.2f}")

                            if self.auto_trade_enabled:
                                success, order_info = self.api.send_buy_order(
                                    self.ticker, self.exchange, self.qty, self.trade_password, self.entry_price
                                )
                                if success:
                                    self.has_entered_today = True # Set to True on successful entry
                                    # 損切りの逆指値注文を発注
                                    sl_success, sl_order_info = self.api.send_stop_loss_sell_order(
                                        self.ticker, self.exchange, self.qty, self.trade_password, self.fixed_stop_loss_price
                                    )
                                    if sl_success:
                                        message = [
                                            f"【エントリー】{self.ticker} 買い (自動発注成功)",
                                            f"価格: {self.entry_price}",
                                            f"時間: {self.entry_time.time()}",
                                            f"損切: {self.fixed_stop_loss_price:.2f} (逆指値発注済)",
                                            f"利確: {self.fixed_take_profit_price:.2f}"
                                        ]
                                        self._send_line_notification(message, "エントリー")
                                    else:
                                        message = [
                                            f"【エントリー】{self.ticker} 買い (自動発注成功、損切逆指値発注失敗)",
                                            f"価格: {self.entry_price}",
                                            f"時間: {self.entry_time.time()}",
                                            f"損切: {self.fixed_stop_loss_price:.2f} (逆指値発注失敗)",
                                            f"利確: {self.fixed_take_profit_price:.2f}",
                                            f"エラー: {sl_order_info}"
                                        ]
                                        self._send_line_notification(message, "エントリー")
                                        # 損切逆指値が失敗した場合、ポジションを解消するかどうかは戦略によるが、ここではボットを停止
                                        self.is_running = False
                                else:
                                    message = [
                                        f"【エントリー】{self.ticker} 買い (自動発注失敗)",
                                        f"価格: {self.entry_price}",
                                        f"時間: {self.entry_time.time()}",
                                        f"エラー: {order_info}"
                                    ]
                                    self._send_line_notification(message, "エントリー")
                                    self.is_running = False # Stop on failure
                            else:
                                message = [
                                    f"【エントリー】{self.ticker} 買い (自動発注無効)",
                                    f"価格: {self.entry_price}",
                                    f"時間: {self.entry_time.time()}",
                                    f"損切: {self.fixed_stop_loss_price:.2f}",
                                    f"利確: {self.fixed_take_profit_price:.2f}"
                                ]
                                self._send_line_notification(message, "エントリー")

        else: # Position is open, check for exit
            # Use current_price (from the latest tick) for exit checks
            # 損切りは逆指値注文で証券会社に発注済みのため、ここでは利確のみを監視
            if current_price >= self.fixed_take_profit_price:
                profit = (self.fixed_take_profit_price - self.entry_price) * self.qty
                self.logger.info(f"EXIT (TP): {self.ticker} at {self.fixed_take_profit_price} (Profit: {profit:.2f})")
                
                if self.auto_trade_enabled:
                    sell_success, sell_order_info = self.api.send_sell_order(
                        self.ticker, self.exchange, self.qty, self.trade_password
                    )
                    if sell_success:
                        message = [
                            f"【決済：利確】{self.ticker} (自動発注成功)",
                            f"価格: {self.fixed_take_profit_price:.2f}",
                            f"損益: {profit:.2f}"
                        ]
                        self._send_line_notification(message, "決済")
                        self.position = None # ポジション解消
                        self.is_running = False # 利確でその日の取引を終了
                    else:
                        message = [
                            f"【決済：利確】{self.ticker} (自動発注失敗)",
                            f"価格: {self.fixed_take_profit_price:.2f}",
                            f"損益: {profit:.2f}",
                            f"エラー: {sell_order_info}"
                        ]
                        self._send_line_notification(message, "決済")
                        # 利確注文失敗の場合、ボットは停止せずポジションを保持し続ける（要検討）
                        # ここでは、失敗してもボットを停止する
                        self.is_running = False
                else:
                    message = [
                        f"【決済：利確】{self.ticker} (自動発注無効)",
                        f"価格: {self.fixed_take_profit_price:.2f}",
                        f"損益: {profit:.2f}"
                    ]
                    self._send_line_notification(message, "決済")
                    self.position = None # ポジション解消
                    self.is_running = False # 利確でその日の取引を終了

    def on_error(self, ws, error):
        self.logger.error(f"WebSocket error: {error}")
        self.is_running = False

    def on_close(self, ws, close_status_code, close_msg):
        self.logger.info("WebSocket connection closed.")
        self.is_running = False

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
        if not self.api.register_symbol(self.ticker, self.exchange): return

        self.api.connect_websocket(self.on_message, self.on_error, self.on_close, self.on_open)

        try:
            while self.is_running:
                time.sleep(1) # Keep bot alive
        except KeyboardInterrupt:
            self.logger.info("Manual interruption detected.")
        finally:
            self.api.close_websocket()
            self.logger.info("DayTraderBot stopped.")
            self._send_line_notification([f"{self.ticker} の日中取引ボットを停止します。"], "停止")

if __name__ == "__main__":
    bot = None
    try:
        bot = DayTraderBot()
        bot.run()
    except Exception as e:
        if 'bot' in locals() and hasattr(bot, 'logger'):
            bot.logger.error("Script terminated due to an unhandled exception", exc_info=True)
        else:
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(levelname)s - %(message)s',
                handlers=[
                    RotatingFileHandler('logs/day_trader_bot.log', maxBytes=102400, backupCount=1, encoding='utf-8'),
                    logging.StreamHandler()
                ]
            )
            logging.error("Script terminated due to an unhandled exception during initialization", exc_info=True)
