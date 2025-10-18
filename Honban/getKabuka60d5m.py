import yfinance as yf
import pandas as pd
import sys
from datetime import date, timedelta
import time
import sqlite3
from pathlib import Path
import configparser
import os

# --- 設定ファイルの読み込み ---
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, 'config.ini')
config = configparser.ConfigParser()
config.read(config_path, encoding='utf-8')

# --- 定数定義 ---
TICKER_CODE = config['TRADE_SETTINGS']['TICKER']
TICKER = f"{TICKER_CODE}.T"
DB_PATH = Path("C:/share/MorinoFolder/Python/KabuRadar/DB/KabuRadar.db")
TABLE_NAME = f"tbl_{TICKER_CODE}_5min" # 5分足データ用のテーブル名
INTERVAL = "5m" # 5分足

def fetch_stock_data_by_range(ticker: str, start_date: str, end_date: str, interval: str) -> pd.DataFrame | None:
    """指定された期間の株価データをyfinanceから取得し、列名を整形します。"""
    print(f"銘柄 {ticker} の株価データを取得します (期間: {start_date} to {end_date}, 間隔: {interval})...")
    try:
        # yfinanceではperiodパラメータを使うと最大60日分取得できる
        df = yf.download(ticker, period="60d", interval=interval, progress=False, auto_adjust=True)
        if df.empty:
            print(f"警告: 銘柄 {ticker} の期間 {start_date} to {end_date} のデータは見つかりませんでした。")
            return None
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        print("データの取得と列名の整形が完了しました。")
        return df
    except Exception as e:
        print(f"エラー: データの取得中に問題が発生しました: {e}", file=sys.stderr)
        return None

def save_data_to_sqlite(df_new_jst: pd.DataFrame, db_path: Path, table_name: str):
    """
    整形済みのデータ(JST)をSQLiteデータベースに保存します。
    既存のデータも読み込んでマージし、重複を除去することでDBを更新します。
    """
    print(f"\nデータベース '{db_path.name}' のテーブル '{table_name}' を更新します...")
    
    if df_new_jst.empty:
        print("保存する新しいデータがありません。")
        return

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)

        merged_df = df_new_jst

        try:
            df_old = pd.read_sql(f"SELECT * FROM {table_name}", conn, index_col='Datetime', parse_dates=['Datetime'])
            if not df_old.empty:
                print(f"既存データ{len(df_old)}件を読み込みました。")
                if df_old.index.tz is None:
                    df_old.index = df_old.index.tz_localize('UTC').tz_convert('Asia/Tokyo')
                else:
                    df_old.index = df_old.index.tz_convert('Asia/Tokyo')
                merged_df = pd.concat([df_old, df_new_jst])
                print("既存データと新データを結合しました。")

        except Exception as e:
            print(f"テーブル '{table_name}' は存在しないか読み込めませんでした。新しいデータのみでテーブルを作成します。(エラー: {e})")
            merged_df = df_new_jst

        final_df = merged_df[~merged_df.index.duplicated(keep='last')]
        final_df.sort_index(inplace=True)

        final_df.to_sql(table_name, conn, if_exists='replace', index=True, index_label='Datetime')

        print("データベースの更新が完了しました。")
        print(f"テーブル '{table_name}' には現在 {len(final_df)} 件のレコードがあります。")

    except Exception as e:
        print(f"エラー: データベースへの保存中に問題が発生しました: {e}", file=sys.stderr)
    finally:
        if 'conn' in locals() and conn:
            conn.close()

def main():
    """メイン処理: 過去60日分の5分足データを取得"""
    today = date.today()
    start_date = today - timedelta(days=59)

    print(f"過去60日分の5分足データを取得します (期間: {start_date.strftime('%Y-%m-%d')} から本日まで)")
    print("-" * 40)

    # yfinanceはstart/endよりperiodを使った方が安定して60日分取得できる
    stock_df = fetch_stock_data_by_range(TICKER, start_date.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'), INTERVAL)

    if stock_df is None or stock_df.empty:
        print("取得できたデータがありませんでした。")
        return

    print("取得したデータをタイムゾーン変換します...")
    if stock_df.index.tz is None:
        stock_df.index = stock_df.index.tz_localize('UTC').tz_convert('Asia/Tokyo')
    else:
        stock_df.index = stock_df.index.tz_convert('Asia/Tokyo')
    print("タイムゾーンをJSTに変換しました。")

    save_data_to_sqlite(stock_df, DB_PATH, TABLE_NAME)

    print("\n--- 今回取得したデータ範囲 ---")
    if not stock_df.empty:
        print(f"期間: {stock_df.index.min()} から {stock_df.index.max()} まで")
        print(f"合計 {len(stock_df)} 件のデータを取得・処理しました。")
        print(stock_df.head())

if __name__ == "__main__":
    main()
