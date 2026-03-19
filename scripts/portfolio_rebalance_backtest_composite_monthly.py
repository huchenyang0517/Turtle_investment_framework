#!/usr/bin/env python3
"""Rebalance backtest using final composite_score (Tier1+Tier2).

策略：
1) 每个“月末交易日”运行龟龟选股器（Tier1+Tier2），按最终 composite_score 取 TopK（默认 10）
2) 如果“前 TopK 的股票集合”相对上次月末发生变化，则在本月末收盘调仓到新的 TopK（等权、一次性全仓）
3) 若集合未变化，则持有不动
4) 与沪深300（默认 399300.SZ）进行净值曲线对比，并输出可视化

重要提示（性能/限频）：
- Tier2 会对最多 200 只股票做深度分析；十年按月运行仍然很重。
- 你可以用参数 --tier2-limit 缩小 Tier2 计算范围（得到的是“近似 TopK”）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import time
from typing import Optional, Dict, List, Tuple, Set

import numpy as np
import pandas as pd
import tushare as ts
from matplotlib import pyplot as plt
import warnings

from config import get_token
from screener_config import ScreenerConfig
from screener_core import TushareScreener

# Silence tushare FutureWarning caused by pandas deprecations.
# It is usually non-fatal and we only suppress this specific message.
warnings.filterwarnings(
    "ignore",
    category=FutureWarning,
    message="Series\\.fillna with 'method' is deprecated.*",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebalance backtest using composite_score (Tier1+Tier2)."
    )
    parser.add_argument(
        "--rebalance-freq",
        choices=["quarterly", "monthly", "weekly", "daily"],
        default="monthly",
        help="Evaluation frequency: 'quarterly' = quarter-end; 'monthly' = month-end; 'weekly' = week-end; 'daily' = every trading day.",
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    parser.add_argument("--years", type=int, default=10, help="Backtest years (approx).")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD")
    parser.add_argument(
        "--adj",
        choices=["qfq", "hfq", "none"],
        default="hfq",
        help="Adjustment type for price series.",
    )
    parser.add_argument(
        "--benchmarks",
        default="399300.SZ",
        help="Comma-separated index codes, e.g. 399300.SZ,000300.SH",
    )
    parser.add_argument(
        "--tier2-limit",
        type=int,
        default=200,
        help="Override the number of stocks for Tier2 (default 200 = full Tier1 candidates).",
    )
    parser.add_argument(
        "--cache-dir",
        default="output/.composite_monthly_cache",
        help="Local cache dir for monthly topK and price series.",
    )
    parser.add_argument(
        "--min-call-interval",
        type=float,
        default=0.3,
        help="Min seconds between price/index API calls made by this script (not inside screener).",
    )
    parser.add_argument(
        "--rate-limit-retry",
        type=int,
        default=6,
        help="Retry count when hitting Tushare rate limit for price/index calls.",
    )
    parser.add_argument(
        "--verbose-rebalance",
        action="store_true",
        help="Print detailed rebalance logs (TopK with company name).",
    )
    parser.add_argument(
        "--verbose-max-events",
        type=int,
        default=50,
        help="When verbose-rebalance is on, max printed events (incl. first).",
    )
    parser.add_argument(
        "--require-adj",
        action="store_true",
        help="要求复权价格必须通过 pro_bar(..., adj=...) 获取成功；失败则直接报错退出（不会回退到 pro.daily）。",
    )
    parser.add_argument(
        "--api-sleep-seconds",
        type=float,
        default=0.3,
        help="Screener 内部每次 API 调用之间的最小等待（秒）。过小可能触发限频，过大耗时更久。(200次/分钟限制下最优值=0.3)",
    )
    parser.add_argument(
        "--max-months",
        type=int,
        default=0,
        help="仅用于快速验证：最多回测多少个月末交易日（0=不限制）。",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=1,
        help="打印进度的间隔月份数（1=每个月都打印；例如 3=每3个月打印一次）。",
    )
    return parser.parse_args()


def _ymd(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def _ensure_date(x: Optional[str], default_dt: dt.date) -> dt.date:
    if x is None:
        return default_dt
    return pd.to_datetime(x).date()


class TushareRateLimiter:
    def __init__(self, min_interval_seconds: float):
        self.min_interval_seconds = float(min_interval_seconds)
        self._last_call_ts: Optional[float] = None

    def wait(self) -> None:
        if self._last_call_ts is None:
            self._last_call_ts = time.monotonic()
            return
        elapsed = time.monotonic() - self._last_call_ts
        remain = self.min_interval_seconds - elapsed
        if remain > 0:
            time.sleep(remain)
        self._last_call_ts = time.monotonic()


def _is_rate_limit_error(err: Exception) -> bool:
    msg = str(err)
    return ("每分钟最多访问" in msg and "次" in msg) or ("rate limit" in msg.lower())


def _call_with_retry(fn, rate_limiter: TushareRateLimiter, retry_count: int, *args, **kwargs):
    last_err: Optional[Exception] = None
    for attempt in range(1, retry_count + 1):
        try:
            rate_limiter.wait()
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if _is_rate_limit_error(e):
                sleep_s = min(60, 8 * attempt)
                print(f"[WARN] hit rate limit, sleep {sleep_s}s then retry ({attempt}/{retry_count})")
                time.sleep(sleep_s)
                continue
            raise
    if last_err is not None:
        raise last_err
    raise RuntimeError("Tushare call failed (unknown error).")


def _month_end_trade_days(
    pro: ts.pro_api,
    exchange: str,
    start_date: dt.date,
    end_date: dt.date,
) -> List[dt.date]:
    cal = pro.trade_cal(
        exchange=exchange,
        start_date=_ymd(start_date),
        end_date=_ymd(end_date),
        fields="cal_date,is_open",
    )
    if cal is None or cal.empty:
        return []

    cal["cal_date"] = pd.to_datetime(cal["cal_date"])
    cal = cal[cal["is_open"] == 1].sort_values("cal_date")
    cal["ym"] = cal["cal_date"].dt.strftime("%Y-%m")
    # last open day each month
    month_ends = cal.groupby("ym", as_index=False).apply(lambda g: g.iloc[-1])
    month_ends = month_ends.sort_values("cal_date")
    return [d.date() for d in month_ends["cal_date"]]


def _quarter_end_trade_days(
    pro: ts.pro_api,
    exchange: str,
    start_date: dt.date,
    end_date: dt.date,
) -> List[dt.date]:
    cal = pro.trade_cal(
        exchange=exchange,
        start_date=_ymd(start_date),
        end_date=_ymd(end_date),
        fields="cal_date,is_open",
    )
    if cal is None or cal.empty:
        return []
    cal["cal_date"] = pd.to_datetime(cal["cal_date"])
    cal = cal[cal["is_open"] == 1].sort_values("cal_date")
    cal["yq"] = cal["cal_date"].dt.to_period("Q").astype(str)
    quarter_ends = cal.groupby("yq", as_index=False).apply(lambda g: g.iloc[-1])
    quarter_ends = quarter_ends.sort_values("cal_date")
    return [d.date() for d in quarter_ends["cal_date"]]


def _week_end_trade_days(
    pro: ts.pro_api,
    exchange: str,
    start_date: dt.date,
    end_date: dt.date,
) -> List[dt.date]:
    cal = pro.trade_cal(
        exchange=exchange,
        start_date=_ymd(start_date),
        end_date=_ymd(end_date),
        fields="cal_date,is_open",
    )
    if cal is None or cal.empty:
        return []
    cal["cal_date"] = pd.to_datetime(cal["cal_date"])
    cal = cal[cal["is_open"] == 1].sort_values("cal_date")
    cal["yw"] = cal["cal_date"].dt.strftime("%G-%V")
    week_ends = cal.groupby("yw", as_index=False).apply(lambda g: g.iloc[-1])
    week_ends = week_ends.sort_values("cal_date")
    return [d.date() for d in week_ends["cal_date"]]


def _all_trade_days(
    pro: ts.pro_api,
    exchange: str,
    start_date: dt.date,
    end_date: dt.date,
) -> List[dt.date]:
    cal = pro.trade_cal(
        exchange=exchange,
        start_date=_ymd(start_date),
        end_date=_ymd(end_date),
        fields="cal_date,is_open",
    )
    if cal is None or cal.empty:
        return []
    cal["cal_date"] = pd.to_datetime(cal["cal_date"])
    cal = cal[cal["is_open"] == 1].sort_values("cal_date")
    return [d.date() for d in cal["cal_date"]]


def _cache_path(cache_dir: str, suffix: str) -> str:
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, suffix)


def _fetch_stock_close_series(
    pro: ts.pro_api,
    ts_code: str,
    start_date: dt.date,
    end_date: dt.date,
    adj: str,
    cache_dir: str,
    rate_limiter: TushareRateLimiter,
    rate_limit_retry: int,
    require_adj: bool,
) -> pd.Series:
    cache_file = _cache_path(
        cache_dir,
        f"prices_{ts_code}_{adj}_{_ymd(start_date)}_{_ymd(end_date)}.parquet",
    )
    if os.path.exists(cache_file):
        df = pd.read_parquet(cache_file)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values("trade_date")
        return pd.Series(
            df["close"].astype(float).values,
            index=df["trade_date"].dt.date.values,
            name=ts_code,
        )

    start_str = _ymd(start_date)
    end_str = _ymd(end_date)

    df = None
    if adj != "none":
        try:
            df = _call_with_retry(
                ts.pro_bar,
                rate_limiter=rate_limiter,
                retry_count=rate_limit_retry,
                ts_code=ts_code,
                api=pro,
                adj=adj,
                start_date=start_str,
                end_date=end_str,
                freq="D",
            )
        except Exception as e:  # noqa: BLE001
            if require_adj:
                raise RuntimeError(f"require-adj enabled: pro_bar({ts_code}, adj={adj}) failed: {e!r}")
            print(f"[WARN] pro_bar({ts_code}, adj={adj}) failed: {e!r}; fallback to pro.daily raw close.")
            df = None

    if df is None:
        df = _call_with_retry(
            pro.daily,
            rate_limiter=rate_limiter,
            retry_count=rate_limit_retry,
            ts_code=ts_code,
            start_date=start_str,
            end_date=end_str,
        )

    if df is None or df.empty:
        return pd.Series(dtype="float64", name=ts_code)

    df = df.sort_values("trade_date")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    out_df = pd.DataFrame({"trade_date": df["trade_date"], "close": df["close"].astype(float)})
    out_df.to_parquet(cache_file, index=False)

    return pd.Series(
        out_df["close"].values,
        index=out_df["trade_date"].dt.date.values,
        name=ts_code,
    )


def _fetch_index_daily(
    pro: ts.pro_api,
    ts_code: str,
    start_date: dt.date,
    end_date: dt.date,
    rate_limiter: TushareRateLimiter,
    rate_limit_retry: int,
) -> pd.Series:
    df = _call_with_retry(
        pro.index_daily,
        rate_limiter=rate_limiter,
        retry_count=rate_limit_retry,
        ts_code=ts_code,
        start_date=_ymd(start_date),
        end_date=_ymd(end_date),
    )
    if df is None or df.empty:
        return pd.Series(dtype="float64", name=ts_code)
    df = df.sort_values("trade_date")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return pd.Series(df["close"].astype(float).values, index=df["trade_date"].dt.date.values, name=ts_code)


def _calc_max_drawdown(values: pd.Series) -> float:
    """Max drawdown from a NAV/price series (negative value)."""
    if values is None or len(values) < 2:
        return np.nan
    running_max = values.cummax()
    dd = values / running_max - 1.0
    return float(dd.min())


def _calc_annualized_return(values: pd.Series) -> float:
    """CAGR from a NAV/price series."""
    if values is None or len(values) < 2:
        return np.nan
    total_return = float(values.iloc[-1] / values.iloc[0] - 1.0)
    days = (values.index[-1] - values.index[0]).days
    years = days / 365.0 if days > 0 else 0.0
    if years <= 0:
        return np.nan
    return float((1.0 + total_return) ** (1.0 / years) - 1.0)


def _calc_simple_avg_annual_return(values: pd.Series) -> float:
    """Simple average of annual returns (arithmetic mean).

    方法：
    - 按自然年度切分；
    - 每年计算 annual_return_y = last_y / first_y - 1；
    - 对所有年份的 annual_return_y 做算术平均。
    """
    if values is None or len(values) < 2:
        return np.nan

    if not isinstance(values.index, pd.DatetimeIndex):
        values = values.copy()
        values.index = pd.to_datetime(values.index)

    annual_rets: List[float] = []
    for y in sorted(set(values.index.year)):
        v_year = values[values.index.year == y]
        if len(v_year) < 2:
            continue
        first = float(v_year.iloc[0])
        last = float(v_year.iloc[-1])
        if first == 0:
            continue
        annual_rets.append(last / first - 1.0)

    if not annual_rets:
        return np.nan
    return float(np.mean(annual_rets))


def main() -> None:
    args = parse_args()

    end_dt = _ensure_date(args.end_date, dt.date.today())
    start_dt = _ensure_date(args.start_date, end_dt - dt.timedelta(days=365 * args.years))

    ts.set_token(get_token())
    pro = ts.pro_api(timeout=30)

    rate_limiter = TushareRateLimiter(min_interval_seconds=args.min_call_interval)

    freq = args.rebalance_freq  # "monthly" or "daily"
    freq_tag = freq

    if freq == "quarterly":
        eval_days = _quarter_end_trade_days(pro, exchange="SSE", start_date=start_dt, end_date=end_dt)
    elif freq == "monthly":
        eval_days = _month_end_trade_days(pro, exchange="SSE", start_date=start_dt, end_date=end_dt)
    elif freq == "weekly":
        eval_days = _week_end_trade_days(pro, exchange="SSE", start_date=start_dt, end_date=end_dt)
    else:
        eval_days = _all_trade_days(pro, exchange="SSE", start_date=start_dt, end_date=end_dt)

    if not eval_days:
        raise RuntimeError("No evaluation trade days found in range.")
    if args.max_months and args.max_months > 0:
        eval_days = eval_days[: args.max_months]

    cfg = ScreenerConfig()
    cfg.api_sleep_seconds = float(args.api_sleep_seconds)
    # 让回测时的 screener 缓存尽量长期，不然长跑会失效
    cfg.cache_stock_basic_ttl_days = 3650
    cfg.cache_daily_basic_ttl_days = 3650
    cfg.cache_tier2_ttl_hours = 24 * 3650
    cfg.cache_tier2_financial_ttl_hours = 24 * 3650
    cfg.cache_tier2_market_ttl_hours = 24 * 3650
    cfg.cache_tier2_global_ttl_hours = 24 * 3650

    screener = TushareScreener(token=get_token(), config=cfg)

    # Monthly topK cache
    cache_dir = args.cache_dir
    topk_cache_dir = _cache_path(cache_dir, f"{freq_tag}_topk")

    prev_top_set: Optional[Set[str]] = None
    events: List[Tuple[dt.date, pd.DataFrame]] = []  # (event_day, topk_df)
    name_by_code: Dict[str, str] = {}

    t0 = time.monotonic()
    print(f"[INFO] Total month-ends to scan: {len(eval_days)}")

    for i, day in enumerate(eval_days):
        if args.progress_every > 0 and (i % args.progress_every == 0):
            elapsed = time.monotonic() - t0
            print(
                f"[PROGRESS] {i+1}/{len(eval_days)} month_end={day} elapsed={elapsed/60:.1f}min events={len(events)}"
            )
        trade_date_str = _ymd(day)
        cache_file = _cache_path(
            topk_cache_dir,
            f"topk_{trade_date_str}_tk{args.top_k}_t2{args.tier2_limit}.csv",
        )
        topk_df: Optional[pd.DataFrame] = None
        cache_hit = False

        if os.path.exists(cache_file):
            tmp = pd.read_csv(cache_file)
            # ensure expected cols
            if "ts_code" in tmp.columns and "composite_score" in tmp.columns:
                topk_df = tmp
                cache_hit = True

        if topk_df is None:
            # Run full screener at this trade_date
            # progress_callback: suppress per-stock output
            def _noop_progress(current: int, total: int, ts_code: str) -> None:
                return

            result_df = screener.run(
                tier1_only=False,
                tier2_limit=args.tier2_limit,
                trade_date=trade_date_str,
                progress_callback=_noop_progress,
                verbose=False,
            )
            # normalize time consumption
            if result_df is None or result_df.empty:
                # If no results, just skip this month
                topk_df = pd.DataFrame(columns=["ts_code", "name", "composite_score"])
            else:
                # Ensure sorted by composite_score desc
                if "composite_score" in result_df.columns:
                    result_df = result_df.sort_values("composite_score", ascending=False)
                topk_df = result_df.head(args.top_k)[["ts_code", "name", "composite_score"]].copy()

            topk_df.to_csv(cache_file, index=False, encoding="utf-8-sig")
        else:
            # ensure cached df types
            if not topk_df.empty and "ts_code" in topk_df.columns:
                topk_df["ts_code"] = topk_df["ts_code"].astype(str)

        # Update name mapping
        if not topk_df.empty and "ts_code" in topk_df.columns and "name" in topk_df.columns:
            for _, r in topk_df.iterrows():
                code = str(r["ts_code"])
                nm = str(r.get("name", "") or "")
                if code and code not in name_by_code:
                    name_by_code[code] = nm

        top_codes = topk_df["ts_code"].astype(str).tolist() if not topk_df.empty else []
        top_set = set(top_codes)
        if not top_set:
            continue

        triggered = False
        if prev_top_set is None:
            events.append((day, topk_df))
            prev_top_set = top_set
            triggered = True
        else:
            if top_set != prev_top_set:
                events.append((day, topk_df))
                prev_top_set = top_set
                triggered = True

        if args.progress_every > 0 and (i % args.progress_every == 0):
            # show whether this month triggered rebalance + quick topk head
            top_show = ", ".join(top_codes[:3])
            status = "EVENT" if triggered else "NO_CHANGE"
            print(f"[PROGRESS] {status} month_end={day} top3=[{top_show}] cache_hit={cache_hit}")

    if not events:
        raise RuntimeError("No rebalance events found.")

    sim_start = events[0][0]
    # Build full trading calendar (all open days), not just month ends.
    cal = pro.trade_cal(
        exchange="SSE",
        start_date=_ymd(sim_start),
        end_date=_ymd(end_dt),
        fields="cal_date,is_open",
    )
    cal["cal_date"] = pd.to_datetime(cal["cal_date"])
    cal = cal[cal["is_open"] == 1].sort_values("cal_date")
    trade_days = [d.date() for d in cal["cal_date"]]
    if not trade_days:
        raise RuntimeError("trade_days empty in simulation range.")

    event_map: Dict[dt.date, pd.DataFrame] = {d: df for d, df in events}

    # Union of all codes that ever appear in TopK events
    codes_union: Set[str] = set()
    for _, topk_df in events:
        codes_union.update(topk_df["ts_code"].astype(str).tolist())

    codes_list = sorted(codes_union)
    print(f"[INFO] Sim start={sim_start}; end={end_dt}; rebalance events={len(events)}; codes_union={len(codes_list)}")

    # Fetch price panel for all codes in union
    prices_cache_dir = _cache_path(cache_dir, "prices")
    price_panel: Dict[str, pd.Series] = {}
    for j, code in enumerate(codes_list):
        s = _fetch_stock_close_series(
            pro=pro,
            ts_code=code,
            start_date=sim_start,
            end_date=end_dt,
            adj=args.adj,
            cache_dir=prices_cache_dir,
            rate_limiter=rate_limiter,
            rate_limit_retry=args.rate_limit_retry,
            require_adj=bool(args.require_adj),
        )
        if s.empty:
            continue
        price_panel[code] = s
        if (j + 1) % 20 == 0:
            print(f"[INFO] fetched prices {j+1}/{len(codes_list)}")

    if not price_panel:
        raise RuntimeError("No price series fetched.")

    prices = pd.DataFrame(price_panel)
    prices.index = pd.to_datetime(prices.index)
    prices = prices.reindex(pd.to_datetime(trade_days)).sort_index().ffill().dropna(axis=0, how="all")

    # Simulate portfolio
    holdings_qty: Dict[str, float] = {}
    portfolio_value_series: List[float] = []
    printed_events = 0

    portfolio_value_prev = args.initial_capital

    # initial rebalance at sim_start
    init_topk = event_map[sim_start]
    target_codes0 = init_topk["ts_code"].astype(str).tolist()
    valid_codes0: List[str] = []
    ts_day0 = pd.to_datetime(sim_start)
    for c in target_codes0:
        if c not in prices.columns:
            continue
        p = prices.at[ts_day0, c]
        if pd.isna(p) or float(p) <= 0:
            continue
        valid_codes0.append(c)

    if not valid_codes0:
        raise RuntimeError("Initial valid codes empty (no prices at sim_start).")

    w0 = 1.0 / len(valid_codes0)
    holdings_qty = {c: (portfolio_value_prev * w0) / float(prices.at[ts_day0, c]) for c in valid_codes0}

    # --- Per-stock interval P/L tracking ---
    # We treat each rebalance event as closing old intervals and opening new ones,
    # so each stock may have multiple intervals throughout the backtest.
    interval_open: Dict[str, Dict[str, object]] = {}
    interval_records: List[Dict[str, object]] = []

    def _close_interval(stock: str, end_date: dt.date, exit_price: float) -> None:
        info = interval_open.get(stock)
        if not info:
            return
        start_date = info["start_date"]  # type: ignore[assignment]
        start_price = float(info["start_price"])  # type: ignore[arg-type]
        shares = float(info["shares"])  # type: ignore[arg-type]
        cost = shares * start_price
        profit = shares * (exit_price - start_price)
        ret_pct = (profit / cost * 100.0) if cost > 0 else np.nan

        interval_records.append(
            {
                "ts_code": stock,
                "name": name_by_code.get(stock, ""),
                "start_date": start_date.isoformat() if hasattr(start_date, "isoformat") else str(start_date),
                "end_date": end_date.isoformat(),
                "start_price": start_price,
                "end_price": exit_price,
                "shares": shares,
                "profit_amount": profit,
                "return(%)": ret_pct,
            }
        )
        interval_open.pop(stock, None)

    # open intervals at initial rebalance
    for c in valid_codes0:
        interval_open[c] = {
            "start_date": sim_start,
            "start_price": float(prices.at[ts_day0, c]),
            "shares": float(holdings_qty[c]),
        }

    if args.verbose_rebalance and printed_events < args.verbose_max_events:
        printed_events += 1
        print(f"[REBALANCE] {sim_start} INIT: portfolio_value={portfolio_value_prev:.2f} top_k={len(target_codes0)} valid={len(valid_codes0)}")
        for c in valid_codes0[:10]:
            p = float(prices.at[ts_day0, c])
            nm = name_by_code.get(c, "")
            print(f"  - {c}{' ('+nm+')' if nm else ''}: price={p:.4f}, weight={w0:.4f}, shares={holdings_qty[c]:.4f}")
        if len(valid_codes0) > 10:
            print(f"  - ... (only showing first 10 of {len(valid_codes0)} valid codes)")

    # iterate all trading days
    for day in trade_days:
        ts_day = pd.to_datetime(day)

        # rebalance events (skip sim_start since already done)
        if day in event_map and day != sim_start:
            # compute current portfolio value at today's close using old holdings
            v_today_old = 0.0
            row_prices_all_old = {}
            for c_old, qty_old in holdings_qty.items():
                if c_old not in prices.columns:
                    continue
                p_old = prices.at[ts_day, c_old] if ts_day in prices.index else np.nan
                if pd.isna(p_old):
                    continue
                p_old = float(p_old)
                row_prices_all_old[c_old] = p_old
                v_today_old += p_old * float(qty_old)

            topk_df = event_map[day]
            target_codes = topk_df["ts_code"].astype(str).tolist()
            row_prices = prices.loc[ts_day, target_codes].replace([np.inf, -np.inf], np.nan)

            valid_codes: List[str] = []
            for c in target_codes:
                if c not in row_prices.index:
                    continue
                p = row_prices.get(c, np.nan)
                if pd.isna(p) or float(p) <= 0:
                    continue
                valid_codes.append(c)

            if valid_codes:
                w = 1.0 / len(valid_codes)
                # close old intervals for all currently held stocks at today's close
                for c_old, p_exit in row_prices_all_old.items():
                    if c_old in interval_open:
                        _close_interval(c_old, day, float(p_exit))

                base_value = v_today_old if v_today_old > 0 else portfolio_value_prev
                holdings_qty_new = {c: (base_value * w) / float(row_prices.get(c)) for c in valid_codes}
                holdings_qty = holdings_qty_new

                # open new intervals for new holdings at today's close
                for c_new in valid_codes:
                    p_entry = float(row_prices.get(c_new))
                    interval_open[c_new] = {
                        "start_date": day,
                        "start_price": p_entry,
                        "shares": float(holdings_qty_new[c_new]),
                    }

                if args.verbose_rebalance and printed_events < args.verbose_max_events:
                    printed_events += 1
                    nm_show = lambda x: name_by_code.get(x, "")
                    print(f"[REBALANCE] {day} EVENT: portfolio_value={portfolio_value_prev:.2f} top_k={len(target_codes)} valid={len(valid_codes)}")
                    print(f"  valid_weight(each)={w:.4f}")
                    for c in valid_codes[:10]:
                        p = float(row_prices.get(c))
                        nm = nm_show(c)
                        print(f"  - {c}{' ('+nm+')' if nm else ''}: price={p:.4f}, weight={w:.4f}, shares={holdings_qty[c]:.4f}")
                    if len(valid_codes) > 10:
                        print(f"  - ... (only showing first 10 of {len(valid_codes)} valid codes)")
            else:
                # no valid prices => keep holdings
                pass

        # compute portfolio value for this day
        v = 0.0
        for c, qty in holdings_qty.items():
            if c not in prices.columns:
                continue
            p = prices.at[ts_day, c] if ts_day in prices.index else np.nan
            if pd.isna(p):
                continue
            v += float(p) * float(qty)

        portfolio_value_series.append(v)
        portfolio_value_prev = v

    # close remaining intervals at final day close
    last_day = trade_days[-1]
    last_ts = pd.to_datetime(last_day)
    for c_open in list(interval_open.keys()):
        if c_open not in prices.columns:
            continue
        p_exit = prices.at[last_ts, c_open] if last_ts in prices.index else np.nan
        if pd.isna(p_exit) or float(p_exit) <= 0:
            continue
        _close_interval(c_open, last_day, float(p_exit))

    # export per-stock P/L
    if interval_records:
        intervals_df = pd.DataFrame(interval_records)
        # aggregate by stock
        agg = (
            intervals_df.groupby("ts_code", as_index=False)
            .agg(
                name=("name", "first"),
                profit_amount=("profit_amount", "sum"),
                cost_amount=("profit_amount", lambda s: np.nan),  # placeholder
            )
        )
        # compute cost_amount separately for return(%) aggregation
        intervals_df["cost_amount"] = intervals_df["shares"] * intervals_df["start_price"]
        agg2 = (
            intervals_df.groupby("ts_code", as_index=False)
            .agg(
                name=("name", "first"),
                profit_amount=("profit_amount", "sum"),
                cost_amount=("cost_amount", "sum"),
                first_date=("start_date", "min"),
                last_date=("end_date", "max"),
                n_intervals=("start_date", "count"),
            )
        )
        agg2["return(%)"] = np.where(
            agg2["cost_amount"] > 0,
            agg2["profit_amount"] / agg2["cost_amount"] * 100.0,
            np.nan,
        )
        # periods string
        periods = (
            intervals_df.sort_values(["ts_code", "start_date", "end_date"])
            .groupby("ts_code")["start_date"]
        )
        periods_str = (
            intervals_df.sort_values(["ts_code", "start_date", "end_date"])
            .groupby("ts_code")
            .apply(lambda g: ";".join([f"{s}~{e}" for s, e in zip(g["start_date"], g["end_date"])]))
        )
        agg2["periods"] = agg2["ts_code"].map(periods_str)
        agg2 = agg2.sort_values("profit_amount", ascending=False)

        out_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "output",
        )
        os.makedirs(out_dir, exist_ok=True)
        intervals_path = os.path.join(out_dir, f"rebalance_composite_{freq_tag}_stock_interval_pl.csv")
        stock_pl_path = os.path.join(out_dir, f"rebalance_composite_{freq_tag}_stock_pl_sorted.csv")

        intervals_export = intervals_df.rename(
            columns={
                "ts_code": "股票代码",
                "name": "股票名称",
                "start_date": "开始日期",
                "end_date": "结束日期",
                "start_price": "起始收盘价",
                "end_price": "结束收盘价",
                "shares": "股数",
                "profit_amount": "区间收益(元)",
                "return(%)": "区间收益率(%)",
                "cost_amount": "成本金额(元)",
            }
        )
        intervals_export.to_csv(intervals_path, index=False, encoding="utf-8-sig")

        agg2_export = agg2.rename(
            columns={
                "ts_code": "股票代码",
                "name": "股票名称",
                "profit_amount": "累计收益(元)",
                "cost_amount": "成本金额(元)",
                "first_date": "首次持有开始日期",
                "last_date": "最后持有结束日期",
                "n_intervals": "持有区间数",
                "return(%)": "累计收益率(%)",
                "periods": "持有区间(列表)",
            }
        )
        agg2_export.to_csv(stock_pl_path, index=False, encoding="utf-8-sig")
        print(f"[INFO] per-stock interval P/L exported:")
        print(f"- intervals: {intervals_path}")
        print(f"- sorted: {stock_pl_path}")

    equity = pd.DataFrame(
        {"date": pd.to_datetime(trade_days), "portfolio_value": portfolio_value_series}
    ).set_index("date")
    equity["daily_return"] = equity["portfolio_value"].pct_change().fillna(0.0)
    equity["cum_return"] = equity["portfolio_value"] / equity["portfolio_value"].iloc[0] - 1.0

    total_return = float(equity["cum_return"].iloc[-1])
    equity_nav = equity["portfolio_value"] / equity["portfolio_value"].iloc[0]
    annual_return_cagr = _calc_annualized_return(equity_nav)
    annual_return_simple_avg = _calc_simple_avg_annual_return(equity_nav)
    max_dd = _calc_max_drawdown(equity["portfolio_value"])

    summary = pd.DataFrame(
        [
            {
                "start_date": sim_start.isoformat(),
                "end_date": end_dt.isoformat(),
                "top_k": args.top_k,
                "n_events": len(events),
                "initial_capital": args.initial_capital,
                "total_return(%)": round(total_return * 100, 2),
                "annual_return_cagr(%)": None
                if np.isnan(annual_return_cagr)
                else round(float(annual_return_cagr) * 100, 2),
                "annual_return_simple_avg(%)": None
                if np.isnan(annual_return_simple_avg)
                else round(float(annual_return_simple_avg) * 100, 2),
                "max_drawdown(%)": round(max_dd * 100, 2),
            }
        ]
    )

    # Benchmarks
    bench_codes = [c.strip() for c in str(args.benchmarks).split(",") if c.strip()]
    bench_series: Dict[str, pd.Series] = {}
    for bc in bench_codes:
        s = _fetch_index_daily(
            pro=pro,
            ts_code=bc,
            start_date=sim_start,
            end_date=end_dt,
            rate_limiter=rate_limiter,
            rate_limit_retry=args.rate_limit_retry,
        )
        if not s.empty:
            bench_series[bc] = s

    bench_df = pd.DataFrame(bench_series)
    if not bench_df.empty:
        bench_df.index = pd.to_datetime(bench_df.index)
        bench_df = bench_df.reindex(prices.index).ffill()
    equity_norm = equity_nav

    out_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "output",
    )

    # Benchmarks metrics (CAGR + max drawdown)
    metrics_rows: List[Dict[str, object]] = []
    metrics_rows.append(
        {
            "instrument": "portfolio",
            "annual_return_cagr(%)": None
            if np.isnan(annual_return_cagr)
            else round(float(annual_return_cagr) * 100, 4),
            "annual_return_simple_avg(%)": None
            if np.isnan(annual_return_simple_avg)
            else round(float(annual_return_simple_avg) * 100, 4),
            "max_drawdown(%)": round(float(max_dd) * 100, 4) if not np.isnan(max_dd) else None,
        }
    )

    bench_metrics: Dict[str, Dict[str, float]] = {}
    if not bench_df.empty:
        for col in bench_df.columns:
            nav = bench_df[col] / bench_df[col].iloc[0]
            ann_cagr = _calc_annualized_return(nav)
            ann_simple = _calc_simple_avg_annual_return(nav)
            mdd = _calc_max_drawdown(bench_df[col])
            bench_metrics[col] = {
                "annual_return_cagr": ann_cagr,
                "annual_return_simple_avg": ann_simple,
                "max_drawdown": mdd,
            }
            metrics_rows.append(
                {
                    "instrument": col,
                    "annual_return_cagr(%)": None
                    if np.isnan(ann_cagr)
                    else round(float(ann_cagr) * 100, 4),
                    "annual_return_simple_avg(%)": None
                    if np.isnan(ann_simple)
                    else round(float(ann_simple) * 100, 4),
                    "max_drawdown(%)": round(float(mdd) * 100, 4) if not np.isnan(mdd) else None,
                }
            )

    metrics_by_instrument_path = os.path.join(
        out_dir,
        f"rebalance_composite_{freq_tag}_metrics_by_instrument.csv",
    )
    pd.DataFrame(metrics_rows).to_csv(metrics_by_instrument_path, index=False, encoding="utf-8-sig")

    # Visualization
    plt.figure(figsize=(10, 6))
    plt.plot(equity_norm.index, equity_norm.values, label="portfolio")
    if not bench_df.empty:
        for col in bench_df.columns:
            plt.plot(bench_df.index, (bench_df[col] / bench_df[col].iloc[0]).values, label=col)
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.title(f"{freq_tag.capitalize()} composite_score rebalance vs Benchmarks (Normalized NAV)")
    plt.xlabel("Date")
    plt.ylabel("Normalized NAV (start=1.0)")

    # Overlay metrics text
    def _fmt_pct(x: float) -> str:
        if x is None or np.isnan(x):
            return "N/A"
        return f"{x*100:.2f}%"

    txt_lines = []
    txt_lines.append(
        f"Portfolio: CAGR={_fmt_pct(annual_return_cagr)}, "
        f"SimpleAvg={_fmt_pct(annual_return_simple_avg)}, "
        f"MDD={max_dd*100:.2f}%"
    )
    if not bench_df.empty:
        for col in bench_df.columns:
            ann_cagr = bench_metrics[col]["annual_return_cagr"]
            ann_simple = bench_metrics[col]["annual_return_simple_avg"]
            mdd = bench_metrics[col]["max_drawdown"]
            txt_lines.append(
                f"{col}: CAGR={_fmt_pct(ann_cagr)}, "
                f"SimpleAvg={_fmt_pct(ann_simple)}, "
                f"MDD={mdd*100:.2f}%"
            )
    txt = "\n".join(txt_lines)
    plt.gca().text(0.01, 0.01, txt, transform=plt.gca().transAxes, fontsize=9, va="bottom",
                   bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.7, edgecolor="gray"))
    plt.tight_layout()

    os.makedirs(out_dir, exist_ok=True)

    equity_path = os.path.join(out_dir, f"rebalance_composite_{freq_tag}_equity_curve.csv")
    events_path = os.path.join(out_dir, f"rebalance_composite_{freq_tag}_events.csv")
    summary_path = os.path.join(out_dir, f"rebalance_composite_{freq_tag}_summary.csv")
    fig_path = os.path.join(out_dir, f"rebalance_composite_{freq_tag}_vs_benchmarks.png")
    combined_path = os.path.join(out_dir, f"rebalance_composite_{freq_tag}_vs_benchmarks.csv")

    equity.to_csv(equity_path, encoding="utf-8-sig")
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")

    # Export events
    rows = []
    for d, topk_df in events:
        codes = topk_df["ts_code"].astype(str).tolist()
        names = [name_by_code.get(c, "") for c in codes]
        rows.append(
            {
                "event_date": d.isoformat(),
                "top_codes": ",".join(codes),
                "top_names": "|".join(names),
            }
        )
    pd.DataFrame(rows).to_csv(events_path, index=False, encoding="utf-8-sig")

    plt.savefig(fig_path, dpi=150)
    plt.close()

    # Combined normalized export
    combined = pd.DataFrame({"portfolio": equity_norm})
    if not bench_df.empty:
        for col in bench_df.columns:
            combined[col] = bench_df[col] / bench_df[col].iloc[0]
    combined.to_csv(combined_path, encoding="utf-8-sig")

    print("回测完成：")
    print(f"- equity: {equity_path}")
    print(f"- events: {events_path}")
    print(f"- summary: {summary_path}")
    print(f"- fig: {fig_path}")
    print(f"- combined: {combined_path}")


if __name__ == "__main__":
    main()

