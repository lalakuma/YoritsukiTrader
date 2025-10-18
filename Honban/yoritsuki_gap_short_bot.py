import configparser
import time
import json
import sqlite3
import pandas as pd
from pathlib import Path
import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime, time as dt_time

from kabu_api import KabuAPI
from line_messaging_api_notifier import line_notify

class YoritsukiGapShortBot:
    def __init__(self):
        self._setup_logger()
        # --- 設定ファイルの読み込み ---
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, 'config.ini')
        config = configparser.ConfigParser()
        config.read(config_path, encoding='utf-8')
        
        self.ticker = config['TRADE_SETTINGS']['TICKER']
        self.exchange = int(config['TRADE_SETTINGS']['EXCHANGE'])
        self.logic_params = {
            'gap_ratio': float(config['LOGIC_PARAMS']['GAP_RATIO']),
            'stop_loss_percent': float(config['LOGIC_PARAMS']['STOP_LOSS_PERCENT']),
            'bullish_candles_for_profit': int(config['LOGIC_PARAMS']['BULLISH_CANDLES_FOR_PROFIT'])
        }
        self.db_table_name = f"tbl_{self.ticker}_min"
        self.db_path = Path("C:/share/MorinoFolder/Python/KabuRadar/DB/KabuRadar.db")

        # --- 新しい設定の読み込み ---
        self.auto_trade_enabled = config.getboolean('TRADE_SETTINGS', 'AUTO_TRADE_ENABLED')
        self.trade_password = config['SECRETS']['TRADE_PASSWORD']
        self.qty = int(config['TRADE_SETTINGS']['QTY'])

        # --- モジュールの初期化 ---
        try:
            self.api = KabuAPI(config_path, logger=self.logger)
        except TypeError:
            self.logger.warning("KabuAPI does not accept a logger argument. Initializing without it.")
            self.api = KabuAPI(config_path)

        # --- 状態変数の初期化 ---
        self.prev_day_close = 0
        self.position = None # 'short' または None
        self.entry_price = 0
        self.stop_loss_price = 0
        self.bullish_candle_count = 0
        self.last_price = 0
        self.is_running = True

    def _setup_logger(self):
        """Setup logger for console and file output."""
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(logging.INFO)
        if self.logger.hasHandlers():
            self.logger.handlers.clear()

        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)

        # Use a rotating file handler
        log_file_path = os.path.join(log_dir, "yoritsuki_gap_short_bot.log")
        # 100KB per file, keep 1 backup
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

    def get_prev_day_close(self):
        """データベースから前日の終値を取得する"""
        self.logger.info("Fetching previous day's close from database...")
        try:
            conn = sqlite3.connect(self.db_path)
            query = f"SELECT Close FROM {self.db_table_name} ORDER BY Datetime DESC LIMIT 1"
            df = pd.read_sql_query(query, conn)
            if not df.empty:
                self.prev_day_close = df.iloc[0]['Close']
                self.logger.info(f"Successfully fetched previous day's close: {self.prev_day_close}")
                return True
            else:
                self.logger.error("No data found in database. Please run getKabuka1m.py first.")
                return False
        except Exception as e:
            self.logger.error(f"Failed to fetch previous day's close from database: {e}")
            return False
        finally:
            if 'conn' in locals(): conn.close()

    def _send_line_notification(self, message_lines, subject):
        """Sends a notification to LINE."""
        try:
            line_notify(message_lines, subject, logger=self.logger)
        except TypeError:
            # Fallback for older version of line_notify
            line_notify(message_lines, subject)

    # --- WebSocketのコールバック関数 ---
    def on_message(self, ws, message):
        data = json.loads(message)
        current_price = data.get("CurrentPrice")
        if not current_price:
            return

        # Ignore ticks before market open (9:00 AM JST)
        now_time = datetime.now().time()
        market_open_time = dt_time(9, 0)

        # 1. エントリー判断 (寄り付き)
        if self.position is None and self.last_price == 0:
            if now_time < market_open_time:
                self.logger.info(f"Ignoring pre-market tick: {current_price} at {now_time.strftime('%H:%M:%S')}")
                return
            
            self.last_price = current_price
            gap_up_price = self.prev_day_close * (1 + self.logic_params['gap_ratio'])
            self.logger.info(f"First valid tick at market open: {current_price}, Gap-up threshold: > {gap_up_price:.2f}")

            if current_price > gap_up_price:
                self.position = 'short'
                self.entry_price = current_price
                self.stop_loss_price = self.entry_price * (1 + self.logic_params['stop_loss_percent'] / 100)
                self.logger.info(f"Condition met. Entering SHORT position at {self.entry_price}. Stop-loss set to {self.stop_loss_price:.2f}")

                if self.auto_trade_enabled:
                    success, order_info = self.api.send_short_sell_order(
                        self.ticker, self.exchange, self.qty, self.trade_password
                    )
                    if success:
                        message = [
                            f"【エントリー】{self.ticker} ギャップアップ空売り (自動発注成功)",
                            f"価格: {self.entry_price}",
                            f"損切: {self.stop_loss_price:.2f}",
                            f"注文情報: {order_info}"
                        ]
                        self._send_line_notification(message, "エントリー")
                    else:
                        message = [
                            f"【エントリー】{self.ticker} ギャップアップ空売り (自動発注失敗)",
                            f"価格: {self.entry_price}",
                            f"損切: {self.stop_loss_price:.2f}",
                            f"エラー: {order_info}"
                        ]
                        self._send_line_notification(message, "エントリー")
                        self.is_running = False # Stop on failure
                else:
                    message = [
                        f"【エントリー】{self.ticker} ギャップアップ空売り (自動発注無効)",
                        f"価格: {self.entry_price}",
                        f"損切: {self.stop_loss_price:.2f}"
                    ]
                    self._send_line_notification(message, "エントリー")
            else:
                self.logger.info("Entry condition not met. No trade today.")
                self.is_running = False # Exit if condition is not met

        # 2. 決済判断（ポジション保有中）
        elif self.position == 'short':
            # We should not process any ticks before market open for exit logic either
            if now_time < market_open_time:
                return

            # a. 損切り判定
            if current_price >= self.stop_loss_price:
                profit = self.entry_price - self.stop_loss_price
                self.logger.info(f"Stop-loss triggered at {current_price}. Profit: {profit:.2f}")
                message = [
                    f"【決済：損切り】{self.ticker}",
                    f"価格: {self.stop_loss_price:.2f}",
                    f"損益: {profit:.2f}"
                ]
                self._send_line_notification(message, "決済")
                self.is_running = False

            # b. 利確判定（陽線カウント）
            if current_price > self.last_price:
                self.bullish_candle_count += 1
                self.logger.info(f"Bullish candle detected. Count: {self.bullish_candle_count}")
            else:
                if self.bullish_candle_count > 0:
                    self.logger.info("Bullish candle streak broken.")
                self.bullish_candle_count = 0

            if self.bullish_candle_count >= self.logic_params['bullish_candles_for_profit']:
                # Condition met, now check if it's profitable
                if current_price < self.entry_price:
                    profit = self.entry_price - current_price
                    self.logger.info(f"Take-profit triggered at {current_price}. Profit: {profit:.2f}")
                    message = [
                        f"【決済：利確】{self.ticker} (陽線{self.logic_params['bullish_candles_for_profit']}本)",
                        f"価格: {current_price}",
                        f"損益: {profit:.2f}"
                    ]
                    self._send_line_notification(message, "決済")
                    self.is_running = False
                else:
                    # The take-profit signal appeared, but the position is not profitable.
                    # Reset the count and continue, letting the stop-loss handle the exit.
                    self.logger.info(f"Take-Profit signal ignored (unprofitable). Resetting bullish count.")
                    self.bullish_candle_count = 0

        self.last_price = current_price

    def on_error(self, ws, error):
        self.logger.error(f"WebSocket error: {error}")
        self.is_running = False

    def on_close(self, ws, close_status_code, close_msg):
        self.logger.info("WebSocket connection closed.")
        self.is_running = False

    def on_open(self, ws):
        self.logger.info("WebSocket connection opened. Waiting for price data...")

    def run(self):
        """ボットのメイン処理"""
        self.logger.info(f"Starting TradingBot for {self.ticker}.")
        if not self.auto_trade_enabled:
            self.logger.warning("Auto trading is disabled. Exiting.")
            self._send_line_notification([f"{self.ticker} の自動取引ボットは無効のため起動しません。"], "通知")
            return
            
        self._send_line_notification([f"{self.ticker} の自動取引ボットを起動します。"], "起動")
        
        if not self.api.get_token(): return
        if not self.get_prev_day_close(): return
        if not self.api.register_symbol(self.ticker, self.exchange): return

        self.api.connect_websocket(self.on_message, self.on_error, self.on_close, self.on_open)

        try:
            while self.is_running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("Manual interruption detected.")
        finally:
            self.api.close_websocket()
            self.logger.info("TradingBot stopped.")
            self._send_line_notification([f"{self.ticker} の自動取引ボットを停止します。"], "停止")

if __name__ == "__main__":
    bot = None
    logger = logging.getLogger("TradingBotLogger")
    try:
        bot = YoritsukiGapShortBot()
        logger = bot.logger
        bot.run()
    except Exception as e:
        if 'bot' in locals() and hasattr(bot, 'logger'):
            bot.logger.error("Script terminated due to an unhandled exception", exc_info=True)
        else:
            log_dir = "logs"
            os.makedirs(log_dir, exist_ok=True)
            log_file_path = os.path.join(log_dir, "yoritsuki_gap_short_bot.log")
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(levelname)s - %(message)s',
                handlers=[
                    RotatingFileHandler(log_file_path, maxBytes=102400, backupCount=1, encoding='utf-8'),
                    logging.StreamHandler()
                ]
            )
            logger.error("Script terminated due to an unhandled exception during initialization", exc_info=True)
