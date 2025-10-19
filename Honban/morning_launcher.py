import subprocess
import sys
import os
import time
import tempfile
import logging
import configparser # Added import
from logging.handlers import RotatingFileHandler

# Define the path to the Python executable
PYTHON_EXE = sys.executable

# Define the paths to the scripts
GET_KABUKA_SCRIPT = "c:/share/MorinoFolder/Python/YoritsukiTrader/Honban/getKabuka1m.py"
GET_BOARD_DATA_SCRIPT = "c:/share/MorinoFolder/Python/YoritsukiTrader/Honban/get_board_data.py"
TRADING_BOT_SCRIPT = "c:/share/MorinoFolder/Python/YoritsukiTrader/Honban/yoritsuki_gap_short_bot.py"
DAY_TRADER_BOT_SCRIPT = "c:/share/MorinoFolder/Python/YoritsukiTrader/Honban/intraday_dip_buy_bot.py"

def setup_logger():
    """Sets up a rotating file logger."""
    logger = logging.getLogger("MorningLauncher")
    logger.setLevel(logging.INFO)
    if logger.hasHandlers():
        logger.handlers.clear()

    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    log_file_path = os.path.join(log_dir, "launcher.log")
    # 100KB per file, keep 1 backup
    handler = RotatingFileHandler(log_file_path, maxBytes=102400, backupCount=1, encoding='utf-8')
    handler.setLevel(logging.INFO)

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    # Also log to console
    logger.addHandler(logging.StreamHandler())
    return logger

def run_script_in_background(logger, script_path, name):
    """Runs a Python script in a new console window using a temporary batch file."""
    logger.info(f"[{name}] Starting {script_path} in a new window via batch file...")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"

    try:
        fd, path = tempfile.mkstemp(suffix=".bat", prefix="launch_")
        with os.fdopen(fd, 'w') as tmp:
            tmp.write(f"@echo off\n")
            tmp.write(f'title {name}\n')
            tmp.write(f'\"{PYTHON_EXE}\" \"{script_path}\"\n')
            # No pause, so the window closes automatically when the script ends
            tmp.write(f'exit\n')

        command = f'start \"{name}\" \"{path}\"'
        subprocess.Popen(command, shell=True, env=env)
        logger.info(f"[{name}] Launched via temporary batch file: {path}")
    except Exception as e:
        logger.error(f"[{name}] Failed to launch script via batch file: {e}", exc_info=True)

def run_script_and_wait(logger, script_path, name):
    """Runs a Python script, waits for it to complete, and logs its output."""
    logger.info(f"[{name}] Running {script_path} and waiting for completion...")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    result = subprocess.run([PYTHON_EXE, script_path], capture_output=True, text=True, encoding='utf-8', env=env)

    logger.info(f"[{name}] {script_path} finished with exit code {result.returncode}")
    if result.stdout:
        logger.info(f"---------- [{name}] Stdout ----------\n{result.stdout}\n------------------------------------")
    if result.stderr:
        logger.error(f"---------- [{name}] Stderr ----------\n{result.stderr}\n------------------------------------")
    return result.returncode

def main():
    logger = setup_logger()
    logger.info("--- Launcher started ---")

    # Read config for strategy toggles
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.ini')
    config = configparser.ConfigParser()
    config.read(config_path, encoding='utf-8')

    enable_opening_short = config.getboolean('STRATEGY_TOGGLES', 'ENABLE_OPENING_SHORT_STRATEGY', fallback=False)
    enable_intraday_dip_buy = config.getboolean('STRATEGY_TOGGLES', 'ENABLE_INTRADAY_DIP_BUY_STRATEGY', fallback=False)

    # 1. Run getKabuka1m.py and wait for it to complete
    get_kabuka_exit_code = run_script_and_wait(logger, GET_KABUKA_SCRIPT, "GetKabuka1m")
    if get_kabuka_exit_code != 0:
        logger.error("getKabuka1m.py failed. Aborting further launches.")
        return

    # 2. Conditionally launch trading bots
    if enable_opening_short:
        run_script_in_background(logger, TRADING_BOT_SCRIPT, "OpeningShortBot")
        time.sleep(1) # Add a small delay
    else:
        logger.info("Opening Short Strategy is disabled in config.ini.")

    if enable_intraday_dip_buy:
        run_script_in_background(logger, DAY_TRADER_BOT_SCRIPT, "IntradayDipBuyBot")
        time.sleep(1) # Add a small delay
    else:
        logger.info("Intraday Dip Buy Strategy is disabled in config.ini.")

    # 3. Launch get_board_data.py in the background
    # run_script_in_background(logger, GET_BOARD_DATA_SCRIPT, "GetBoardData")
    time.sleep(1) # Add a small delay

    logger.info("All morning scripts launched. Launcher is now exiting.")
    logger.info("--- Launcher finished ---")

if __name__ == "__main__":
    main()