#!/usr/bin/env python3
"""Turtle Investment Framework - Tushare Data Collector (Phase 1A).

Collects 5 years of financial data from Tushare Pro API and outputs
a structured data_pack_market.md file.

Usage:
    python3 scripts/tushare_collector.py --code 600887.SH
    python3 scripts/tushare_collector.py --code 600887.SH --output output/data_pack.md
    python3 scripts/tushare_collector.py --code 600887.SH --dry-run
"""

import argparse
import functools
import sys
import time

import pandas as pd
import tushare as ts

from config import get_token, validate_stock_code
from format_utils import format_number, format_table, format_header


def rate_limit(func):
    """Decorator to enforce 0.3s delay between Tushare API calls."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        time.sleep(0.3)
        return func(*args, **kwargs)
    return wrapper


class TushareClient:
    """Client for Tushare Pro API with rate limiting and retry logic."""

    MAX_RETRIES = 3
    RETRY_DELAY = 1.0  # seconds between retries

    def __init__(self, token: str):
        ts.set_token(token)
        self.pro = ts.pro_api()
        self.token = token

    @rate_limit
    def _safe_call(self, api_name: str, **kwargs) -> pd.DataFrame:
        """Call a Tushare API endpoint with retry logic.

        Args:
            api_name: The API endpoint name (e.g., 'stock_basic').
            **kwargs: Parameters passed to the API call.

        Returns:
            DataFrame with results.

        Raises:
            RuntimeError: After MAX_RETRIES failures.
        """
        last_err = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                api_func = getattr(self.pro, api_name)
                df = api_func(**kwargs)
                return df
            except Exception as e:
                last_err = e
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY * attempt)
        raise RuntimeError(
            f"Tushare API '{api_name}' failed after {self.MAX_RETRIES} retries: {last_err}"
        )

    # --- Feature #14: Section 1 — Basic company info ---

    def get_basic_info(self, ts_code: str) -> str:
        """Section 1: Basic company info from stock_basic + daily_basic."""
        basic = self._safe_call("stock_basic", ts_code=ts_code,
                                fields="ts_code,name,industry,area,market,exchange,list_date,fullname")
        if basic.empty:
            return format_header(2, "1. 基本信息") + "\n\n数据缺失\n"

        row = basic.iloc[0]

        # Get latest daily_basic for valuation
        daily = self._safe_call("daily_basic", ts_code=ts_code,
                                fields="ts_code,trade_date,close,pe_ttm,pb,total_mv,circ_mv,total_share,float_share")
        val_rows = []
        if not daily.empty:
            d = daily.iloc[0]
            val_rows = [
                ["当前价格", f"{d.get('close', '—')}"],
                ["PE (TTM)", f"{d.get('pe_ttm', '—')}"],
                ["PB", f"{d.get('pb', '—')}"],
                ["总市值 (万元)", format_number(d.get('total_mv', None), divider=1, decimals=2)],
                ["流通市值 (万元)", format_number(d.get('circ_mv', None), divider=1, decimals=2)],
            ]

        lines = [format_header(2, "1. 基本信息"), ""]
        info_table = format_table(
            ["项目", "内容"],
            [
                ["股票代码", str(row.get("ts_code", ""))],
                ["公司名称", str(row.get("name", ""))],
                ["全称", str(row.get("fullname", ""))],
                ["行业", str(row.get("industry", ""))],
                ["地区", str(row.get("area", ""))],
                ["交易所", str(row.get("exchange", ""))],
                ["上市日期", str(row.get("list_date", ""))],
            ] + val_rows,
            alignments=["l", "r"],
        )
        lines.append(info_table)
        return "\n".join(lines)

    # --- Feature #15: Section 2 — Market data ---

    def get_market_data(self, ts_code: str) -> str:
        """Section 2: Current price and 52-week range."""
        today = pd.Timestamp.now().strftime("%Y%m%d")
        year_ago = (pd.Timestamp.now() - pd.DateOffset(years=1)).strftime("%Y%m%d")

        df = self._safe_call("daily", ts_code=ts_code,
                             start_date=year_ago, end_date=today,
                             fields="ts_code,trade_date,open,high,low,close,vol,amount")
        lines = [format_header(2, "2. 市场行情"), ""]

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        latest_close = df.iloc[0]["close"]
        high_52w = df["high"].max()
        low_52w = df["low"].min()
        high_date = df.loc[df["high"].idxmax(), "trade_date"]
        low_date = df.loc[df["low"].idxmin(), "trade_date"]
        avg_vol = df["vol"].mean()

        table = format_table(
            ["指标", "数值"],
            [
                ["最新收盘价", f"{latest_close:.2f}"],
                ["52周最高", f"{high_52w:.2f} ({high_date})"],
                ["52周最低", f"{low_52w:.2f} ({low_date})"],
                ["52周涨跌幅", f"{(latest_close / low_52w - 1) * 100:.1f}% (自低点)"],
                ["日均成交量 (手)", f"{avg_vol:,.0f}"],
            ],
            alignments=["l", "r"],
        )
        lines.append(table)
        return "\n".join(lines)

    # --- Feature #16: Section 3 — Consolidated income statement ---

    def get_income(self, ts_code: str, report_type: str = "1") -> str:
        """Section 3: Five-year consolidated income statement."""
        df = self._safe_call("income", ts_code=ts_code,
                             report_type=report_type,
                             fields="ts_code,end_date,report_type,revenue,oper_cost,"
                                    "sell_exp,admin_exp,rd_exp,oper_profit,"
                                    "n_income,n_income_attr_p,minority_gain,"
                                    "basic_eps,diluted_eps,dt_eps")
        section_label = "3P. 母公司利润表" if report_type == "4" else "3. 合并利润表"
        lines = [format_header(2, section_label), ""]

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        # Keep only annual reports (end_date ending in 1231), deduplicate
        df = df[df["end_date"].str.endswith("1231")].drop_duplicates(subset=["end_date"])
        df = df.sort_values("end_date", ascending=False).head(5)

        if df.empty:
            lines.append("无年报数据\n")
            return "\n".join(lines)

        years = df["end_date"].apply(lambda x: x[:4]).tolist()

        fields = [
            ("营业收入", "revenue"),
            ("营业成本", "oper_cost"),
            ("销售费用", "sell_exp"),
            ("管理费用", "admin_exp"),
            ("研发费用", "rd_exp"),
            ("营业利润", "oper_profit"),
            ("净利润", "n_income"),
            ("归母净利润", "n_income_attr_p"),
            ("少数股东损益", "minority_gain"),
            ("基本EPS", "basic_eps"),
            ("稀释EPS", "diluted_eps"),
        ]

        headers = ["项目 (百万元)"] + years
        rows = []
        for label, col in fields:
            row = [label]
            for _, r in df.iterrows():
                val = r.get(col)
                if col in ("basic_eps", "diluted_eps", "dt_eps"):
                    row.append(f"{val:.2f}" if val is not None and val == val else "—")
                else:
                    row.append(format_number(val))
            rows.append(row)

        table = format_table(headers, rows,
                             alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        lines.append("")
        lines.append("*单位: 百万元 (原始数据 / 1,000,000), EPS为元/股*")
        return "\n".join(lines)

    # --- Feature #17: Section 3P — Parent company income ---

    def get_income_parent(self, ts_code: str) -> str:
        """Section 3P: Five-year parent-company income statement."""
        return self.get_income(ts_code, report_type="4")

    # --- Feature #18: Section 4 — Consolidated balance sheet ---

    def get_balance_sheet(self, ts_code: str, report_type: str = "1") -> str:
        """Section 4: Five-year consolidated balance sheet."""
        df = self._safe_call("balancesheet", ts_code=ts_code,
                             report_type=report_type,
                             fields="ts_code,end_date,report_type,"
                                    "total_assets,total_liab,"
                                    "total_hldr_eqy_exc_min_int,minority_int,"
                                    "money_cap,accounts_receiv,notes_receiv,"
                                    "oth_receiv,inventories,goodwill,"
                                    "fix_assets,lt_eqt_invest,"
                                    "contract_liab,adv_receipts,"
                                    "lt_borr,st_borr")
        section_label = "4P. 母公司资产负债表" if report_type == "4" else "4. 合并资产负债表"
        lines = [format_header(2, section_label), ""]

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        df = df[df["end_date"].str.endswith("1231")].drop_duplicates(subset=["end_date"])
        df = df.sort_values("end_date", ascending=False).head(5)

        if df.empty:
            lines.append("无年报数据\n")
            return "\n".join(lines)

        years = df["end_date"].apply(lambda x: x[:4]).tolist()

        fields = [
            ("总资产", "total_assets"),
            ("总负债", "total_liab"),
            ("归母所有者权益", "total_hldr_eqy_exc_min_int"),
            ("少数股东权益", "minority_int"),
            ("货币资金", "money_cap"),
            ("应收账款", "accounts_receiv"),
            ("应收票据", "notes_receiv"),
            ("其他应收款", "oth_receiv"),
            ("存货", "inventories"),
            ("商誉", "goodwill"),
            ("固定资产", "fix_assets"),
            ("长期股权投资", "lt_eqt_invest"),
            ("合同负债", "contract_liab"),
            ("预收款项", "adv_receipts"),
            ("短期借款", "st_borr"),
            ("长期借款", "lt_borr"),
        ]

        # For parent company, use subset of fields
        if report_type == "4":
            fields = [
                ("货币资金", "money_cap"),
                ("长期股权投资", "lt_eqt_invest"),
                ("总资产", "total_assets"),
                ("短期借款", "st_borr"),
                ("长期借款", "lt_borr"),
                ("总负债", "total_liab"),
            ]

        headers = ["项目 (百万元)"] + years
        rows = []
        for label, col in fields:
            row = [label]
            for _, r in df.iterrows():
                row.append(format_number(r.get(col)))
            rows.append(row)

        table = format_table(headers, rows,
                             alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        lines.append("")
        lines.append("*单位: 百万元*")
        return "\n".join(lines)

    # --- Feature #19: Section 4P — Parent company balance sheet ---

    def get_balance_sheet_parent(self, ts_code: str) -> str:
        """Section 4P: Five-year parent-company balance sheet."""
        return self.get_balance_sheet(ts_code, report_type="4")

    # --- Feature #20: Section 5 — Cash flow statement ---

    def get_cashflow(self, ts_code: str) -> str:
        """Section 5: Five-year cash flow statement with FCF calculation."""
        df = self._safe_call("cashflow", ts_code=ts_code,
                             report_type="1",
                             fields="ts_code,end_date,report_type,"
                                    "n_cashflow_act,n_cashflow_inv_act,"
                                    "n_cashflow_fnc_act,c_paid_invest,"
                                    "prov_depr_assets,"
                                    "c_pay_dist_dpcp_int_exp")
        lines = [format_header(2, "5. 现金流量表"), ""]

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        df = df[df["end_date"].str.endswith("1231")].drop_duplicates(subset=["end_date"])
        df = df.sort_values("end_date", ascending=False).head(5)

        if df.empty:
            lines.append("无年报数据\n")
            return "\n".join(lines)

        years = df["end_date"].apply(lambda x: x[:4]).tolist()

        headers = ["项目 (百万元)"] + years
        rows = []

        base_fields = [
            ("经营活动现金流 (OCF)", "n_cashflow_act"),
            ("投资活动现金流", "n_cashflow_inv_act"),
            ("筹资活动现金流", "n_cashflow_fnc_act"),
            ("资本支出 (Capex)", "c_paid_invest"),
            ("折旧与摊销 (D&A)", "prov_depr_assets"),
        ]
        for label, col in base_fields:
            row = [label]
            for _, r in df.iterrows():
                row.append(format_number(r.get(col)))
            rows.append(row)

        # FCF = OCF - |Capex| (values are in raw yuan, format_number divides by 1e6)
        fcf_row = ["自由现金流 (FCF)"]
        for _, r in df.iterrows():
            ocf = r.get("n_cashflow_act")
            capex = r.get("c_paid_invest")
            if ocf is not None and capex is not None:
                fcf = float(ocf) - abs(float(capex))
                fcf_row.append(format_number(fcf))
            else:
                fcf_row.append("—")
        rows.append(fcf_row)

        table = format_table(headers, rows,
                             alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        lines.append("")
        lines.append("*单位: 百万元; FCF = OCF - |Capex|*")
        return "\n".join(lines)

    # --- Feature #21: Section 6 — Dividend history ---

    def get_dividends(self, ts_code: str) -> str:
        """Section 6: Dividend history."""
        df = self._safe_call("dividend", ts_code=ts_code,
                             fields="ts_code,end_date,ann_date,div_proc,"
                                    "stk_div,cash_div_tax,record_date,"
                                    "ex_date,base_share")
        lines = [format_header(2, "6. 分红历史"), ""]

        if df.empty:
            lines.append("暂无分红数据\n")
            return "\n".join(lines)

        # Filter for completed dividends
        df = df[df["div_proc"] == "实施"].copy()
        df = df.drop_duplicates(subset=["end_date"])
        df = df.sort_values("end_date", ascending=False).head(5)

        if df.empty:
            lines.append("暂无已实施分红\n")
            return "\n".join(lines)

        headers = ["年度", "每股现金分红(税前)", "每股送股", "登记日", "除权日", "总分红 (百万元)"]
        rows = []
        for _, r in df.iterrows():
            year = str(r.get("end_date", ""))[:4]
            cash_div = r.get("cash_div_tax", 0) or 0
            stk_div = r.get("stk_div", 0) or 0
            base_share = r.get("base_share", 0) or 0
            total_div = cash_div * base_share  # yuan
            rows.append([
                year,
                f"{cash_div:.4f}",
                f"{stk_div:.2f}" if stk_div else "—",
                str(r.get("record_date", "—")),
                str(r.get("ex_date", "—")),
                format_number(total_div),
            ])

        table = format_table(headers, rows,
                             alignments=["l", "r", "r", "l", "l", "r"])
        lines.append(table)
        return "\n".join(lines)

    # --- Feature #22: Section 11 + Appendix A — 10-year weekly prices ---

    def get_weekly_prices(self, ts_code: str) -> str:
        """Section 11 + Appendix A: 10-year weekly price history."""
        today = pd.Timestamp.now().strftime("%Y%m%d")
        ten_years_ago = (pd.Timestamp.now() - pd.DateOffset(years=10)).strftime("%Y%m%d")

        df = self._safe_call("weekly", ts_code=ts_code,
                             start_date=ten_years_ago, end_date=today,
                             fields="ts_code,trade_date,open,high,low,close,vol,amount")
        lines = [format_header(2, "11. 十年周线行情"), ""]

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        df = df.sort_values("trade_date", ascending=True)

        # 10-year summary
        high_10y = df["high"].max()
        low_10y = df["low"].min()
        high_date = df.loc[df["high"].idxmax(), "trade_date"]
        low_date = df.loc[df["low"].idxmin(), "trade_date"]
        latest_close = df.iloc[-1]["close"]

        summary_table = format_table(
            ["指标", "数值"],
            [
                ["10年最高", f"{high_10y:.2f} ({high_date})"],
                ["10年最低", f"{low_10y:.2f} ({low_date})"],
                ["最新收盘", f"{latest_close:.2f}"],
                ["距最高回撤", f"{(1 - latest_close / high_10y) * 100:.1f}%"],
                ["距最低涨幅", f"{(latest_close / low_10y - 1) * 100:.1f}%"],
            ],
            alignments=["l", "r"],
        )
        lines.append(summary_table)
        lines.append("")

        # Annual summary
        df["year"] = df["trade_date"].str[:4]
        annual = df.groupby("year").agg(
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            avg_vol=("vol", "mean"),
        ).reset_index()
        annual = annual.sort_values("year", ascending=False)

        lines.append(format_header(3, "年度行情汇总"))
        lines.append("")
        annual_table = format_table(
            ["年度", "最高", "最低", "年末收盘", "周均成交量(手)"],
            [[
                r["year"],
                f"{r['high']:.2f}",
                f"{r['low']:.2f}",
                f"{r['close']:.2f}",
                f"{r['avg_vol']:,.0f}",
            ] for _, r in annual.iterrows()],
            alignments=["l", "r", "r", "r", "r"],
        )
        lines.append(annual_table)
        return "\n".join(lines)

    # --- Feature #23: Section 12 — Financial indicators ---

    def get_fina_indicators(self, ts_code: str) -> str:
        """Section 12: Key financial indicators from fina_indicator endpoint."""
        df = self._safe_call("fina_indicator", ts_code=ts_code,
                             fields="ts_code,end_date,roe,roe_waa,"
                                    "grossprofit_margin,netprofit_margin,"
                                    "rd_exp,current_ratio,quick_ratio,"
                                    "assets_turn,debt_to_assets")
        lines = [format_header(2, "12. 关键财务指标"), ""]

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        df = df[df["end_date"].str.endswith("1231")].drop_duplicates(subset=["end_date"])
        df = df.sort_values("end_date", ascending=False).head(5)

        if df.empty:
            lines.append("无年报数据\n")
            return "\n".join(lines)

        years = df["end_date"].apply(lambda x: x[:4]).tolist()

        pct_fields = [
            ("ROE (%)", "roe"),
            ("加权ROE (%)", "roe_waa"),
            ("毛利率 (%)", "grossprofit_margin"),
            ("净利率 (%)", "netprofit_margin"),
            ("资产负债率 (%)", "debt_to_assets"),
        ]
        ratio_fields = [
            ("流动比率", "current_ratio"),
            ("速动比率", "quick_ratio"),
            ("总资产周转率", "assets_turn"),
        ]

        headers = ["指标"] + years
        rows = []
        for label, col in pct_fields:
            row = [label]
            for _, r in df.iterrows():
                val = r.get(col)
                row.append(f"{val:.2f}" if val is not None and val == val else "—")
            rows.append(row)
        for label, col in ratio_fields:
            row = [label]
            for _, r in df.iterrows():
                val = r.get(col)
                row.append(f"{val:.2f}" if val is not None and val == val else "—")
            rows.append(row)

        table = format_table(headers, rows,
                             alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        return "\n".join(lines)

    # --- Feature #24: Section 9 — Business segments ---

    def get_segments(self, ts_code: str) -> str:
        """Section 9: Business segment data from fina_mainbz_ts."""
        lines = [format_header(2, "9. 主营业务构成"), ""]
        try:
            df = self._safe_call("fina_mainbz", ts_code=ts_code, type="P")
        except RuntimeError:
            lines.append("数据缺失 (接口可能无权限)\n")
            return "\n".join(lines)

        if df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        # Get latest period
        if "end_date" in df.columns:
            latest_period = df["end_date"].max()
            df = df[df["end_date"] == latest_period]

        headers = ["业务名称", "营业收入 (百万元)", "营业利润 (百万元)", "毛利率 (%)"]
        rows = []
        for _, r in df.iterrows():
            name = r.get("bz_item", "—")
            rev = r.get("bz_sales", None)
            profit = r.get("bz_profit", None)
            margin = r.get("bz_cost", None)
            # Compute gross margin if both revenue and cost available
            gm = "—"
            if rev and margin:
                try:
                    gm = f"{(1 - float(margin)/float(rev)) * 100:.1f}"
                except (ValueError, ZeroDivisionError):
                    gm = "—"
            rows.append([
                str(name),
                format_number(rev),
                format_number(profit),
                gm,
            ])

        table = format_table(headers, rows,
                             alignments=["l", "r", "r", "r"])
        lines.append(table)
        return "\n".join(lines)

    # --- Feature #25: Section 7 (partial) — Top 10 holders + audit ---

    def get_holders(self, ts_code: str) -> str:
        """Section 7 (partial): Top 10 shareholders."""
        lines = [format_header(2, "7. 股东与治理 (部分)"), ""]

        try:
            df = self._safe_call("top10_holders", ts_code=ts_code)
        except RuntimeError:
            lines.append("股东数据缺失\n")
            return "\n".join(lines)

        if df.empty:
            lines.append("股东数据缺失\n")
            return "\n".join(lines)

        # Get latest period
        if "end_date" in df.columns:
            latest = df["end_date"].max()
            df = df[df["end_date"] == latest]

        lines.append(f"*截至 {latest}*\n" if "end_date" in df.columns else "")

        headers = ["序号", "股东名称", "持股数量 (万股)", "持股比例 (%)"]
        rows = []
        for i, (_, r) in enumerate(df.head(10).iterrows(), 1):
            rows.append([
                str(i),
                str(r.get("holder_name", "—")),
                format_number(r.get("hold_amount", None), divider=1e4, decimals=2),
                f"{r.get('hold_ratio', 0) or 0:.2f}",
            ])

        table = format_table(headers, rows,
                             alignments=["l", "l", "r", "r"])
        lines.append(table)
        return "\n".join(lines)

    def get_audit(self, ts_code: str) -> str:
        """Audit opinion info."""
        lines = [format_header(3, "审计意见"), ""]
        try:
            df = self._safe_call("fina_audit", ts_code=ts_code)
        except RuntimeError:
            lines.append("审计数据缺失\n")
            return "\n".join(lines)

        if df.empty:
            lines.append("审计数据缺失\n")
            return "\n".join(lines)

        df = df.sort_values("end_date", ascending=False).head(3)
        headers = ["年度", "审计意见"]
        rows = []
        for _, r in df.iterrows():
            year = str(r.get("end_date", ""))[:4]
            opinion = str(r.get("audit_result", "—"))
            rows.append([year, opinion])

        table = format_table(headers, rows, alignments=["l", "l"])
        lines.append(table)
        return "\n".join(lines)

    # --- Feature #28: Full data_pack_market.md assembly ---

    def assemble_data_pack(self, ts_code: str) -> str:
        """Assemble complete data_pack_market.md combining all sections."""
        timestamp = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            format_header(1, f"数据包 — {ts_code}"),
            "",
            f"*生成时间: {timestamp}*",
            f"*数据来源: Tushare Pro*",
            f"*金额单位: 百万元 (除特殊标注)*",
            "",
            "---",
            "",
        ]

        sections = [
            ("1. 基本信息", self.get_basic_info),
            ("2. 市场行情", self.get_market_data),
            ("3. 合并利润表", self.get_income),
            ("3P. 母公司利润表", self.get_income_parent),
            ("4. 合并资产负债表", self.get_balance_sheet),
            ("4P. 母公司资产负债表", self.get_balance_sheet_parent),
            ("5. 现金流量表", self.get_cashflow),
            ("6. 分红历史", self.get_dividends),
            ("7. 股东与治理", self.get_holders),
            ("9. 主营业务构成", self.get_segments),
            ("11. 十年周线行情", self.get_weekly_prices),
            ("12. 关键财务指标", self.get_fina_indicators),
        ]

        completed = 0
        for name, method in sections:
            try:
                print(f"  Collecting {name}...")
                section_md = method(ts_code)
                lines.append(section_md)
                lines.append("")
                completed += 1
            except Exception as e:
                lines.append(format_header(2, name))
                lines.append(f"\n数据获取失败: {e}\n")

        # Audit info (sub-section of 7)
        try:
            audit_md = self.get_audit(ts_code)
            lines.append(audit_md)
            lines.append("")
        except Exception:
            pass

        # Placeholder sections for Agent-filled content
        for sec_num, sec_name in [
            ("8", "行业与竞争"),
            ("10", "管理层讨论与分析 (MD&A)"),
            ("13", "风险警示"),
        ]:
            lines.append(format_header(2, f"{sec_num}. {sec_name}"))
            lines.append("")
            lines.append("*[待Agent WebSearch补充]*")
            lines.append("")

        lines.append("---")
        lines.append(f"*共 {completed}/{len(sections)} 个数据板块成功获取*")

        return "\n".join(lines)


class WarningsCollector:
    """Auto-detect anomalies during data collection (Feature #30)."""

    def __init__(self):
        self.warnings = []

    def check_missing_data(self, section_name: str, df: pd.DataFrame):
        """Warn if a data section returned empty."""
        if df is None or (isinstance(df, pd.DataFrame) and df.empty):
            self.warnings.append({
                "type": "DATA_MISSING",
                "severity": "中",
                "message": f"{section_name} 数据缺失",
            })

    def check_yoy_change(self, section_name: str, field_name: str,
                         values: list, threshold: float = 3.0):
        """Warn if year-over-year change exceeds threshold (e.g., 300%)."""
        for i in range(len(values) - 1):
            curr, prev = values[i], values[i + 1]
            if prev and prev != 0 and curr is not None:
                try:
                    change = abs(float(curr) / float(prev) - 1)
                    if change > threshold:
                        self.warnings.append({
                            "type": "YOY_ANOMALY",
                            "severity": "高",
                            "message": f"{section_name}/{field_name}: "
                                       f"同比变化 {change*100:.0f}% 超过 {threshold*100:.0f}% 阈值",
                        })
                except (ValueError, ZeroDivisionError):
                    pass

    def check_audit_risk(self, audit_opinion: str):
        """Warn if audit opinion is not clean."""
        if audit_opinion and audit_opinion not in ("标准无保留意见", "—", ""):
            self.warnings.append({
                "type": "AUDIT_RISK",
                "severity": "高",
                "message": f"审计意见非标准: {audit_opinion}",
            })

    def check_goodwill_ratio(self, goodwill: float, total_assets: float):
        """Warn if goodwill/total_assets > 20%."""
        if goodwill and total_assets and total_assets > 0:
            ratio = float(goodwill) / float(total_assets)
            if ratio > 0.20:
                self.warnings.append({
                    "type": "GOODWILL_RISK",
                    "severity": "高",
                    "message": f"商誉占总资产比例 {ratio*100:.1f}% 超过 20%",
                })

    def check_debt_ratio(self, total_liab: float, total_assets: float):
        """Warn if debt ratio > 70%."""
        if total_liab and total_assets and total_assets > 0:
            ratio = float(total_liab) / float(total_assets)
            if ratio > 0.70:
                self.warnings.append({
                    "type": "LEVERAGE_RISK",
                    "severity": "中",
                    "message": f"资产负债率 {ratio*100:.1f}% 超过 70%",
                })

    def format_warnings(self) -> str:
        """Format all collected warnings as section 13 markdown."""
        lines = [format_header(2, "13. 风险警示 (脚本自动生成)"), ""]

        if not self.warnings:
            lines.append("未检测到异常。")
            return "\n".join(lines)

        # Group by severity
        high = [w for w in self.warnings if w["severity"] == "高"]
        medium = [w for w in self.warnings if w["severity"] == "中"]
        low = [w for w in self.warnings if w["severity"] == "低"]

        if high:
            lines.append("**高风险:**")
            for w in high:
                lines.append(f"- [{w['type']}] {w['message']}")
            lines.append("")
        if medium:
            lines.append("**中风险:**")
            for w in medium:
                lines.append(f"- [{w['type']}] {w['message']}")
            lines.append("")
        if low:
            lines.append("**低风险:**")
            for w in low:
                lines.append(f"- [{w['type']}] {w['message']}")
            lines.append("")

        lines.append(f"*共 {len(self.warnings)} 条自动警示*")
        return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Collect financial data from Tushare Pro API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --code 600887.SH
  %(prog)s --code 600887 --output output/data_pack_market.md
  %(prog)s --code 00700.HK --extra-fields balancesheet.defer_tax_assets
        """,
    )
    parser.add_argument(
        "--code",
        required=True,
        help="Stock code (e.g., 600887.SH, 000858.SZ, 00700.HK, or plain digits)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Tushare API token (defaults to TUSHARE_TOKEN env var)",
    )
    parser.add_argument(
        "--output",
        default="output/data_pack_market.md",
        help="Output file path (default: output/data_pack_market.md)",
    )
    parser.add_argument(
        "--extra-fields",
        nargs="*",
        help="Additional fields to fetch (format: endpoint.field_name)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print parsed arguments and exit without calling API",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Validate and normalize stock code
    try:
        ts_code = validate_stock_code(args.code)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("=== Dry Run ===")
        print(f"  Stock code: {args.code} -> {ts_code}")
        print(f"  Token: {'provided via --token' if args.token else 'from TUSHARE_TOKEN env'}")
        print(f"  Output: {args.output}")
        print(f"  Extra fields: {args.extra_fields or 'none'}")
        return

    # Get token
    token = args.token or get_token()
    client = TushareClient(token)

    print(f"Collecting data for {ts_code}...")
    data_pack = client.assemble_data_pack(ts_code)

    # Handle extra fields
    if args.extra_fields:
        extra_lines = ["\n", format_header(2, "附加字段"), ""]
        for field_spec in args.extra_fields:
            parts = field_spec.split(".", 1)
            if len(parts) != 2:
                extra_lines.append(f"- 无效字段格式: {field_spec} (应为 endpoint.field_name)")
                continue
            endpoint, field_name = parts
            try:
                df = client._safe_call(endpoint, ts_code=ts_code, fields=f"ts_code,end_date,{field_name}")
                if not df.empty:
                    extra_lines.append(f"**{endpoint}.{field_name}**:")
                    extra_lines.append(df.to_markdown(index=False))
                    extra_lines.append("")
                else:
                    extra_lines.append(f"- {endpoint}.{field_name}: 无数据")
            except Exception as e:
                extra_lines.append(f"- {endpoint}.{field_name}: 获取失败 ({e})")
        data_pack += "\n".join(extra_lines)

    # Write output
    import os
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(data_pack)
    print(f"Output written to {args.output}")
    print(f"File size: {os.path.getsize(args.output):,} bytes")


if __name__ == "__main__":
    main()
