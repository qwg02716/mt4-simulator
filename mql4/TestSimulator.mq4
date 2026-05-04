//+------------------------------------------------------------------+
//| TestSimulator.mq4 — OPOEngine isolation test EA                  |
//|                                                                   |
//| 用途: Python OPOEngine との完全一致を検証する。                   |
//| test_engine/test_strategy.py と同一のロジックを実装。             |
//|                                                                   |
//| 実行方法:                                                         |
//|   ストラテジーテスター → Expert Advisor                           |
//|   モデル: Open prices only                                        |
//|   通貨ペア: XAUUSD、時間足: M5                                    |
//|   期間: Python の --start --end と同じ範囲を指定                  |
//|   スプレッド: 20 (= 2.0 pips, SPREAD_PIPS と一致)                |
//|                                                                   |
//| 出力: MQL4/Files/test_engine_mt4.csv                              |
//|                                                                   |
//| 【シグナル条件 (test_strategy.py と完全一致)】                    |
//|   signal_mod = ((bar_server_time - EPOCH_SERVER) / BAR_PERIOD) % 100  |
//|   mod == 10  → BUY  即時                                          |
//|   mod == 35  → SELL 即時 (保有中なら ドテン)                     |
//|   mod == 55  → BUY  ブレイクアウト (high[i] 超え, wait=8)        |
//|   mod == 80  → SELL 即時                                          |
//+------------------------------------------------------------------+
#property strict

// ─── 設定 (test_strategy.py の定数と合わせること) ─────────────────
extern double SPREAD_PIPS     = 2.0;
extern double PIP_POINT       = 0.1;
extern int    WARMUP          = 200;
extern int    TIME_EXIT_BARS  = 20;
extern double SL_PRICE        = 5.0;    // 価格単位 (50 pips @ 0.1)
extern double TP_PRICE        = 10.0;   // 価格単位 (100 pips @ 0.1)
extern int    BO_WAIT_BARS    = 8;
extern int    BAR_PERIOD_MIN  = 5;      // M5 = 5
extern int    SERVER_UTC_OFFSET = 2;    // UTC+2
extern string OUTPUT_FILE     = "test_engine_mt4.csv";

// エポック: 2000-01-03 02:00 サーバー時刻 (UTC+2) ← Python 側は UTC 2000-01-03 00:00
// サーバー UTC+2 なので +7200秒
datetime EPOCH_SERVER = D'2000.01.03 02:00';

// ─── 決済コード ────────────────────────────────────────────────────
#define EXIT_SL      1
#define EXIT_TP      2
#define EXIT_TIME    9
#define EXIT_REVERSE 10
#define EXIT_END     11

// ─── ポジション状態 ────────────────────────────────────────────────
int    g_pos_dir;        // 1=BUY, -1=SELL, 0=なし
double g_pos_entry;
int    g_pos_open_bar;   // Python open_bar 相当 (g_bar_abs ベース)
int    g_pos_sig_bar;
double g_pos_sl;
double g_pos_tp;
int    g_pos_teb;        // time_exit_bar = open_bar + TIME_EXIT_BARS
datetime g_pos_entry_time;

// ─── ブレイクアウト待機 ────────────────────────────────────────────
bool   g_brk_active;
int    g_brk_dir;
double g_brk_level;
int    g_brk_left;
int    g_brk_sig_bar;    // g_bar_abs ベース

// ─── バー絶対インデックス ──────────────────────────────────────────
// g_bar_abs = Python i に対応する 0 始まりのカウンター。
// 各 OnTick の先頭でインクリメント (初期値 -1 → 最初の OnTick で 0)。
// shift=1 が存在しない最初の 1 tick は Bars <= 1 でガード。
int    g_bar_abs;

// ─── トレード記録 (並列配列) ──────────────────────────────────────
int      g_t_entry_bar[];
int      g_t_exit_bar[];
int      g_t_dir[];
double   g_t_entry_p[];
double   g_t_exit_p[];
double   g_t_profit[];
int      g_t_reason[];
datetime g_t_entry_time[];
datetime g_t_exit_time[];
int      g_trade_n;

//+------------------------------------------------------------------+
void OnInit() {
   g_pos_dir   = 0;
   g_brk_active = false;
   // g_bar_abs = -2 にする理由:
   // 各 OnTick の先頭で ++。最初の tick (Bars=1, shift=1 未確定) で g_bar_abs=−1 → Bars<=1 でスキップ。
   // 2 番目の tick (Bars=2, shift=1=bar0) で g_bar_abs=0 = Python i=0 に対応。
   // こうすることで MT4 の g_bar_abs k のとき shift=1 = Python bar k と一致する。
   g_bar_abs   = -2;
   g_trade_n   = 0;
   ArrayResize(g_t_entry_bar,  0);
   ArrayResize(g_t_exit_bar,   0);
   ArrayResize(g_t_dir,        0);
   ArrayResize(g_t_entry_p,    0);
   ArrayResize(g_t_exit_p,     0);
   ArrayResize(g_t_profit,     0);
   ArrayResize(g_t_reason,     0);
   ArrayResize(g_t_entry_time, 0);
   ArrayResize(g_t_exit_time,  0);
}

void OnDeinit(const int reason) {
   // データ終端での強制決済
   if (g_pos_dir != 0) {
      double last_close = iClose(NULL, 0, 0);
      datetime last_time = iTime(NULL, 0, 0);
      RecordClose(g_bar_abs + 1, last_close, EXIT_END, last_time);
      g_pos_dir = 0;
   }
   WriteCSV();
}

//+------------------------------------------------------------------+
// signal_mod の計算 (test_strategy.py の _signal_mod() と等価)。
// bar_time: shift=1 のサーバー時刻 (datetime = Unix 秒)
//+------------------------------------------------------------------+
int SignalMod(datetime bar_time) {
   long sec = (long)bar_time - (long)EPOCH_SERVER;
   if (sec < 0) return -1;
   long idx = sec / (BAR_PERIOD_MIN * 60);
   return (int)(idx % 100);
}

//+------------------------------------------------------------------+
void OnTick() {
   g_bar_abs++;
   int i = g_bar_abs;

   // warmup 前 or shift=1 未確定はスキップ
   if (Bars <= 1 || i < WARMUP) return;

   double spread = SPREAD_PIPS * PIP_POINT;

   // バー i (shift=1) の OHLC
   double h1 = iHigh(NULL, 0, 1);
   double l1 = iLow(NULL, 0, 1);
   double o1 = iOpen(NULL, 0, 1);
   datetime t1 = iTime(NULL, 0, 1);   // バー i のサーバー時刻

   // バー i+1 (shift=0) の始値
   double o0 = iOpen(NULL, 0, 0);
   datetime t0 = iTime(NULL, 0, 0);

   // ═══ 1. SL/TP 判定 (バー i の high/low) ═══════════════════════
   if (g_pos_dir != 0) {
      bool sltp_closed = false;
      if (g_pos_dir == 1) {  // BUY
         if (g_pos_sl > 0 && l1 <= g_pos_sl) {
            RecordClose(i, g_pos_sl, EXIT_SL, t1);
            g_pos_dir = 0; sltp_closed = true;
         } else if (g_pos_tp > 0 && h1 >= g_pos_tp) {
            RecordClose(i, g_pos_tp, EXIT_TP, t1);
            g_pos_dir = 0; sltp_closed = true;
         }
      } else {  // SELL
         if (g_pos_sl > 0 && h1 >= g_pos_sl) {
            RecordClose(i, g_pos_sl, EXIT_SL, t1);
            g_pos_dir = 0; sltp_closed = true;
         } else if (g_pos_tp > 0 && l1 <= g_pos_tp) {
            RecordClose(i, g_pos_tp, EXIT_TP, t1);
            g_pos_dir = 0; sltp_closed = true;
         }
      }
      if (sltp_closed) {
         g_brk_active = false;
         g_brk_left   = 0;
      }
   }

   // ═══ 2. 時間決済 (on_exit 相当, exit at open[i] = o1) ═════════
   if (g_pos_dir != 0) {
      if (i >= g_pos_teb) {
         RecordClose(i, o1, EXIT_TIME, t1);
         g_pos_dir    = 0;
         g_brk_active = false;
         g_brk_left   = 0;
      }
   }

   // ═══ 3. ブレイクアウト待機チェック ════════════════════════════
   // signal_bar+1 の tick はスキップ (OPOEngine の挙動に一致)
   if (g_brk_active && g_pos_dir == 0) {
      if (i != g_brk_sig_bar + 1) {
         bool triggered = false;
         if (g_brk_dir == 1) {
            triggered = (o1 + spread) > g_brk_level;
         } else {
            triggered = o1 < g_brk_level;
         }
         if (triggered) {
            double ep = (g_brk_dir == 1) ? o1 + spread : o1;
            OpenPos(g_brk_dir, ep, i, g_brk_sig_bar, t1);
            g_brk_active = false;
         } else {
            g_brk_left--;
            if (g_brk_left < 0) g_brk_active = false;
         }
      }
   }

   // ═══ 4. on_bar(): シグナル検出 (バー i のタイムスタンプ基準) ══
   int mod = SignalMod(t1);
   int sig_dir = 0;
   bool use_bo = false;
   double bo_level = 0;

   if      (mod == 10) { sig_dir =  1; }
   else if (mod == 35) { sig_dir = -1; }
   else if (mod == 55) { sig_dir =  1; use_bo = true; bo_level = h1; }
   else if (mod == 80) { sig_dir = -1; }

   if (sig_dir != 0) {
      if (g_pos_dir != 0 && sig_dir != g_pos_dir) {
         // ドテン: 逆方向シグナル → 次バー始値でクローズ & 即時エントリー
         double rev = (sig_dir == 1) ? o0 + spread : o0;
         RecordClose(i + 1, rev, EXIT_REVERSE, t0);
         g_pos_dir    = 0;
         g_brk_active = false;
         OpenPos(sig_dir, rev, i + 1, i, t0);
      } else if (g_pos_dir == 0) {
         if (!use_bo) {
            // 即時エントリー (次バー始値)
            g_brk_active = false;
            double ep = (sig_dir == 1) ? o0 + spread : o0;
            OpenPos(sig_dir, ep, i + 1, i, t0);
         } else {
            // ブレイクアウト待機 (新シグナルが古い BO を上書き)
            g_brk_dir      = sig_dir;
            g_brk_level    = bo_level;
            g_brk_left     = BO_WAIT_BARS;
            g_brk_sig_bar  = i;
            g_brk_active   = true;
         }
      }
      // 同方向シグナルは無視 (OPOEngine と同じ)
   }
}

//+------------------------------------------------------------------+
void OpenPos(int dir, double entry, int open_bar, int sig_bar, datetime entry_time) {
   g_pos_dir        = dir;
   g_pos_entry      = entry;
   g_pos_open_bar   = open_bar;
   g_pos_sig_bar    = sig_bar;
   g_pos_entry_time = entry_time;
   if (dir == 1) {
      g_pos_sl = entry - SL_PRICE;
      g_pos_tp = entry + TP_PRICE;
   } else {
      g_pos_sl = entry + SL_PRICE;
      g_pos_tp = entry - TP_PRICE;
   }
   g_pos_teb = open_bar + TIME_EXIT_BARS;
}

//+------------------------------------------------------------------+
void RecordClose(int exit_bar, double exit_price, int reason, datetime exit_time) {
   int n = g_trade_n;
   ArrayResize(g_t_entry_bar,  n + 1);
   ArrayResize(g_t_exit_bar,   n + 1);
   ArrayResize(g_t_dir,        n + 1);
   ArrayResize(g_t_entry_p,    n + 1);
   ArrayResize(g_t_exit_p,     n + 1);
   ArrayResize(g_t_profit,     n + 1);
   ArrayResize(g_t_reason,     n + 1);
   ArrayResize(g_t_entry_time, n + 1);
   ArrayResize(g_t_exit_time,  n + 1);

   g_t_entry_bar[n]  = g_pos_open_bar;
   g_t_exit_bar[n]   = exit_bar;
   g_t_dir[n]        = g_pos_dir;
   g_t_entry_p[n]    = g_pos_entry;
   g_t_exit_p[n]     = exit_price;
   g_t_profit[n]     = (exit_price - g_pos_entry) * g_pos_dir / PIP_POINT;
   g_t_reason[n]     = reason;
   g_t_entry_time[n] = g_pos_entry_time;
   g_t_exit_time[n]  = exit_time;
   g_trade_n = n + 1;
}

//+------------------------------------------------------------------+
string ExitStr(int r) {
   if (r == EXIT_SL)      return "SL";
   if (r == EXIT_TP)      return "TP";
   if (r == EXIT_TIME)    return "TimeExit";
   if (r == EXIT_REVERSE) return "Reverse";
   if (r == EXIT_END)     return "EndOfData";
   return "Unknown";
}

//+------------------------------------------------------------------+
void WriteCSV() {
   int fh = FileOpen(OUTPUT_FILE, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if (fh == INVALID_HANDLE) {
      Print("ERROR: FileOpen failed for ", OUTPUT_FILE);
      return;
   }
   FileWrite(fh,
      "entry_bar", "exit_bar", "direction",
      "entry_price", "exit_price", "profit_pips",
      "exit_reason", "entry_time_server", "exit_time_server");

   for (int k = 0; k < g_trade_n; k++) {
      FileWrite(fh,
         g_t_entry_bar[k],
         g_t_exit_bar[k],
         (g_t_dir[k] == 1 ? "BUY" : "SELL"),
         DoubleToStr(g_t_entry_p[k], 5),
         DoubleToStr(g_t_exit_p[k], 5),
         DoubleToStr(g_t_profit[k], 1),
         ExitStr(g_t_reason[k]),
         TimeToStr(g_t_entry_time[k], TIME_DATE | TIME_MINUTES),
         TimeToStr(g_t_exit_time[k],  TIME_DATE | TIME_MINUTES)
      );
   }
   FileClose(fh);
   Print("CSV written: MQL4/Files/", OUTPUT_FILE, " (", g_trade_n, " trades)");
}
