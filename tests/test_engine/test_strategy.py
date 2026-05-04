# -*- coding: utf-8 -*-
"""OPOEngine 検証用決定論的ストラテジー。

シグナル条件: バータイムスタンプ由来の signal_mod = (bar_index_from_epoch % 100)
  mod == 10  → BUY  即時エントリー
  mod == 35  → SELL 即時エントリー (BUY保有中なら ドテン)
  mod == 55  → BUY  ブレイクアウト (high[i] 超え, wait=8bars)
  mod == 80  → SELL 即時エントリー

SL = entry ± SL_PRICE (BUY: entry-SL, SELL: entry+SL)
TP = entry ± TP_PRICE
時間決済: open_bar + TIME_EXIT_BARS バー後の open 価格で決済

【注意】 signal_mod はバーの UTC タイムスタンプ + エポック基準で計算するため、
Python と MT4 が同じ日から開始していなくても同じシグナルが発生する。
MT4 側 (TestSimulator.mq4) と SERVER_UTC_OFFSET を合わせること。
"""
from typing import Optional, Tuple
import numpy as np
import pandas as pd

from mt4_simulator.engine import Strategy, BarContext, Position, Signal, Trade
from mt4_simulator import exit_codes as ec

# ─── パラメーター（TestSimulator.mq4 の extern と一致させること） ────────────
TIME_EXIT_BARS  = 20
SL_PRICE        = 5.0     # 価格単位 (pip_point=0.1 → 50 pips)
TP_PRICE        = 10.0    # 価格単位 (100 pips)
BO_WAIT_BARS    = 8
BAR_PERIOD_MIN  = 5       # M5 データ用 (M1 なら 1 に変更)
SERVER_UTC_OFFSET = 2     # UTC+2

# エポック: 2000-01-03 00:00 UTC (月曜日) — このエポック以降のデータであれば常に正の idx
_EPOCH_UTC_NS = int(pd.Timestamp("2000-01-03", tz="UTC").value)
_BAR_PERIOD_NS = BAR_PERIOD_MIN * 60 * int(1e9)


def _signal_mod(timestamp_ns: int) -> int:
    """signal_mod (0-99) を計算。

    timestamps は CSV のサーバー時刻を UTC として保存 (= 実UTC + SERVER_UTC_OFFSET*h)。
    MT4 の iTime() は実際の UTC 秒を返すため、両者を合わせるために
    ここでサーバーオフセット分を引いて実 UTC に変換してから idx を計算する。
    """
    actual_utc_ns = timestamp_ns - SERVER_UTC_OFFSET * 3600 * int(1e9)
    idx = (actual_utc_ns - _EPOCH_UTC_NS) // _BAR_PERIOD_NS
    return int(idx % 100)


class TestStrategy(Strategy):
    def setup_indicators(self, ctx: BarContext) -> None:
        pass

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        mod = _signal_mod(int(ctx.timestamps[ctx.i]))
        if mod == 10:
            return Signal(direction=1, reason=ec.ENTRY_BUY)
        if mod == 35:
            return Signal(direction=-1, reason=ec.ENTRY_SELL)
        if mod == 55:
            return Signal(
                direction=1, reason=ec.ENTRY_BUY,
                use_breakout=True,
                breakout_level=ctx.high[ctx.i],
                breakout_wait_bars=BO_WAIT_BARS,
            )
        if mod == 80:
            return Signal(direction=-1, reason=ec.ENTRY_SELL)
        return None

    def on_position_opened(self, ctx: BarContext, pos: Position) -> None:
        if pos.direction == 1:
            pos.sl = pos.open_price - SL_PRICE
            pos.tp = pos.open_price + TP_PRICE
        else:
            pos.sl = pos.open_price + SL_PRICE
            pos.tp = pos.open_price - TP_PRICE
        pos.state["time_exit_bar"] = pos.open_bar + TIME_EXIT_BARS

    def on_exit(self, ctx: BarContext, pos: Position) -> Optional[Tuple[int, float]]:
        if ctx.i >= pos.state["time_exit_bar"]:
            return ec.EXIT_TIME, ctx.open[ctx.i]
        return None
