import configparser
import json
import sqlite3
import time
import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path

from kabu_api import KabuAPI

class BoardDataCollector:
    def __init__(self):
        self._setup_logger()
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, 'config.ini')
        config = configparser.ConfigParser()
        config.read(config_path, encoding='utf-8')

        self.ticker = config['TRADE_SETTINGS']['TICKER']
        self.exchange = int(config['TRADE_SETTINGS']['EXCHANGE'])
        self.db_path = Path("C:/share/MorinoFolder/Python/KabuRadar/DB/KabuRadar.db")
        self.db_table_name = f"tbl_{self.ticker}_board"

        # New config parameter
        self.save_interval_seconds = config.getint('TRADE_SETTINGS', 'BOARD_DATA_SAVE_INTERVAL_SECONDS', fallback=1) # Default to 1 second if not found
        self.last_save_timestamp = None

        # Pass the logger to the API if it accepts it
        try:
            self.api = KabuAPI(config_path, logger=self.logger)
        except TypeError:
            self.logger.warning("KabuAPI does not accept a logger argument. Initializing without it.")
            self.api = KabuAPI(config_path)
            
        self.is_collecting = False

    def _setup_logger(self):
        """Setup logger for console and file output."""
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(logging.INFO)
        if self.logger.hasHandlers():
            self.logger.handlers.clear()

        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)

        # Use a rotating file handler
        log_file_path = os.path.join(log_dir, "board_collector.log")
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

    def _create_table_if_not_exists(self):
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.db_table_name} (
                    Datetime TEXT PRIMARY KEY,
                    BoardData TEXT
                )
            """)
            conn.commit()
            self.logger.info(f"Table '{self.db_table_name}' ensured to exist.")
        except Exception as e:
            self.logger.error(f"Failed to create table: {e}")
        finally:
            if conn:
                conn.close()

    def _save_board_data(self, timestamp, board_data_json):
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(f"INSERT OR REPLACE INTO {self.db_table_name} (Datetime, BoardData) VALUES (?, ?)",
                           (timestamp, board_data_json))
            conn.commit()
        except Exception as e:
            self.logger.error(f"Failed to save board data: {e}")
        finally:
            if conn:
                conn.close()

    def on_message(self, ws, message):
        data = json.loads(message)
        if "Sell1" in data and "Buy1" in data:
            current_timestamp = datetime.now()
            if self.last_save_timestamp is None or \
               (current_timestamp - self.last_save_timestamp).total_seconds() >= self.save_interval_seconds:
                timestamp_str = current_timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")
                self._save_board_data(timestamp_str, message)
                self.last_save_timestamp = current_timestamp
                self.logger.info(f"Board data saved at {timestamp_str}")
        else:
            self.logger.warning(f"Unknown message type received: {data}")

    def on_error(self, ws, error):
        self.logger.error(f"WebSocket error: {error}")
        self.is_collecting = False

    def on_close(self, ws, close_status_code, close_msg):
        self.logger.info("WebSocket connection closed.")
        self.is_collecting = False

    def on_open(self, ws):
        self.logger.info("WebSocket connection opened. Registering for board data...")
        if not self.api.register_board(self.ticker, self.exchange):
            self.logger.error("Failed to register for board data. Closing WebSocket.")
            self.api.close_websocket()

    def start_websocket_connection(self):
        """Initiates the WebSocket connection."""
        if not self.api.get_token():
            self.logger.error("Failed to get API token. Exiting.")
            return False
        self._create_table_if_not_exists()
        self.logger.info(f"Board data collection for {self.ticker} is preparing to start...")
        self.is_collecting = True
        self.api.connect_websocket(self.on_message, self.on_error, self.on_close, self.on_open)
        return True

    def stop_websocket_connection(self):
        """Closes the WebSocket connection."""
        self.logger.info("Stopping board data collection...")
        self.is_collecting = False
        self.api.close_websocket()
        self.logger.info("Program finished.")

    def run(self):
        """Main logic to control the collection window."""
        self.logger.info("--- BoardDataCollector STARTED ---")
        exit_reason = "Unknown"

        try:
            collection_start_time = dt_time(8, 58)
            collection_duration = timedelta(minutes=15)
            collection_end_time = (datetime.combine(datetime.today(), collection_start_time) + collection_duration).time()

            now_time = datetime.now().time()

            if now_time > collection_end_time:
                self.logger.warning(f"Current time ({now_time.strftime('%H:%M:%S')}) is past the collection window. Exiting.")
                exit_reason = "Outside collection window (already passed)."
                return

            while now_time < collection_start_time:
                self.logger.info(f"Waiting for collection window to start at {collection_start_time.strftime('%H:%M')}...")
                time.sleep(10)
                now_time = datetime.now().time()
                if now_time > collection_end_time:
                    self.logger.warning("Collection window passed while waiting. Exiting.")
                    exit_reason = "Outside collection window (passed while waiting)."
                    return

            self.logger.info("Collection window started. Connecting to WebSocket...")
            
            if not self.start_websocket_connection():
                exit_reason = "Failed to start WebSocket connection (e.g., token error)."
                return

            while self.is_collecting:
                if datetime.now().time() >= collection_end_time:
                    self.logger.info("Collection window finished.")
                    exit_reason = "Collection window finished normally."
                    break
                time.sleep(1)
            
            # If the loop exits because is_collecting became false
            if self.is_collecting is False:
                exit_reason = "WebSocket connection closed or errored."

        except KeyboardInterrupt:
            self.logger.info("Manual interruption detected.")
            exit_reason = "Manual interruption (KeyboardInterrupt)."
        except Exception as e:
            self.logger.error(f"An unexpected error occurred in the run loop: {e}", exc_info=True)
            exit_reason = f"Unexpected error: {e}"
        finally:
            self.logger.info(f"EXIT REASON: {exit_reason}")
            self.stop_websocket_connection()

if __name__ == "__main__":
    # Setup a basic logger for the main block in case the class fails to initialize
    logger = logging.getLogger("BoardDataCollectorLogger")
    try:
        collector = BoardDataCollector()
        collector.run()
    except Exception as e:
        # Attempt to use the class logger if it was initialized, otherwise use the basic one
        if 'collector' in locals() and hasattr(collector, 'logger'):
            collector.logger.error("Script terminated due to an unhandled exception", exc_info=True)
        else:
            # Fallback basic logger configuration
            log_dir = "logs"
            os.makedirs(log_dir, exist_ok=True)
            log_file_path = os.path.join(log_dir, "board_collector.log")
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(levelname)s - %(message)s',
                handlers=[
                    RotatingFileHandler(log_file_path, maxBytes=102400, backupCount=1, encoding='utf-8'),
                    logging.StreamHandler()
                ]
            )
            logger.error("Script terminated due to an unhandled exception during initialization", exc_info=True)
