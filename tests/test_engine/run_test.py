#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OPOEngine テストドライバー。TestStrategy を走らせ結果を CSV 出力。

使い方:
  python tests/test_engine/run_test.py --data XAUUSD5.csv \\
         --out test_engine_python.csv [--start 2024-01-01]
"""
import argparse
import csv
import sys

import numpy as np
import pandas as pd

from mt4_simulator.engine import OPOEngine
from mt4_simulator.data import BarSeries
from mt4_simulator import exit_codes as ec
from test_engine.test_strategy import (
    TestStrategy, BAR_PERIOD_MIN, SERVER_UTC_OFFSET,
    TIME_EXIT_BARS, SL_PRICE, TP_PRICE, BO_WAIT_BARS,
)

SPREAD_PIPS = 2.0
PIP_POINT   = 0.1
WARMUP      = 200


def load_df(path: str) -> pd.DataFrame:
    """MT4 形式 CSV (ヘッダーなし 7列) と標準形式 (datetime index) 両対応。"""
    with open(path, encoding="utf-8", errors="replace") as f:
        first = f.readline().strip()

    if first.startswith("<") or first.lower().startswith("date"):
        df = pd.read_csv(path, header=0, encoding="utf-8")
        df.columns = [c.strip("<>").lower() for c in df.columns]
        if "date" in df.columns and "time" in df.columns:
            df["datetime"] = pd.to_datetime(
                df["date"].astype(str) + " " + df["time"].astype(str), format="mixed"
            )
            df.drop(columns=["date", "time"], inplace=True)
        df.set_index("datetime", inplace=True)
    else:
        df = pd.read_csv(path, header=None, encoding="utf-8")
        cols = ["date", "time", "open", "high", "low", "close"]
        if df.shape[1] >= 7:
            cols.append("volume")
        df.columns = cols[: df.shape[1]]
        df["datetime"] = pd.to_datetime(
            df["date"].astype(str) + " " + df["time"].astype(str), format="mixed"
        )
        df.drop(columns=["date", "time"], inplace=True)
        df.set_index("datetime", inplace=True)

    df.index = pd.to_datetime(df.index, utc=True)
    df.sort_index(inplace=True)
    df.rename(columns=str.lower, inplace=True)
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"Column '{col}' not found in {path}")
    return df


def ts_to_server(ts_ns: int) -> str:
    """timestamps は CSV のサーバー時刻をそのまま UTC として保存しているため、
    オフセット補正なしで直接フォーマットするとサーバー時刻文字列になる。"""
    return pd.Timestamp(ts_ns, unit="ns").strftime("%Y.%m.%d %H:%M")


def main() -> None:
    ap = argparse.ArgumentParser(description="OPOEngine test runner")
    ap.add_argument("--data", required=True, help="OHLC CSV (XAUUSD5.csv 等)")
    ap.add_argument("--out", default="test_engine_python.csv")
    ap.add_argument("--start", default=None, help="開始日 YYYY-MM-DD (省略時は全期間)")
    ap.add_argument("--end", default=None, help="終了日 YYYY-MM-DD")
    args = ap.parse_args()

    df = load_df(args.data)
    if args.start:
        df = df[df.index >= pd.Timestamp(args.start, tz="UTC")]
    if args.end:
        df = df[df.index <= pd.Timestamp(args.end, tz="UTC")]
    if len(df) < WARMUP + 2:
        print(f"ERROR: データ不足 ({len(df)} bars, warmup={WARMUP})", file=sys.stderr)
        sys.exit(1)

    bs = BarSeries.from_dataframe(df, server_utc_offset=SERVER_UTC_OFFSET)
    strategy = TestStrategy()
    engine = OPOEngine(strategy, spread_pips=SPREAD_PIPS, pip_point=PIP_POINT, warmup=WARMUP)
    trades = engine.run(
        bs.open, bs.high, bs.low, bs.close,
        bs.timestamps, bs.ts_hours_jst, bs.ts_dow,
    )

    ts_ns = bs.timestamps
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "entry_bar", "exit_bar", "direction",
            "entry_price", "exit_price", "profit_pips",
            "exit_reason", "entry_time_server", "exit_time_server",
        ])
        for t in trades:
            w.writerow([
                t.entry_bar,
                t.exit_bar,
                "BUY" if t.direction == 1 else "SELL",
                f"{t.entry_price:.5f}",
                f"{t.exit_price:.5f}",
                f"{t.profit_pips:.1f}",
                ec.EXIT_CODE_TO_STR.get(t.exit_reason, str(t.exit_reason)),
                ts_to_server(int(ts_ns[t.entry_bar])),
                ts_to_server(int(ts_ns[t.exit_bar])),
            ])

    print(f"Wrote {len(trades)} trades → {args.out}")
    print(f"Settings: SPREAD={SPREAD_PIPS}pips, PIP_POINT={PIP_POINT}, "
          f"WARMUP={WARMUP}, TEB={TIME_EXIT_BARS}, SL={SL_PRICE}, TP={TP_PRICE}, "
          f"BO_WAIT={BO_WAIT_BARS}, BAR_PERIOD={BAR_PERIOD_MIN}min")


if __name__ == "__main__":
    main()
