import sqlite3
import pandas as pd
import configparser
import os
from datetime import datetime, time as dt_time
import logging
from pathlib import Path

def setup_logger(is_optimizer=False):
    logger = logging.getLogger("BacktestLogic")
    if logger.hasHandlers():
        logger.handlers.clear()

    logger.setLevel(logging.INFO)
    if is_optimizer:
        logger.propagate = False
    else:
        logger.addHandler(logging.StreamHandler())

    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file_path = os.path.join(log_dir, "backtest_logic.log")
    
    file_handler = logging.FileHandler(log_file_path, mode='w', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return logger

def run_backtest(logger, timeframe_mins=5, trigger_timeframe_mins=1, stop_loss_percent=None, take_profit_percent=None, trailing_stop_percent=None):
    """
    Runs a backtest for the reversal entry strategy on a variable timeframe.
    """
    logger.info(f"--- Starting Backtest (Setup:{timeframe_mins}min, Trigger:{trigger_timeframe_mins}min, SL={stop_loss_percent}%, TP={take_profit_percent}%, TSL={trailing_stop_percent}%) ---")
    
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, 'config.ini')
        config = configparser.ConfigParser()
        config.read(config_path, encoding='utf-8')
        ticker = config['TRADE_SETTINGS']['TICKER']
        qty = int(config['TRADE_SETTINGS']['QTY'])
        db_path = Path("C:/share/MorinoFolder/Python/KabuRadar/DB/KabuRadar.db")
        table_name = f"tbl_{ticker}_min"
        conn = sqlite3.connect(db_path)
        df_1min = pd.read_sql(f"SELECT * FROM {table_name}", conn, index_col='Datetime', parse_dates=['Datetime'])
        conn.close()
        df_1min.sort_index(inplace=True)
        logger.info(f"Data period from {df_1min.index.min()} to {df_1min.index.max()}")
    except Exception as e:
        logger.error(f"Error during initialization: {e}")
        return None

    excluded_dates = ["2025-08-08", "2025-08-09"]
    trades = []
    total_profit = 0
    wins = 0
    losses = 0
    gross_profit = 0 # Initialize gross_profit
    gross_loss = 0   # Initialize gross_loss
    
    unique_days = df_1min.index.normalize().unique()

    for i in range(len(unique_days) - 1):
        current_day = unique_days[i]
        next_day = unique_days[i+1]

        if current_day.strftime("%Y-%m-%d") in excluded_dates:
            continue

        day_df_1min = df_1min[df_1min.index.date == current_day.date()]
        if day_df_1min.empty:
            continue

        # --- Setup Timeframe (e.g., 5min) ---
        setup_resample_period = f'{timeframe_mins}min'
        df_setup = day_df_1min.resample(setup_resample_period, label='right', closed='right').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
        }).dropna()

        search_start_time = dt_time(9, 1)
        search_end_time = dt_time(11, 30)

        entry_found = False
        entry_price = 0
        entry_time = None
        dip_flag_on = False
        lowest_price_bar_index = -1
        lowest_price_value = float('inf')
        reversal_point = None

        # --- Find Reversal Point on Setup Timeframe ---
        for j in range(len(df_setup)):
            current_setup_bar_time = df_setup.index[j].time()
            
            if current_setup_bar_time > search_end_time:
                break
            if current_setup_bar_time > search_start_time:
                if j >= 2:
                    close_j = df_setup.iloc[j]['Close']
                    close_j_1 = df_setup.iloc[j-1]['Close']
                    close_j_2 = df_setup.iloc[j-2]['Close']
                    if (close_j < close_j_1) and (close_j_1 < close_j_2):
                        if not dip_flag_on:
                            dip_flag_on = True
                if dip_flag_on:
                    if df_setup.iloc[j]['Low'] < lowest_price_value:
                        lowest_price_value = df_setup.iloc[j]['Low']
                        lowest_price_bar_index = j
                    
                    if lowest_price_bar_index != -1 and j >= lowest_price_bar_index + 2:
                        reversal_point_bar_index = lowest_price_bar_index - 2
                        if reversal_point_bar_index >= 0:
                            reversal_point = df_setup.iloc[reversal_point_bar_index]['High']
                            # Reversal point found, now look for entry on trigger timeframe
                            break
        
        # --- Look for Entry on Trigger Timeframe ---
        if reversal_point is not None:
            trigger_resample_period = f'{trigger_timeframe_mins}min'
            df_trigger = day_df_1min.resample(trigger_resample_period, label='right', closed='right').agg({
                'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
            }).dropna()
            
            # Start searching for trigger from the time the setup bar closed
            # (or the next bar if the setup bar is the last one)
            start_trigger_search_time = df_setup.index[j].time() if j < len(df_setup) else search_end_time

            for k in range(len(df_trigger)):
                current_trigger_bar_time = df_trigger.index[k].time()
                current_trigger_bar_data = df_trigger.iloc[k]

                if current_trigger_bar_time > search_end_time:
                    break
                
                # Only check for trigger after the setup bar has closed
                if current_trigger_bar_time > start_trigger_search_time:
                    if current_trigger_bar_data['Close'] > reversal_point:
                        entry_price = current_trigger_bar_data['Open']
                        entry_time = df_trigger.index[k]
                        entry_found = True
                        break

        if entry_found:
            profit = 0
            exit_found = False

            if trailing_stop_percent is not None:
                stop_loss_price = entry_price * (1 - trailing_stop_percent / 100)
                exit_search_df = day_df_1min[day_df_1min.index > entry_time]
                for k in range(len(exit_search_df)):
                    current_1min_candle = exit_search_df.iloc[k]
                    if current_1min_candle['Low'] <= stop_loss_price:
                        profit = (stop_loss_price - entry_price) * qty
                        exit_found = True
                        break
                    new_stop_loss = current_1min_candle['High'] * (1 - trailing_stop_percent / 100)
                    if new_stop_loss > stop_loss_price:
                        stop_loss_price = new_stop_loss
                if not exit_found and not exit_search_df.empty:
                    exit_price = exit_search_df.iloc[-1]['Close']
                    profit = (exit_price - entry_price) * qty
            elif stop_loss_percent is not None and take_profit_percent is not None:
                stop_loss_price = entry_price * (1 - stop_loss_percent / 100)
                take_profit_price = entry_price * (1 + take_profit_percent / 100)
                exit_search_df = day_df_1min[day_df_1min.index > entry_time]
                for k in range(len(exit_search_df)):
                    current_1min_candle = exit_search_df.iloc[k]
                    if current_1min_candle['Low'] <= stop_loss_price:
                        profit = (stop_loss_price - entry_price) * qty
                        exit_found = True
                        break
                    if current_1min_candle['High'] >= take_profit_price:
                        profit = (take_profit_price - entry_price) * qty
                        exit_found = True
                        break
                if not exit_found and not exit_search_df.empty:
                    exit_price = exit_search_df.iloc[-1]['Close']
                    profit = (exit_price - entry_price) * qty
            else:
                next_day_df_1min = df_1min[df_1min.index.date == next_day.date()]
                exit_df_1min = next_day_df_1min[next_day_df_1min.index.time >= dt_time(9, 0)]
                if not exit_df_1min.empty:
                    exit_price = exit_df_1min.iloc[0]['Open']
                    profit = (exit_price - entry_price) * qty
            
            if profit != 0:
                total_profit += profit
                trades.append(profit)
                if profit > 0:
                    wins += 1
                    gross_profit += profit # Add to gross_profit
                    logger.info(f"{current_day.strftime('%Y-%m-%d')}: ● (Profit: {profit:.2f})")
                else:
                    losses += 1
                    gross_loss += abs(profit) # Add absolute value to gross_loss
                    logger.info(f"{current_day.strftime('%Y-%m-%d')}: 〇 (Profit: {profit:.2f})")

    if not trades:
        return {'total_profit': 0, 'win_rate': 0, 'total_trades': 0, 'wins': 0, 'losses': 0, 'gross_profit': 0, 'gross_loss': 0}

    win_rate = (wins / len(trades)) * 100
    return {
        'total_profit': total_profit,
        'win_rate': win_rate,
        'total_trades': len(trades),
        'wins': wins,
        'losses': losses,
        'gross_profit': gross_profit,
        'gross_loss': gross_loss,
        'trades': trades # Return the list of individual trades
    }

if __name__ == "__main__":
    logger = setup_logger()

    # config.ini を読み込む
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.ini')
    config = configparser.ConfigParser()
    config.read(config_path, encoding='utf-8')

    # config.ini からパラメータを読み込む
    setup_timeframe_mins = int(config['INTRADAY_DIP_BUY_PARAMS']['SETUP_TIMEFRAME_MINS'])
    trigger_timeframe_mins = int(config['INTRADAY_DIP_BUY_PARAMS']['TRIGGER_TIMEFRAME_MINS'])
    stop_loss_percent = float(config['INTRADAY_DIP_BUY_PARAMS']['STOP_LOSS_PERCENT'])
    take_profit_percent = float(config['INTRADAY_DIP_BUY_PARAMS']['TAKE_PROFIT_PERCENT'])

    results = run_backtest(
        logger,
        timeframe_mins=setup_timeframe_mins,
        trigger_timeframe_mins=trigger_timeframe_mins,
        stop_loss_percent=stop_loss_percent,
        take_profit_percent=take_profit_percent
    )
    if results:
        logger.info("--- Backtest Results ---")
        logger.info(f"Total Trades: {results['total_trades']}")
        logger.info(f"Wins: {results['wins']}")
        logger.info(f"Losses: {results['losses']}")
        logger.info(f"Win Rate: {results['win_rate']:.2f}%")
        logger.info(f"Total Profit: {results['total_profit']:.2f} JPY")
        logger.info(f"Gross Profit: {results['gross_profit']:.2f} JPY")
        logger.info(f"Gross Loss: {results['gross_loss']:.2f} JPY")
        if results['gross_loss'] > 0:
            logger.info(f"Profit Factor: {results['gross_profit'] / results['gross_loss']:.2f}")
        else:
            logger.info("Profit Factor: Inf (No Gross Loss)")
        
        # Analyze individual trades
        winning_trades = [t for t in results['trades'] if t > 0]
        losing_trades = [t for t in results['trades'] if t < 0]

        if winning_trades:
            logger.info(f"Average Winning Trade: {sum(winning_trades) / len(winning_trades):.2f} JPY")
            logger.info(f"Max Winning Trade: {max(winning_trades):.2f} JPY")
        if losing_trades:
            logger.info(f"Average Losing Trade: {sum(losing_trades) / len(losing_trades):.2f} JPY")
            logger.info(f"Max Losing Trade: {min(losing_trades):.2f} JPY")