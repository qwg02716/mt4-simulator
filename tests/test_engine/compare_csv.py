#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Python vs MT4 OPOEngine テスト結果比較。

使い方:
  python test_engine/compare_csv.py --py test_engine_python.csv --mt4 test_engine_mt4.csv
"""
import argparse
import csv
import re
import sys

PRICE_TOL = 0.001   # 価格許容差 (0.001 = 0.01 pips @ pip_point=0.1)
PIPS_TOL  = 0.1     # pips 許容差


def load(path: str) -> list:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def norm_time(s: str) -> str:
    """日付区切り文字を統一 (2024.01.15 or 2024-01-15 → 2024.01.15)。"""
    return re.sub(r"[-/]", ".", s.strip())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--py",  required=True, help="Python 出力 CSV")
    ap.add_argument("--mt4", required=True, help="MT4 出力 CSV")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    py_rows  = load(args.py)
    mt4_rows = load(args.mt4)

    n_py, n_mt4 = len(py_rows), len(mt4_rows)
    print(f"Python: {n_py} trades   MT4: {n_mt4} trades")

    # entry_time_server でマッチング試行
    # 両方ソートして順序比較 (同一 entry_time が複数ある場合は方向も使う)
    def sort_key(r):
        return (norm_time(r.get("entry_time_server", "")), r.get("direction", ""))

    py_sorted  = sorted(py_rows,  key=sort_key)
    mt4_sorted = sorted(mt4_rows, key=sort_key)

    mismatches = []
    unmatched_py  = []
    unmatched_mt4 = []

    pi, mi = 0, 0
    while pi < len(py_sorted) and mi < len(mt4_sorted):
        p = py_sorted[pi]
        m = mt4_sorted[mi]
        p_key = sort_key(p)
        m_key = sort_key(m)

        if p_key == m_key:
            # 同一トレード: フィールド検査
            issues = []
            for field in ("direction",):
                if p.get(field) != m.get(field):
                    issues.append(f"{field}: py={p.get(field)!r} mt4={m.get(field)!r}")
            for field in ("entry_price", "exit_price"):
                try:
                    diff = abs(float(p[field]) - float(m[field]))
                    if diff > PRICE_TOL:
                        issues.append(f"{field}: py={p[field]} mt4={m[field]} diff={diff:.5f}")
                except (KeyError, ValueError):
                    issues.append(f"{field}: parse error py={p.get(field)} mt4={m.get(field)}")
            try:
                diff = abs(float(p["profit_pips"]) - float(m["profit_pips"]))
                if diff > PIPS_TOL:
                    issues.append(f"profit_pips: py={p['profit_pips']} mt4={m['profit_pips']}")
            except (KeyError, ValueError):
                pass
            if p.get("exit_reason") != m.get("exit_reason"):
                issues.append(f"exit_reason: py={p.get('exit_reason')} mt4={m.get('exit_reason')}")
            exit_py  = norm_time(p.get("exit_time_server", ""))
            exit_mt4 = norm_time(m.get("exit_time_server", ""))
            if exit_py != exit_mt4:
                issues.append(f"exit_time: py={exit_py} mt4={exit_mt4}")
            if issues:
                mismatches.append((p_key, issues))
            elif args.verbose:
                print(f"  OK  {p_key[0]} {p_key[1]}")
            pi += 1
            mi += 1
        elif p_key < m_key:
            unmatched_py.append(p)
            pi += 1
        else:
            unmatched_mt4.append(m)
            mi += 1

    unmatched_py.extend(py_sorted[pi:])
    unmatched_mt4.extend(mt4_sorted[mi:])

    # ── 結果表示 ──────────────────────────────────────────────────────
    matched = min(n_py, n_mt4) - len(mismatches) - len(unmatched_py) - len(unmatched_mt4)
    # matched の計算が複雑なので直接カウント
    total_compared = n_py + n_mt4 - len(unmatched_py) - len(unmatched_mt4)
    matched_pairs  = total_compared // 2

    print(f"\nMatched pairs: {matched_pairs}   Mismatches: {len(mismatches)}")
    print(f"Unmatched py-only: {len(unmatched_py)}   Unmatched mt4-only: {len(unmatched_mt4)}")

    if mismatches:
        print("\n=== MISMATCHES ===")
        for key, issues in mismatches[:30]:
            print(f"  {key[0]} {key[1]}: {'; '.join(issues)}")
        if len(mismatches) > 30:
            print(f"  ... and {len(mismatches)-30} more")

    if unmatched_py:
        print("\n=== Python のみ (MT4 に存在しない) ===")
        for r in unmatched_py[:10]:
            print(f"  {r.get('entry_time_server')} {r.get('direction')} "
                  f"exit={r.get('exit_reason')} pips={r.get('profit_pips')}")
        if len(unmatched_py) > 10:
            print(f"  ... +{len(unmatched_py)-10}")

    if unmatched_mt4:
        print("\n=== MT4 のみ (Python に存在しない) ===")
        for r in unmatched_mt4[:10]:
            print(f"  {r.get('entry_time_server')} {r.get('direction')} "
                  f"exit={r.get('exit_reason')} pips={r.get('profit_pips')}")
        if len(unmatched_mt4) > 10:
            print(f"  ... +{len(unmatched_mt4)-10}")

    if not mismatches and not unmatched_py and not unmatched_mt4:
        print("\n>>> OK: 全トレード完全一致 <<<")
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
