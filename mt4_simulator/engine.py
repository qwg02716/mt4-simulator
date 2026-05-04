# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
import numpy as np
from . import exit_codes as ec


@dataclass
class Position:
    direction: int          # 1=BUY, -1=SELL
    open_price: float
    open_bar: int           # エントリー実行バー（next_bar = signal_bar+1）
    signal_bar: int         # シグナル発生バー
    sl: float = 0.0         # 0.0 = SLなし
    tp: float = 0.0         # 0.0 = TPなし
    # 内部状態（Strategyが自由に使う追加フィールドはstate dictに）
    state: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Trade:
    entry_bar: int
    exit_bar: int
    signal_bar: int
    direction: int
    entry_price: float
    exit_price: float
    profit_pips: float
    exit_reason: int        # exit_codes定数
    entry_reason: int       # exit_codes ENTRY_*定数
    # 追加統計情報（Strategyがon_position_closedで設定可）
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Signal:
    direction: int          # 1=BUY, -1=SELL
    reason: int             # ENTRY_SELL/BUY等
    use_breakout: bool = False
    breakout_level: float = 0.0
    breakout_wait_bars: int = 0


@dataclass
class BarContext:
    """Strategyコールバックに渡されるバー情報。

    シフト仕様:
      - i は「現在の確定済みシグナルバー」（MT4のshift=1相当）
      - エントリー価格はopen[i+1]（次バー始値）
      - indicators[key][i] はそのバーの確定値
    """
    i: int
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    timestamps: np.ndarray
    ts_hours_jst: np.ndarray
    ts_dow: np.ndarray
    indicators: Dict[str, np.ndarray] = field(default_factory=dict)
    position: Optional[Position] = None
    spread: float = 0.0
    pip_point: float = 0.1


class Strategy(ABC):
    @abstractmethod
    def setup_indicators(self, ctx: BarContext) -> None:
        """バックテスト開始前に全インジケーターを事前計算してctx.indicatorsにセット。1回だけ呼ばれる。"""
        ...

    @abstractmethod
    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        """新バー確定時のエントリーシグナル判定。Noneならシグナルなし。"""
        ...

    @abstractmethod
    def on_exit(self, ctx: BarContext, pos: Position) -> Optional[Tuple[int, float]]:
        """SL/TP以外の決済判定。決済するなら (exit_codes定数, exit_price) を返す。Noneなら決済しない。"""
        ...

    def on_sl_update_pre(self, ctx: BarContext, pos: Position) -> float:
        """SL/TPチェック前のSLトレール更新（high_low_trail相当）。新SL値を返す。"""
        return pos.sl

    def on_sl_update_post(self, ctx: BarContext, pos: Position) -> float:
        """SL/TPチェック後のSLトレール更新（atr_trail/pips_trail相当）。新SL値を返す。"""
        return pos.sl

    def on_position_opened(self, ctx: BarContext, pos: Position) -> None:
        """ポジションオープン後。pos.sl/pos.tpを直接変更してSL/TP初期値を設定可。"""
        pass

    def on_position_closed(self, ctx: BarContext, trade: Trade) -> None:
        """ポジションクローズ後のコールバック。"""
        pass


class OPOEngine:
    """MT4 Open Prices Only バックテストエンジン。

    MT4 OPOモードのバー処理順序を正確に再現する:
      各バーiで:
        1. on_sl_update_pre()   ← high_low_trail（SL/TP前）
        2. SL/TP判定            ← バーのhigh/lowで判定
        3. on_sl_update_post()  ← atr_trail/pips_trail（SL後）
        4. on_exit()            ← RCI到達/バンド切れ/時間決済等
        5. ブレイクアウト待機中のトリガー判定
        6. on_bar()             ← 新シグナル検出
        7. エントリー処理        ← 即時or待機
    """

    def __init__(self, strategy: Strategy, spread_pips: float = 2.0,
                 pip_point: float = 0.1, warmup: int = 200):
        self.strategy = strategy
        self.spread = spread_pips * pip_point
        self.pip_point = pip_point
        self.warmup = warmup

    def run(self, open_: np.ndarray, high: np.ndarray, low: np.ndarray,
            close: np.ndarray, timestamps: np.ndarray,
            ts_hours_jst: np.ndarray, ts_dow: np.ndarray) -> List[Trade]:
        """バックテストを実行してトレードリストを返す。

        全配列は時系列昇順（0=最古バー）。
        """
        n = len(close)
        trades = []

        ctx = BarContext(
            i=0,
            open=open_, high=high, low=low, close=close,
            timestamps=timestamps,
            ts_hours_jst=ts_hours_jst, ts_dow=ts_dow,
            indicators={},
            position=None,
            spread=self.spread,
            pip_point=self.pip_point,
        )

        # インジケーター事前計算（1回だけ）
        self.strategy.setup_indicators(ctx)

        pos: Optional[Position] = None
        # ブレイクアウト待機状態
        brk_level: float = 0.0
        brk_bars_left: int = 0
        brk_signal: Optional[Signal] = None
        brk_signal_bar: int = 0

        for i in range(self.warmup, n - 1):
            ctx.i = i
            ctx.position = pos
            next_bar = i + 1

            # ========== ポジションあり: 決済処理 ==========
            if pos is not None:
                # 1. SL/TPチェック前トレール（high_low_trail）
                new_sl = self.strategy.on_sl_update_pre(ctx, pos)
                if new_sl != pos.sl:
                    pos.sl = new_sl

                # 2. SL/TP判定（バーのhigh/lowで判定）
                closed_trade = self._check_sl_tp(ctx, pos, i, next_bar)
                if closed_trade is not None:
                    self.strategy.on_position_closed(ctx, closed_trade)
                    trades.append(closed_trade)
                    pos = None
                    ctx.position = None
                    brk_bars_left = 0
                    brk_signal = None
                    # NO continue — fall through to entry signal check

                if pos is not None:
                    # 3. SL/TPチェック後トレール（atr_trail/pips_trail）
                    new_sl = self.strategy.on_sl_update_post(ctx, pos)
                    if new_sl != pos.sl:
                        pos.sl = new_sl

                    # 4. その他決済判定（RCI/バンド/時間等）
                    exit_result = self.strategy.on_exit(ctx, pos)
                    if exit_result is not None:
                        exit_reason, exit_price = exit_result
                        trade = self._make_trade(pos, i, exit_price, exit_reason)
                        self.strategy.on_position_closed(ctx, trade)
                        trades.append(trade)
                        pos = None
                        ctx.position = None
                        brk_bars_left = 0
                        brk_signal = None
                        # NO continue — fall through to entry signal check

            # ========== ブレイクアウト待機中 ==========
            # MT4挙動: シグナルバーの翌バー(signal_bar+1)ではトリガーチェックをスキップ。
            # on_bar()はBOウェイト中も常に呼び出す（新シグナルで上書き可能）。
            if brk_signal is not None and pos is None:
                if i != brk_signal_bar + 1:
                    triggered = False
                    if brk_signal.direction == 1:   # BUY breakout: 上抜け (ask > level)
                        triggered = (open_[i] + self.spread) > brk_level
                    else:                            # SELL breakout: 下抜け (bid < level)
                        triggered = open_[i] < brk_level
                    if triggered:
                        if brk_signal.direction == 1:
                            entry_price = open_[i] + self.spread
                        else:
                            entry_price = open_[i]
                        pos = Position(
                            direction=brk_signal.direction,
                            open_price=entry_price,
                            open_bar=i,
                            signal_bar=brk_signal_bar,
                        )
                        ctx.position = pos
                        self.strategy.on_position_opened(ctx, pos)
                        brk_signal = None
                        brk_bars_left = 0
                    else:
                        brk_bars_left -= 1
                        if brk_bars_left < 0:
                            brk_signal = None

            # ========== on_bar(): ポジションの有無に関わらず常に呼ぶ ==========
            # BOウェイト中も呼び出す（新シグナルが古いBOを上書きする）
            ctx.position = pos  # 決済後の状態を反映
            signal = self.strategy.on_bar(ctx)

            if signal is not None:
                if pos is not None:
                    # ドテン: 逆方向シグナルの場合のみ
                    if signal.direction != pos.direction:
                        if pos.direction == -1:  # SELL → reverse to BUY
                            reverse_price = open_[next_bar] + self.spread
                        else:  # BUY → reverse to SELL
                            reverse_price = open_[next_bar]
                        close_trade = self._make_trade(pos, next_bar, reverse_price, ec.EXIT_REVERSE)
                        self.strategy.on_position_closed(ctx, close_trade)
                        trades.append(close_trade)
                        pos = None
                        brk_signal = None
                        brk_bars_left = 0
                        # ドテン: 即時エントリー（breakout待機なし）
                        entry_price = reverse_price
                        pos = Position(
                            direction=signal.direction,
                            open_price=entry_price,
                            open_bar=next_bar,
                            signal_bar=i,
                        )
                        ctx.position = pos
                        self.strategy.on_position_opened(ctx, pos)
                    # 同方向: 無視（on_bar()内でフラグ更新は済み）
                else:
                    # 新規エントリー（BOウェイト中は新シグナルが上書き）
                    if not signal.use_breakout:
                        brk_signal = None
                        brk_bars_left = 0
                        if signal.direction == 1:  # BUY
                            entry_price = open_[next_bar] + self.spread
                        else:  # SELL
                            entry_price = open_[next_bar]
                        pos = Position(
                            direction=signal.direction,
                            open_price=entry_price,
                            open_bar=next_bar,
                            signal_bar=i,
                        )
                        ctx.position = pos
                        self.strategy.on_position_opened(ctx, pos)
                    else:
                        # ブレイクアウト待機（新シグナルで上書き）
                        brk_level = signal.breakout_level
                        brk_bars_left = signal.breakout_wait_bars
                        brk_signal = signal
                        brk_signal_bar = i

        # データ終端での強制決済
        if pos is not None:
            exit_price = close[n - 1]
            trade = self._make_trade(pos, n - 1, exit_price, ec.EXIT_END)
            trades.append(trade)

        return trades

    def _check_sl_tp(self, ctx: BarContext, pos: Position, i: int, next_bar: int) -> Optional[Trade]:
        """SL/TP判定。発動した場合はTradeを返す。"""
        h = ctx.high[i]
        l = ctx.low[i]
        if pos.direction == 1:  # BUY
            if pos.sl > 0.0 and l <= pos.sl:
                return self._make_trade(pos, i, pos.sl, ec.EXIT_SL)
            if pos.tp > 0.0 and h >= pos.tp:
                return self._make_trade(pos, i, pos.tp, ec.EXIT_TP)
        else:  # SELL
            if pos.sl > 0.0 and h >= pos.sl:
                return self._make_trade(pos, i, pos.sl, ec.EXIT_SL)
            if pos.tp > 0.0 and l <= pos.tp:
                return self._make_trade(pos, i, pos.tp, ec.EXIT_TP)
        return None

    def _make_trade(self, pos: Position, exit_bar: int, exit_price: float,
                    exit_reason: int) -> Trade:
        """Tradeオブジェクトを生成する。"""
        profit_pips = (exit_price - pos.open_price) * pos.direction / self.pip_point
        return Trade(
            entry_bar=pos.open_bar,
            exit_bar=exit_bar,
            signal_bar=pos.signal_bar,
            direction=pos.direction,
            entry_price=pos.open_price,
            exit_price=exit_price,
            profit_pips=profit_pips,
            exit_reason=exit_reason,
            entry_reason=pos.state.get("entry_reason", ec.ENTRY_BUY if pos.direction == 1 else ec.ENTRY_SELL),
        )
