import pandas as pd
import logging
import os
import sys
from itertools import product

# Add BackTest to the path to import from it
# This is a bit of a hack, a better solution would be to structure the project with packages
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from BackTest.backtest_logic_full_day import run_backtest, setup_logger
except ImportError as e:
    print(f"Failed to import backtest logic. Make sure 'BackTest/backtest_logic_full_day.py' exists. Error: {e}", file=sys.stderr)
    sys.exit(1)

def optimize_full_day_strategy():
    optimizer_logger = logging.getLogger("Optimizer")
    optimizer_logger.setLevel(logging.INFO)
    if optimizer_logger.hasHandlers():
        optimizer_logger.handlers.clear()

    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file_path = os.path.join(log_dir, "optimize_full_day.log")
    
    file_handler = logging.FileHandler(log_file_path, mode='w', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    optimizer_logger.addHandler(file_handler)
    optimizer_logger.addHandler(console_handler)

    optimizer_logger.info("--- Starting Full-Day Strategy Optimization ---")

    # --- Optimization Parameters ---
    setup_timeframe_range = [2, 3, 5, 7, 10]
    trigger_timeframe_range = [1, 2, 3]
    sl_range = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    tp_range = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]

    # Create all combinations, filtering out invalid ones
    param_combinations = [
        params for params in product(setup_timeframe_range, trigger_timeframe_range, sl_range, tp_range)
        if params[1] <= params[0] # trigger timeframe must be <= setup timeframe
    ]

    results_list = []
    best_profit = -float('inf')
    best_params = {}

    backtest_logger = setup_logger(is_optimizer=True)

    total_combinations = len(param_combinations)
    optimizer_logger.info(f"Testing {total_combinations} combinations of parameters...")
    count = 0

    for setup_tf, trigger_tf, sl, tp in param_combinations:
        count += 1
        optimizer_logger.info(f"Running test {count}/{total_combinations}: Setup={setup_tf}min, Trigger={trigger_tf}min, SL={sl}%, TP={tp}%")

        results = run_backtest(
            logger=backtest_logger, 
            timeframe_mins=setup_tf,
            trigger_timeframe_mins=trigger_tf,
            stop_loss_percent=sl, 
            take_profit_percent=tp
        )

        if results is None:
            optimizer_logger.warning(f"Backtest failed for params. Skipping.")
            continue

        current_profit = results['total_profit']
        results_list.append({
            'Setup (min)': setup_tf,
            'Trigger (min)': trigger_tf,
            'SL (%)': sl,
            'TP (%)': tp,
            'Total Profit': current_profit,
            'Win Rate (%)': results['win_rate'],
            'Trades': results['total_trades'],
            'PF': results['gross_profit'] / results['gross_loss'] if results['gross_loss'] > 0 else float('inf')
        })

        if current_profit > best_profit:
            best_profit = current_profit
            best_params = {'Setup': setup_tf, 'Trigger': trigger_tf, 'SL': sl, 'TP': tp}
            optimizer_logger.info(f"*** New Best Profit Found: {best_profit:.2f} JPY with {best_params} ***")

    optimizer_logger.info("--- Optimization Finished ---")

    if not results_list:
        optimizer_logger.info("No backtests were successfully completed.")
        return

    results_df = pd.DataFrame(results_list)
    results_df = results_df.sort_values(by='Total Profit', ascending=False)

    optimizer_logger.info("\n--- Top 10 Most Profitable Combinations ---")
    optimizer_logger.info(results_df.head(10).to_string())

    optimizer_logger.info("\n--- Champion Parameters ---")
    champion_results = results_df.iloc[0]
    optimizer_logger.info(
        f"Best combination is Setup={champion_results['Setup (min)']}min, Trigger={champion_results['Trigger (min)']}min, SL={champion_results['SL (%)']}% and TP={champion_results['TP (%)']}%\n"
        f"  - Total Profit: {champion_results['Total Profit']:.2f} JPY\n"
        f"  - Win Rate: {champion_results['Win Rate (%)']:.2f}%\n"
        f"  - Total Trades: {champion_results['Trades']}\n"
        f"  - Profit Factor: {champion_results['PF']:.2f}"
    )

if __name__ == "__main__":
    optimize_full_day_strategy()
