# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}

@dataclass
class BarSeries:
    """OHLCV + datetime 管理クラス。全配列は時系列昇順（0=最古）。"""
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    timestamps: np.ndarray      # int64 nanoseconds (UTC)
    ts_hours_jst: np.ndarray    # JST時間 0-23
    ts_dow: np.ndarray          # 曜日 0=月〜6=日

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame, server_utc_offset: int = 2) -> "BarSeries":
        """pd.DataFrame（DatetimeIndex=UTC）からBarSeriesを生成。

        JST変換: jst_hour = (server_hour + 9 - server_utc_offset) % 24
        """
        if not isinstance(df.index, pd.DatetimeIndex):
            df = df.set_index(pd.to_datetime(df.index))
        idx = df.index
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        server_hours = idx.hour
        jst_hours = (server_hours + 9 - server_utc_offset) % 24
        dow = idx.dayofweek  # 0=月, 6=日
        return cls(
            open=df["open"].values.astype(np.float64),
            high=df["high"].values.astype(np.float64),
            low=df["low"].values.astype(np.float64),
            close=df["close"].values.astype(np.float64),
            volume=df.get("volume", pd.Series(np.zeros(len(df)))).values.astype(np.float64),
            timestamps=idx.view(np.int64),
            ts_hours_jst=jst_hours.values.astype(np.int64),
            ts_dow=dow.values.astype(np.int64),
        )


def resample_to_htf(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """M1 DataFrameを指定タイムフレームにリサンプル。

    tf: 'M1'/'M5'/'M15'/'M30'/'H1'/'H4'/'D1'
    戻り値のインデックスはHTFバーの開始時刻（UTC）。
    """
    minutes = TF_MINUTES.get(tf)
    if minutes is None:
        raise ValueError(f"Unknown TF: {tf}. Use one of {list(TF_MINUTES.keys())}")
    rule = f"{minutes}min"
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df.columns:
        agg["volume"] = "sum"
    resampled = df.resample(rule, closed="left", label="left").agg(agg).dropna()
    return resampled


def get_htf_bar_indices(m_timestamps: np.ndarray, htf_timestamps: np.ndarray) -> np.ndarray:
    """MT4のiBarShift相当。

    各m_barに対して「そのバーが属するHTFバーのインデックス」を返す。
    np.searchsorted(htf_ns, m_ns, side='right') - 1 と等価。

    使用方法:
        htf_idx = get_htf_bar_indices(m5_ts, htf_ts)
        # MT4の htfShift=MathMax(iBarShift(...,1),1) 相当:
        j = max(htf_idx[i] - 1, 0)  # 直前確定HTFバー
    """
    return np.searchsorted(htf_timestamps, m_timestamps, side="right") - 1
