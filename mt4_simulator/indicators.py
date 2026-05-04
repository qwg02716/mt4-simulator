# -*- coding: utf-8 -*-
"""
mt4_simulator/indicators.py  ―  標準インジケーター公開API
==========================================================
全関数は時系列昇順配列（0=最古, n-1=最新）を受け取る。
result[i] = バーiが確定した時点での値。
MT4のshift=1 = result[i-1], shift=0 = result[i]
"""

import numpy as np
from typing import Tuple

# numpy >= 1.20 の sliding_window_view を優先使用、古い場合は as_strided にフォールバック
try:
    from numpy.lib.stride_tricks import sliding_window_view as _swv
    def _windows(arr: np.ndarray, w: int) -> np.ndarray:
        """shape (n-w+1, w) の読み取り専用スライディングウィンドウビュー"""
        return _swv(arr, w)
except ImportError:
    from numpy.lib.stride_tricks import as_strided as _as_strided
    def _windows(arr: np.ndarray, w: int) -> np.ndarray:
        n = len(arr)
        shape = (n - w + 1, w)
        strides = (arr.strides[0], arr.strides[0])
        return _as_strided(arr, shape=shape, strides=strides)

# scipy.signal.lfilter が使えれば EMA 系の逐次ループを C 実装に置き換える
try:
    from scipy.signal import lfilter as _lfilter, lfilter_zi as _lfilter_zi
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


def _iir_ema(x: np.ndarray, alpha: float, seed: float) -> np.ndarray:
    """
    EMA 型 IIR フィルター: y[n] = alpha*x[n] + (1-alpha)*y[n-1]
    scipy.signal.lfilter を使用（C 実装、Python ループの約 10〜50×）。
    x[0] から始まる配列を受け取り、同長の配列を返す。
    seed は y[-1]（ウォームアップ初期値）。
    """
    b = np.array([alpha], dtype=np.float64)
    a = np.array([1.0, -(1.0 - alpha)], dtype=np.float64)
    zi = _lfilter_zi(b, a) * seed
    out, _ = _lfilter(b, a, x, zi=zi)
    return out


def _iir_ha_open(ha_close_from_first: np.ndarray, ha_open_init: float) -> np.ndarray:
    """
    HA_Open 再帰: y[n] = (y[n-1] + x[n-1]) / 2  (= 0.5*y[n-1] + 0.5*x[n-1])
    IIR フィルター: b=[0, 0.5], a=[1, -0.5]
    zi = [ha_open_init] → y[0] = ha_open_init（先頭はシード値のまま）
    """
    b = np.array([0.0, 0.5], dtype=np.float64)
    a = np.array([1.0, -0.5], dtype=np.float64)
    out, _ = _lfilter(b, a, ha_close_from_first, zi=np.array([ha_open_init]))
    return out


def _ema(series: np.ndarray, period: int) -> np.ndarray:
    """EMA（指数移動平均）。MQL4 の iMA(MODE_EMA) と同等。"""
    alpha = 2.0 / (period + 1)
    result = np.full(len(series), np.nan)
    first = 0
    while first < len(series) and np.isnan(series[first]):
        first += 1
    if first >= len(series):
        return result

    if _HAS_SCIPY:
        result[first:] = _iir_ema(series[first:], alpha, series[first])
    else:
        result[first] = series[first]
        for i in range(first + 1, len(series)):
            prev = result[i - 1]
            if np.isnan(series[i]) or np.isnan(prev):
                result[i] = np.nan
            else:
                result[i] = alpha * series[i] + (1 - alpha) * prev
    return result


def _sma(series: np.ndarray, period: int) -> np.ndarray:
    """SMA（単純移動平均）— ベクトル化済み"""
    n = len(series)
    result = np.full(n, np.nan)
    if n < period:
        return result
    wins = _windows(series, period)
    mask = ~np.any(np.isnan(wins), axis=1)
    result[period - 1:][mask] = wins[mask].mean(axis=1)
    return result


def _smma(series: np.ndarray, period: int) -> np.ndarray:
    """
    SMMA（Smoothed Moving Average）= Wilder's MA。
    MQL4 の iMA(MODE_SMMA) と同等。alpha = 1/period。
    初期値: 先頭 period 本の SMA。
    """
    n = len(series)
    result = np.full(n, np.nan)
    first = 0
    while first < n and np.isnan(series[first]):
        first += 1
    if first + period > n:
        return result
    result[first + period - 1] = series[first: first + period].mean()
    alpha = 1.0 / period
    if _HAS_SCIPY:
        result[first + period:] = _iir_ema(series[first + period:], alpha,
                                            result[first + period - 1])
    else:
        for i in range(first + period, n):
            result[i] = (result[i - 1] * (period - 1) + series[i]) / period
    return result


def _wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                period: int) -> np.ndarray:
    """Wilder's ATR（MQL4 の iATR と同等）。"""
    n = len(close)
    result = np.full(n, np.nan)
    if period >= n:
        return result

    hl  = high[1:] - low[1:]
    hcp = np.abs(high[1:] - close[:-1])
    lcp = np.abs(low[1:]  - close[:-1])
    tr  = np.full(n, np.nan)
    tr[1:] = np.maximum(hl, np.maximum(hcp, lcp))

    result[period] = tr[1:period + 1].mean()

    if period + 1 < n:
        if _HAS_SCIPY:
            result[period + 1:] = _iir_ema(tr[period + 1:], 1.0 / period, result[period])
        else:
            for i in range(period + 1, n):
                result[i] = (result[i - 1] * (period - 1) + tr[i]) / period
    return result


# ============================================================
# 公開インジケーター
# ============================================================

def rci(prices: np.ndarray, period: int) -> np.ndarray:
    """
    RCI（スピアマン順位相関係数）— MT4互換 competition ranking

    MT4 の CalcRCI() と完全同等:
      timeRank=1 = 最新バー, priceRank=1 = 最高値
      competition ranking: 同値には同一（最小）順位を付与

    時系列仕様:
      prices: 昇順（0=最古, n-1=最新）
      result[i] = バーiまでのperiod本で計算したRCI値
    """
    n = len(prices)
    result = np.full(n, np.nan)
    denom = period * (period * period - 1.0)
    if denom == 0 or n < period:
        return result

    wins = _windows(prices, period)     # (m, period) oldest→newest
    wins_rev = wins[:, ::-1].copy()     # (m, period) newest→oldest

    # Competition ranking (descending: highest price = rank 1)
    # rank[j] = 1 + count(k where wins_rev[k] > wins_rev[j])
    # ブロード broadcast で O(chunk * period²) メモリ。period=52 の場合チャンクで処理。
    m = wins_rev.shape[0]
    time_ranks = np.arange(1, period + 1, dtype=np.float64)
    d_sq_sum = np.zeros(m)
    CHUNK = 8000
    for start in range(0, m, CHUNK):
        end = min(start + CHUNK, m)
        chunk = wins_rev[start:end]                               # (c, p)
        # count_greater[i,j] = count k where chunk[i,k] > chunk[i,j]
        count_gt = (chunk[:, np.newaxis, :] > chunk[:, :, np.newaxis]).sum(axis=2)
        price_ranks = count_gt.astype(np.float64) + 1.0
        d = time_ranks - price_ranks
        d_sq_sum[start:end] = (d * d).sum(axis=1)

    result[period - 1:] = (1.0 - 6.0 * d_sq_sum / denom) * 100.0
    return result


def ema(prices: np.ndarray, period: int) -> np.ndarray:
    """EMA（指数移動平均）の公開版。MQL4 の iMA(MODE_EMA) と同等。"""
    return _ema(prices, period)


def sma(prices: np.ndarray, period: int) -> np.ndarray:
    """SMA（単純移動平均）の公開版。MQL4 の iMA(MODE_SMA) と同等。"""
    return _sma(prices, period)


def smma(prices: np.ndarray, period: int) -> np.ndarray:
    """SMMA（Smoothed Moving Average）の公開版。MQL4 の iMA(MODE_SMMA) と同等。"""
    return _smma(prices, period)


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """Wilder's ATR の公開版。MQL4 の iATR と同等。"""
    return _wilder_atr(high, low, close, period)


def macd(prices: np.ndarray, fast: int, slow: int,
         signal: int) -> Tuple[np.ndarray, np.ndarray]:
    """MACD ライン・シグナルラインを返す。戻り値: (macd_line, signal_line)"""
    macd_line = _ema(prices, fast) - _ema(prices, slow)
    sig_line = _ema(macd_line, signal)
    return macd_line, sig_line


def bollinger(prices: np.ndarray, period: int,
              deviation: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    ボリンジャーバンド（MT4 iBands準拠、ddof=0）。

    戻り値: (upper, middle, lower)
      middle[i] = SMA(period) at bar i
      upper[i]  = middle[i] + deviation * std(period, ddof=0)
      lower[i]  = middle[i] - deviation * std(period, ddof=0)
    """
    n = len(prices)
    upper  = np.full(n, np.nan)
    middle = np.full(n, np.nan)
    lower  = np.full(n, np.nan)
    if n < period:
        return upper, middle, lower

    wins = _windows(prices, period)           # (m, period)
    mask = ~np.any(np.isnan(wins), axis=1)
    mid_vals = wins[mask].mean(axis=1)
    std_vals = wins[mask].std(axis=1, ddof=0)

    middle[period - 1:][mask] = mid_vals
    upper[period - 1:][mask]  = mid_vals + deviation * std_vals
    lower[period - 1:][mask]  = mid_vals - deviation * std_vals
    return upper, middle, lower


def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        period: int = 14) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Wilder's ADX / +DI / -DI。MT4 の iADX と同等。

    戻り値: (adx_line, plus_di, minus_di)
    """
    n = len(close)
    adx_out   = np.full(n, np.nan)
    plus_out  = np.full(n, np.nan)
    minus_out = np.full(n, np.nan)
    if n < period * 2 + 1:
        return adx_out, plus_out, minus_out

    hl   = high[1:] - low[1:]
    hcp  = np.abs(high[1:] - close[:-1])
    lcp  = np.abs(low[1:]  - close[:-1])
    tr_raw  = np.maximum(hl, np.maximum(hcp, lcp))
    up      = high[1:] - high[:-1]
    dn      = low[:-1] - low[1:]
    pdm_raw = np.where((up > dn) & (up > 0), up, 0.0)
    mdm_raw = np.where((dn > up) & (dn > 0), dn, 0.0)

    alpha = 1.0 / period
    atr_s = np.full(n, np.nan)
    pdm_s = np.full(n, np.nan)
    mdm_s = np.full(n, np.nan)
    atr_s[period] = tr_raw[:period].mean()
    pdm_s[period] = pdm_raw[:period].mean()
    mdm_s[period] = mdm_raw[:period].mean()

    if _HAS_SCIPY:
        atr_s[period + 1:] = _iir_ema(tr_raw[period:],  alpha, atr_s[period])
        pdm_s[period + 1:] = _iir_ema(pdm_raw[period:], alpha, pdm_s[period])
        mdm_s[period + 1:] = _iir_ema(mdm_raw[period:], alpha, mdm_s[period])
    else:
        for i in range(period + 1, n):
            j = i - 1
            atr_s[i] = (atr_s[i - 1] * (period - 1) + tr_raw[j])  / period
            pdm_s[i] = (pdm_s[i - 1] * (period - 1) + pdm_raw[j]) / period
            mdm_s[i] = (mdm_s[i - 1] * (period - 1) + mdm_raw[j]) / period

    valid = ~np.isnan(atr_s) & (atr_s > 0)
    plus_out[valid]  = 100.0 * pdm_s[valid] / atr_s[valid]
    minus_out[valid] = 100.0 * mdm_s[valid] / atr_s[valid]

    dx = np.full(n, np.nan)
    denom = plus_out + minus_out
    nz = valid & (denom > 0)
    dx[nz] = 100.0 * np.abs(plus_out[nz] - minus_out[nz]) / denom[nz]

    adx_start = 2 * period
    if adx_start <= n:
        dx_slice = dx[period:adx_start]
        if not np.any(np.isnan(dx_slice)):
            adx_out[adx_start - 1] = dx_slice.mean()
            if adx_start < n:
                if _HAS_SCIPY:
                    adx_out[adx_start:] = _iir_ema(dx[adx_start:], alpha, adx_out[adx_start - 1])
                else:
                    for i in range(adx_start, n):
                        if not np.isnan(dx[i]):
                            adx_out[i] = (adx_out[i - 1] * (period - 1) + dx[i]) / period

    return adx_out, plus_out, minus_out
