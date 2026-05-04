# mt4-simulator

MT4 Open-Prices-Only (OPO) バックテストエンジンの Python 実装。

MT4 ストラテジーテスターの「Open prices only」モードと同一のバー処理順序を再現し、Python で EA ロジックを記述・検証できます。

## 特徴

- MT4 OPO モードと完全一致（1366/1366 トレードで検証済み）
- SL/TP、時間決済、ブレイクアウト待機、ドテン をすべてサポート
- インジケーター: EMA / SMA / SMMA / RCI / ATR / MACD / Bollinger / ADX
- `scipy` が入っている場合は EMA 系を C 実装で高速化

## インストール

```bash
pip install git+https://github.com/qwg02716/mt4-simulator.git
```

scipy による高速化を含める場合:

```bash
pip install "mt4-simulator[fast] @ git+https://github.com/qwg02716/mt4-simulator.git"
```

ローカル開発:

```bash
git clone https://github.com/qwg02716/mt4-simulator.git
pip install -e ./mt4-simulator
```

## クイックスタート

```python
from mt4_simulator.engine import OPOEngine, Strategy, BarContext, Signal, Position
from mt4_simulator.data import BarSeries
from mt4_simulator import exit_codes as ec
import pandas as pd

class MyStrategy(Strategy):
    def setup_indicators(self, ctx: BarContext) -> None:
        pass  # ctx.indicators["ema"] = indicators.ema(ctx.close, 20) など

    def on_bar(self, ctx: BarContext):
        # シグナル判定（確定済みバー ctx.i を使う）
        if some_condition:
            return Signal(direction=1, reason=ec.ENTRY_BUY)
        return None

    def on_position_opened(self, ctx: BarContext, pos: Position) -> None:
        pos.sl = pos.open_price - 5.0
        pos.tp = pos.open_price + 10.0

    def on_exit(self, ctx: BarContext, pos: Position):
        return None  # SL/TP のみで決済する場合

df = pd.read_csv("XAUUSD5.csv", ...)   # DatetimeIndex, open/high/low/close 列
bs = BarSeries.from_dataframe(df, server_utc_offset=2)

engine = OPOEngine(MyStrategy(), spread_pips=2.0, pip_point=0.1, warmup=200)
trades = engine.run(bs.open, bs.high, bs.low, bs.close,
                    bs.timestamps, bs.ts_hours_jst, bs.ts_dow)

for t in trades:
    print(t.entry_bar, t.exit_bar, t.profit_pips, t.exit_reason)
```

## バー処理順序

各バー `i` で以下の順に処理されます（MT4 OPO モードと同一）:

1. `on_sl_update_pre()` — SL/TP チェック前のトレール更新
2. SL/TP 判定（バー `i` の high/low）
3. `on_sl_update_post()` — SL/TP チェック後のトレール更新
4. `on_exit()` — 時間決済・RCI 決済など
5. ブレイクアウト待機のトリガー判定
6. `on_bar()` — エントリーシグナル検出
7. エントリー処理（即時 or ブレイクアウト待機）

エントリー価格は `open[i+1]`（次バー始値）を使用します。

## MT4 との整合

### タイムゾーン

MT4 の `iTime()` は UTC 秒を返しますが、CSV に保存されるバー時刻はサーバー時刻です。`BarSeries.from_dataframe()` の `server_utc_offset` に MT4 サーバーの UTC オフセットを指定してください（通常 `2` = UTC+2）。

```python
# MT4 サーバーが UTC+2 の場合
bs = BarSeries.from_dataframe(df, server_utc_offset=2)
```

### WARMUP

MT4 と同様に先頭 `warmup` バーはスキップされます（デフォルト 200）。`OPOEngine` の `warmup` 引数で変更できます。

## 検証

`tests/test_engine/` に Python/MT4 クロス検証ツールが含まれています。

```bash
# Python 側を実行
python tests/test_engine/run_test.py \
    --data XAUUSD5.csv --out python_out.csv \
    --start 2024-01-01 --end 2024-06-30

# MT4 側: mql4/TestSimulator.mq4 をストラテジーテスターで実行
# 出力: MQL4/Files/test_engine_mt4.csv

# 比較
python tests/test_engine/compare_csv.py \
    --py python_out.csv --mt4 test_engine_mt4.csv
```

## 動作環境

- Python 3.9+
- numpy >= 1.20
- pandas >= 1.3
- scipy >= 1.7 (任意、EMA 系の高速化)
