"""Tests for TushareClient class — init, rate limiting, retry, data methods."""

import json
import os
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd
import pytest

from tushare_collector import TushareClient, WarningsCollector, rate_limit

MOCK_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "mock_tushare_responses")


def _load_mock(filename: str) -> pd.DataFrame:
    """Load a mock fixture as DataFrame."""
    with open(os.path.join(MOCK_DIR, filename)) as f:
        data = json.load(f)
    if isinstance(data, list):
        return pd.DataFrame(data)
    return pd.DataFrame([data])


def _make_client():
    """Create a TushareClient with mocked tushare module."""
    with patch("tushare_collector.ts") as mock_ts:
        mock_ts.pro_api.return_value = MagicMock()
        client = TushareClient("test_token")
    return client


class TestRateLimit:
    def test_enforces_delay(self):
        """rate_limit decorator should sleep ~0.5s."""
        call_count = 0

        @rate_limit
        def dummy():
            nonlocal call_count
            call_count += 1
            return call_count

        start = time.time()
        dummy()
        elapsed = time.time() - start
        assert elapsed >= 0.45  # allow slight tolerance
        assert call_count == 1


class TestTushareClientInit:
    @patch("tushare_collector.ts")
    def test_init_sets_token(self, mock_ts):
        mock_ts.pro_api.return_value = MagicMock()
        client = TushareClient("test_token")
        mock_ts.set_token.assert_called_once_with("test_token")
        mock_ts.pro_api.assert_called_once_with(timeout=30)
        assert client.token == "test_token"


class TestSafeCall:
    @patch("tushare_collector.ts")
    def test_successful_call(self, mock_ts):
        mock_pro = MagicMock()
        mock_ts.pro_api.return_value = mock_pro
        expected_df = pd.DataFrame({"col": [1, 2, 3]})
        mock_pro.stock_basic.return_value = expected_df

        client = TushareClient("token")
        # Bypass rate_limit sleep for testing speed
        with patch("tushare_collector.time.sleep"):
            result = client._safe_call("stock_basic", ts_code="600887.SH")

        assert result.equals(expected_df)
        mock_pro.stock_basic.assert_called_once_with(ts_code="600887.SH")

    @patch("tushare_collector.ts")
    def test_retry_on_failure(self, mock_ts):
        mock_pro = MagicMock()
        mock_ts.pro_api.return_value = mock_pro
        expected_df = pd.DataFrame({"col": [1]})
        # Fail twice, succeed on third
        mock_pro.income.side_effect = [
            Exception("timeout"),
            Exception("timeout"),
            expected_df,
        ]

        client = TushareClient("token")
        with patch("tushare_collector.time.sleep"):
            result = client._safe_call("income", ts_code="600887.SH")

        assert result.equals(expected_df)
        assert mock_pro.income.call_count == 3

    @patch("tushare_collector.ts")
    def test_raises_after_max_retries(self, mock_ts):
        mock_pro = MagicMock()
        mock_ts.pro_api.return_value = mock_pro
        mock_pro.daily.side_effect = Exception("permanent failure")

        client = TushareClient("token")
        with patch("tushare_collector.time.sleep"):
            with pytest.raises(RuntimeError, match="failed after 5 retries"):
                client._safe_call("daily", ts_code="600887.SH")

        assert mock_pro.daily.call_count == 5

    @patch("tushare_collector.ts")
    def test_connection_error_recreates_pro(self, mock_ts):
        """RemoteDisconnected-style errors should re-create the pro_api client."""
        mock_pro_old = MagicMock()
        mock_pro_new = MagicMock()
        expected_df = pd.DataFrame({"col": [1]})
        # First call fails with connection error, second succeeds on new client
        mock_pro_old.cashflow.side_effect = OSError("RemoteDisconnected")
        mock_pro_new.cashflow.return_value = expected_df
        mock_ts.pro_api.side_effect = [mock_pro_old, mock_pro_new]

        client = TushareClient("token")
        with patch("tushare_collector.time.sleep"):
            result = client._safe_call("cashflow", ts_code="600887.SH")

        assert result.equals(expected_df)
        # pro_api called twice: once in __init__, once for reconnect
        assert mock_ts.pro_api.call_count == 2
        mock_pro_old.cashflow.assert_called_once()
        mock_pro_new.cashflow.assert_called_once()


# --- Feature #14: get_basic_info ---

class TestGetBasicInfo:
    def test_basic_info_output(self):
        client = _make_client()
        mock_basic = _load_mock("stock_basic.json")
        mock_daily = _load_mock("daily_basic.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(side_effect=[mock_basic, mock_daily])
            result = client.get_basic_info("600887.SH")

        assert "## 1. 基本信息" in result
        assert "伊利股份" in result
        assert "600887.SH" in result
        assert "乳品" in result

    def test_empty_data(self):
        client = _make_client()
        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=pd.DataFrame())
            result = client.get_basic_info("600887.SH")
        assert "数据缺失" in result


# --- Feature #15: get_market_data ---

class TestGetMarketData:
    def test_52_week_range(self):
        client = _make_client()
        # Create mock daily data with known high/low
        mock_df = pd.DataFrame([
            {"ts_code": "600887.SH", "trade_date": "20241230", "open": 27, "high": 35.0, "low": 26.0, "close": 27.5, "vol": 100000, "amount": 275000},
            {"ts_code": "600887.SH", "trade_date": "20240701", "open": 30, "high": 32.0, "low": 22.0, "close": 30.0, "vol": 120000, "amount": 360000},
            {"ts_code": "600887.SH", "trade_date": "20240301", "open": 28, "high": 29.0, "low": 25.0, "close": 28.0, "vol": 110000, "amount": 308000},
        ])

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_market_data("600887.SH")

        assert "## 2. 市场行情" in result
        assert "35.00" in result  # 52-week high
        assert "22.00" in result  # 52-week low
        assert "27.50" in result  # latest close


# --- Feature #16: get_income ---

class TestGetIncome:
    def test_five_year_income(self):
        client = _make_client()
        mock_df = _load_mock("income.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_income("600887.SH")

        assert "## 3. 合并利润表" in result
        assert "2024" in result
        assert "2020" in result
        # Check amount conversion: 96886000000 -> 96,886.00
        assert "96,886.00" in result
        assert "百万元" in result

    def test_empty_income(self):
        client = _make_client()
        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=pd.DataFrame())
            result = client.get_income("600887.SH")
        assert "数据缺失" in result


# --- Feature #17: get_income_parent ---

class TestGetIncomeParent:
    def test_parent_income_uses_report_type_6(self):
        client = _make_client()
        mock_df = _load_mock("income.json")
        # Change report_type to "6" to simulate parent data
        mock_df["report_type"] = "6"

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_income_parent("600887.SH")

        assert "3P. 母公司利润表" in result
        # Verify _safe_call was called (report_type=6 is handled internally)
        client._safe_call.assert_called_once()


# --- Feature #18: get_balance_sheet ---

class TestGetBalanceSheet:
    def test_five_year_balance_sheet(self):
        client = _make_client()
        mock_df = _load_mock("balancesheet.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_balance_sheet("600887.SH")

        assert "## 4. 合并资产负债表" in result
        assert "合同负债" in result
        assert "短期借款" in result
        assert "长期借款" in result
        assert "百万元" in result

    def test_interest_bearing_debt_fields(self):
        client = _make_client()
        mock_df = _load_mock("balancesheet.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_balance_sheet("600887.SH")

        # Verify st_borr and lt_borr are present (interest-bearing debt)
        assert "短期借款" in result
        assert "长期借款" in result


# --- Feature #19: get_balance_sheet_parent ---

class TestGetBalanceSheetParent:
    def test_parent_balance_sheet(self):
        client = _make_client()
        mock_df = _load_mock("balancesheet.json")
        mock_df["report_type"] = "6"

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_balance_sheet_parent("600887.SH")

        assert "4P. 母公司资产负债表" in result
        assert "货币资金" in result
        assert "长期股权投资" in result


# --- Feature #20: get_cashflow ---

class TestGetCashflow:
    def test_fcf_calculation(self):
        client = _make_client()
        mock_df = _load_mock("cashflow.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_cashflow("600887.SH")

        assert "## 5. 现金流量表" in result
        assert "自由现金流" in result
        assert "FCF" in result
        # 2024: OCF=16850M, Capex(c_pay_acq_const_fiolta)=7850M, FCF=9000M = 9,000.00
        assert "9,000.00" in result

    def test_empty_cashflow(self):
        client = _make_client()
        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=pd.DataFrame())
            result = client.get_cashflow("600887.SH")
        assert "数据缺失" in result


# --- Feature #21: get_dividends ---

class TestGetDividends:
    def test_dividend_extraction(self):
        client = _make_client()
        mock_df = _load_mock("dividend.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_dividends("600887.SH")

        assert "## 6. 分红历史" in result
        assert "2024" in result
        assert "0.9700" in result  # cash_div_tax for 2024
        # Total dividend: 0.97 * 636363.64(万股) * 10000 = 6172727108 yuan = 6,172.73 million
        assert "6,172.73" in result

    def test_empty_dividends(self):
        client = _make_client()
        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=pd.DataFrame())
            result = client.get_dividends("600887.SH")
        assert "暂无分红" in result


# --- Feature #22: get_weekly_prices ---

class TestGetWeeklyPrices:
    def test_10_year_range(self):
        client = _make_client()
        mock_df = _load_mock("weekly.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_weekly_prices("600887.SH")

        assert "## 11. 十年周线行情" in result
        assert "10年最高" in result
        assert "10年最低" in result
        # 10yr high is 41.80 (from 2021 data)
        assert "41.80" in result
        # 10yr low is 15.20 (from 2015 data)
        assert "15.20" in result

    def test_annual_aggregation(self):
        client = _make_client()
        mock_df = _load_mock("weekly.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_weekly_prices("600887.SH")

        assert "年度行情汇总" in result
        # Should have multiple years
        assert "2024" in result
        assert "2015" in result


# --- Feature #23: get_fina_indicators ---

class TestGetFinaIndicators:
    def test_financial_indicators(self):
        client = _make_client()
        mock_df = _load_mock("fina_indicator.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_fina_indicators("600887.SH")

        assert "## 12. 关键财务指标" in result
        assert "ROE" in result
        assert "毛利率" in result
        assert "17.15" in result  # ROE 2024
        assert "29.22" in result  # gross margin 2024

    def test_empty_indicators(self):
        client = _make_client()
        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=pd.DataFrame())
            result = client.get_fina_indicators("600887.SH")
        assert "数据缺失" in result


# --- Feature #24: get_segments ---

class TestGetSegments:
    def test_segment_breakdown(self):
        client = _make_client()
        mock_df = _load_mock("fina_mainbz.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_segments("600887.SH")

        assert "## 9. 主营业务构成" in result
        assert "液体乳" in result
        assert "冷饮产品" in result

    def test_permission_error(self):
        client = _make_client()
        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(side_effect=RuntimeError("no permission"))
            result = client.get_segments("600887.SH")
        assert "无权限" in result or "数据缺失" in result


# --- Feature #25: get_holders ---

class TestGetHolders:
    def test_top10_holders(self):
        client = _make_client()
        mock_df = _load_mock("top10_holders.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_holders("600887.SH")

        assert "## 7. 股东与治理" in result
        assert "呼和浩特投资" in result
        assert "9.05" in result  # hold ratio


# --- Feature #25: get_audit ---

class TestGetAudit:
    def test_audit_with_agency_and_fees(self):
        client = _make_client()
        mock_df = _load_mock("fina_audit.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_audit("600887.SH")

        assert "审计意见" in result
        assert "标准无保留意见" in result
        assert "安永华明" in result
        assert "1350.0" in result  # 13500000 / 10000 = 1350.0

    def test_audit_empty(self):
        client = _make_client()
        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=pd.DataFrame())
            result = client.get_audit("600887.SH")
        assert "审计数据缺失" in result


# --- Feature #28: assemble_data_pack ---

class TestAssembleDataPack:
    def test_all_section_headers_present(self):
        client = _make_client()
        # Mock all API calls to return empty DataFrames
        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=pd.DataFrame())
            result = client.assemble_data_pack("600887.SH")

        # Verify main structure
        assert "# 数据包 — 600887.SH" in result
        assert "Tushare Pro" in result
        assert "百万元" in result

        # Verify section headers present
        for sec in ["1. 基本信息", "2. 市场行情", "3. 合并利润表",
                     "4. 合并资产负债表", "5. 现金流量表", "6. 分红历史",
                     "11. 十年周线行情", "12. 关键财务指标"]:
            assert sec in result

        # Verify placeholder sections
        assert "8. 行业与竞争" in result
        assert "10. 管理层讨论与分析" in result
        assert "13. 风险警示" in result
        assert "Agent WebSearch" in result


# --- Feature #30: WarningsCollector ---

class TestWarningsCollector:
    def test_missing_data_warning(self):
        wc = WarningsCollector()
        wc.check_missing_data("利润表", pd.DataFrame())
        assert len(wc.warnings) == 1
        assert wc.warnings[0]["type"] == "DATA_MISSING"

    def test_yoy_anomaly(self):
        wc = WarningsCollector()
        # 400% change: 500 vs 100
        wc.check_yoy_change("利润表", "revenue", [500, 100])
        assert len(wc.warnings) == 1
        assert wc.warnings[0]["type"] == "YOY_ANOMALY"
        assert wc.warnings[0]["severity"] == "高"

    def test_yoy_normal(self):
        wc = WarningsCollector()
        wc.check_yoy_change("利润表", "revenue", [110, 100])
        assert len(wc.warnings) == 0

    def test_audit_risk(self):
        wc = WarningsCollector()
        wc.check_audit_risk("保留意见")
        assert len(wc.warnings) == 1
        assert wc.warnings[0]["type"] == "AUDIT_RISK"

    def test_audit_clean(self):
        wc = WarningsCollector()
        wc.check_audit_risk("标准无保留意见")
        assert len(wc.warnings) == 0

    def test_goodwill_risk(self):
        wc = WarningsCollector()
        # 25% goodwill ratio
        wc.check_goodwill_ratio(25e9, 100e9)
        assert len(wc.warnings) == 1
        assert wc.warnings[0]["type"] == "GOODWILL_RISK"

    def test_goodwill_ok(self):
        wc = WarningsCollector()
        wc.check_goodwill_ratio(5e9, 100e9)
        assert len(wc.warnings) == 0

    def test_debt_ratio_risk(self):
        wc = WarningsCollector()
        wc.check_debt_ratio(75e9, 100e9)
        assert len(wc.warnings) == 1
        assert wc.warnings[0]["type"] == "LEVERAGE_RISK"

    def test_format_warnings_empty(self):
        wc = WarningsCollector()
        result = wc.format_warnings()
        assert "未检测到异常" in result

    def test_format_warnings_grouped(self):
        wc = WarningsCollector()
        wc.check_audit_risk("保留意见")
        wc.check_missing_data("利润表", pd.DataFrame())
        result = wc.format_warnings()
        assert "高风险" in result
        assert "中风险" in result
        assert "共 2 条" in result


# --- _prepare_display_periods ---

class TestPrepareDisplayPeriods:
    """Tests for TushareClient._prepare_display_periods."""

    def test_annual_only_returns_five_years(self):
        """Pure annual data should return 5 years descending."""
        df = pd.DataFrame([
            {"end_date": "20241231", "revenue": 100},
            {"end_date": "20231231", "revenue": 90},
            {"end_date": "20221231", "revenue": 80},
            {"end_date": "20211231", "revenue": 70},
            {"end_date": "20201231", "revenue": 60},
        ])
        result_df, labels = TushareClient._prepare_display_periods(df)
        assert labels == ["2024", "2023", "2022", "2021", "2020"]
        assert len(result_df) == 5

    def test_annual_plus_newer_interim(self):
        """Interim reports newer than latest annual appear before annual cols."""
        df = pd.DataFrame([
            {"end_date": "20250930", "revenue": 95},
            {"end_date": "20250630", "revenue": 62},
            {"end_date": "20250331", "revenue": 31},
            {"end_date": "20241231", "revenue": 120},
            {"end_date": "20231231", "revenue": 112},
            {"end_date": "20221231", "revenue": 123},
            {"end_date": "20211231", "revenue": 110},
            {"end_date": "20201231", "revenue": 96},
        ])
        result_df, labels = TushareClient._prepare_display_periods(df)
        assert labels == ["2025Q3", "2025H1", "2025Q1", "2024", "2023", "2022", "2021", "2020"]
        assert len(result_df) == 8

    def test_older_interim_not_included(self):
        """Interim reports from same year or earlier than latest annual are excluded."""
        df = pd.DataFrame([
            {"end_date": "20240930", "revenue": 90},  # same year as latest annual
            {"end_date": "20241231", "revenue": 120},
            {"end_date": "20231231", "revenue": 112},
        ])
        result_df, labels = TushareClient._prepare_display_periods(df)
        assert labels == ["2024", "2023"]
        assert len(result_df) == 2

    def test_h1_label(self):
        """0630 end_date maps to H1 label."""
        df = pd.DataFrame([
            {"end_date": "20250630", "revenue": 62},
            {"end_date": "20241231", "revenue": 120},
        ])
        _, labels = TushareClient._prepare_display_periods(df)
        assert labels[0] == "2025H1"

    def test_q1_label(self):
        """0331 end_date maps to Q1 label."""
        df = pd.DataFrame([
            {"end_date": "20250331", "revenue": 31},
            {"end_date": "20241231", "revenue": 120},
        ])
        _, labels = TushareClient._prepare_display_periods(df)
        assert labels[0] == "2025Q1"

    def test_q3_label(self):
        """0930 end_date maps to Q3 label."""
        df = pd.DataFrame([
            {"end_date": "20250930", "revenue": 95},
            {"end_date": "20241231", "revenue": 120},
        ])
        _, labels = TushareClient._prepare_display_periods(df)
        assert labels[0] == "2025Q3"

    def test_empty_dataframe(self):
        """Empty DataFrame returns empty labels."""
        df = pd.DataFrame(columns=["end_date", "revenue"])
        result_df, labels = TushareClient._prepare_display_periods(df)
        assert labels == []
        assert result_df.empty

    def test_only_interim_no_annual(self):
        """If only interim data exists, return it (no annual cutoff)."""
        df = pd.DataFrame([
            {"end_date": "20250930", "revenue": 95},
            {"end_date": "20250630", "revenue": 62},
        ])
        result_df, labels = TushareClient._prepare_display_periods(df)
        assert labels == ["2025Q3", "2025H1"]
        assert len(result_df) == 2

    def test_deduplication(self):
        """Duplicate end_dates are removed."""
        df = pd.DataFrame([
            {"end_date": "20241231", "revenue": 120},
            {"end_date": "20241231", "revenue": 120},  # duplicate
            {"end_date": "20231231", "revenue": 112},
        ])
        result_df, labels = TushareClient._prepare_display_periods(df)
        assert labels == ["2024", "2023"]
        assert len(result_df) == 2


# --- Parent income field exclusion ---

class TestParentIncomeFieldExclusion:
    """Tests for report_type=6 excluding minority_gain/basic_eps/diluted_eps."""

    def test_report_type_6_excludes_fields(self):
        """Parent income (report_type=6) should not contain certain fields."""
        client = _make_client()
        mock_df = _load_mock("income.json")
        mock_df["report_type"] = "6"

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_income("600887.SH", report_type="6")

        assert "少数股东损益" not in result
        assert "基本EPS" not in result
        assert "稀释EPS" not in result
        # Core fields should still be present
        assert "营业收入" in result
        assert "净利润" in result
        assert "归母净利润" in result

    def test_report_type_1_includes_all_fields(self):
        """Consolidated income (report_type=1) should include all fields."""
        client = _make_client()
        mock_df = _load_mock("income.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_income("600887.SH", report_type="1")

        assert "少数股东损益" in result
        assert "基本EPS" in result
        assert "稀释EPS" in result


# --- Feature #79: Income statement expanded fields ---

class TestIncomeExpanded:
    def test_new_fields_present(self):
        """Verify 11 new income fields appear in output."""
        client = _make_client()
        mock_df = _load_mock("income.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_income("600887.SH")

        new_labels = [
            "财务费用", "所得税费用", "利润总额", "投资收益",
            "营业外收入", "营业外支出", "资产减值损失",
            "信用减值损失", "公允价值变动收益", "资产处置收益",
            "税金及附加",
        ]
        for label in new_labels:
            assert label in result, f"Missing: {label}"

    def test_field_order(self):
        """Verify fields are in accounting standards order."""
        client = _make_client()
        mock_df = _load_mock("income.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_income("600887.SH")

        ordered_labels = [
            "营业收入", "营业成本", "税金及附加",
            "销售费用", "管理费用", "研发费用", "财务费用",
            "营业利润", "营业外收入", "营业外支出",
            "利润总额", "所得税费用", "净利润", "归母净利润",
        ]
        positions = []
        for label in ordered_labels:
            pos = result.index(label)
            positions.append(pos)
        # Each label should appear after the previous
        for i in range(1, len(positions)):
            assert positions[i] > positions[i - 1], \
                f"{ordered_labels[i]} should appear after {ordered_labels[i - 1]}"

    def test_report_type_6_excludes_credit_impair(self):
        """Parent income (report_type=6) should also exclude credit_impair_loss."""
        client = _make_client()
        mock_df = _load_mock("income.json")
        mock_df["report_type"] = "6"

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_income("600887.SH", report_type="6")

        assert "信用减值损失" not in result
        # Other new fields should still be present
        assert "财务费用" in result
        assert "所得税费用" in result


# --- Feature #80: Balance sheet expanded fields ---

class TestBalanceSheetExpanded:
    def test_13_new_fields_present(self):
        """Verify 13 new balance sheet fields appear in output."""
        client = _make_client()
        mock_df = _load_mock("balancesheet.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_balance_sheet("600887.SH")

        new_labels = [
            "交易性金融资产", "其他流动资产", "无形资产",
            "在建工程", "应付账款", "应付票据",
            "递延所得税资产", "递延所得税负债", "应付债券",
            "一年内到期非流动负债", "其他流动负债",
            "流动资产合计", "流动负债合计",
        ]
        for label in new_labels:
            assert label in result, f"Missing: {label}"

    def test_balance_sheet_order(self):
        """Verify assets before liabilities before equity."""
        client = _make_client()
        mock_df = _load_mock("balancesheet.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_balance_sheet("600887.SH")

        # Assets appear before liabilities
        assert result.index("货币资金") < result.index("总资产")
        assert result.index("总资产") < result.index("短期借款")
        assert result.index("总负债") < result.index("归母所有者权益")


# --- Feature #81: Parent balance sheet expanded ---

class TestParentBalanceSheetExpanded:
    def test_parent_new_fields(self):
        """Parent balance sheet should include bond_payable, non_cur_liab_due_1y, equity."""
        client = _make_client()
        mock_df = _load_mock("balancesheet.json")
        mock_df["report_type"] = "6"

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_balance_sheet_parent("600887.SH")

        assert "4P. 母公司资产负债表" in result
        assert "应付债券" in result
        assert "一年内到期非流动负债" in result
        assert "归母权益" in result


# --- Feature #82: Cashflow expanded fields ---

class TestCashflowExpanded:
    def test_new_cashflow_fields(self):
        """Verify 5 new cashflow fields + c_pay_dist_dpcp_int_exp display."""
        client = _make_client()
        mock_df = _load_mock("cashflow.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_cashflow("600887.SH")

        new_labels = [
            "支付给职工现金", "支付的各项税费",
            "处置固定资产收回现金", "收到税费返还",
            "取得投资收益收到现金", "分配股利偿付利息",
        ]
        for label in new_labels:
            assert label in result, f"Missing: {label}"

    def test_cashflow_values(self):
        """Verify specific cashflow values appear."""
        client = _make_client()
        mock_df = _load_mock("cashflow.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_cashflow("600887.SH")

        # c_pay_to_staff 2024: 8520000000 -> 8,520.00
        assert "8,520.00" in result
        # c_pay_dist_dpcp_int_exp 2024: 5800000000 -> 5,800.00
        assert "5,800.00" in result


# --- Feature #83: Financial indicators expanded ---

class TestFinaIndicatorsExpanded:
    def test_new_indicator_fields(self):
        """Verify growth, per-share, and quality fields appear."""
        client = _make_client()
        mock_df = _load_mock("fina_indicator.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_fina_indicators("600887.SH")

        new_labels = [
            "营收同比增长率", "净利润同比增长率",
            "每股经营现金流", "每股净资产",
            "扣非净利润",
        ]
        for label in new_labels:
            assert label in result, f"Missing: {label}"

    def test_indicator_values(self):
        """Verify specific indicator values."""
        client = _make_client()
        mock_df = _load_mock("fina_indicator.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_fina_indicators("600887.SH")

        # revenue_yoy 2024: 7.12
        assert "7.12" in result
        # ocfps 2024: 2.65
        assert "2.65" in result
        # profit_dedt 2024: 9850000000 -> 9,850.00
        assert "9,850.00" in result


# --- Feature #84: Risk-free rate ---

class TestRiskFreeRate:
    def test_rf_output(self):
        """Verify risk-free rate section output."""
        client = _make_client()
        mock_df = _load_mock("yc_cb.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_risk_free_rate()

        assert "## 14. 无风险利率" in result
        assert "10年期国债收益率" in result
        assert "2.3150" in result
        assert "20260305" in result

    def test_rf_empty(self):
        """Verify graceful handling of empty data."""
        client = _make_client()
        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=pd.DataFrame())
            result = client.get_risk_free_rate()
        assert "数据缺失" in result

    def test_rf_permission_error(self):
        """Verify graceful handling of API permission error."""
        client = _make_client()
        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(side_effect=RuntimeError("no permission"))
            result = client.get_risk_free_rate()
        assert "无权限" in result or "数据缺失" in result


# --- Feature #85: Share repurchase ---

class TestRepurchase:
    def test_repurchase_output(self):
        """Verify repurchase section output."""
        client = _make_client()
        mock_df = _load_mock("repurchase.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_repurchase("600887.SH")

        assert "## 15. 股票回购" in result
        assert "回购金额" in result
        assert "累计回购金额" in result
        assert "年均回购金额" in result
        # Should show proc column
        assert "进度" in result

    def test_repurchase_dedup_removes_duplicates(self):
        """Verify duplicate (ann_date, amount) records are deduplicated."""
        client = _make_client()
        # Fixture has 8 rows including same-date and cross-date duplicates
        mock_df = _load_mock("repurchase.json")
        assert len(mock_df) == 8, "fixture should have 8 rows including duplicates"

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_repurchase("600887.SH")

        # After all dedup: 2 完成 records (1050M + 1200M), 实施 removed (same high_limit as 完成)
        stored = client._store.get("repurchase")
        assert stored is not None
        assert len(stored) == 2, f"expected 2 records after cross-date dedup, got {len(stored)}"
        assert all(stored["proc"].isin(["完成", "实施"]))

    def test_repurchase_status_filter_completed_only(self):
        """Verify only proc in ['完成', '实施'] records are kept when available."""
        client = _make_client()
        mock_df = _load_mock("repurchase.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_repurchase("600887.SH")

        stored = client._store.get("repurchase")
        for _, row in stored.iterrows():
            assert row["proc"] in ["完成", "实施"]

    def test_repurchase_fallback_no_completed(self):
        """When no executed records, fallback to deduped full data."""
        client = _make_client()
        # All records are 董事会预案/股东大会通过 (no 完成/实施)
        mock_df = pd.DataFrame([
            {"ts_code": "600887.SH", "ann_date": "20250101", "proc": "董事会预案",
             "amount": 1000000000.0, "vol": 30000000.0, "high_limit": 30.0, "low_limit": 20.0},
            {"ts_code": "600887.SH", "ann_date": "20240601", "proc": "股东大会通过",
             "amount": 800000000.0, "vol": 25000000.0, "high_limit": 28.0, "low_limit": 18.0},
        ])

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_repurchase("600887.SH")

        stored = client._store.get("repurchase")
        assert len(stored) == 2, "should fallback to all deduped records"

    def test_repurchase_amount_after_dedup(self):
        """Verify total amount reflects deduped + filtered data only."""
        client = _make_client()
        mock_df = _load_mock("repurchase.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_repurchase("600887.SH")

        # Only 完成 records: 1,050 + 1,200 = 2,250 (million)
        # In raw yuan: 1,050,000,000 + 1,200,000,000 = 2,250,000,000
        # format_number divides by 1e6 → 2,250.00
        assert "2,250.00" in result

    def test_repurchase_cross_date_dedup(self):
        """Verify same plan across different dates is deduplicated."""
        client = _make_client()
        # Two 完成 records with same (amount=1050M, high_limit=32) on different dates
        mock_df = _load_mock("repurchase.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            client.get_repurchase("600887.SH")

        stored = client._store.get("repurchase")
        # Should have exactly one record with amount=1050M (cross-date dedup)
        completed_1050 = stored[stored["amount"] == 1050000000.0]
        assert len(completed_1050) == 1, (
            f"expected 1 record for amount=1050M, got {len(completed_1050)}")

    def test_repurchase_executing_dedup(self):
        """Verify 实施 records with same high_limit keep only max amount,
        and are dropped when a 完成 record exists for the same plan."""
        client = _make_client()
        # Fixture has 实施 records (high_limit=33, amounts 800M and 300M)
        # and a 完成 record (high_limit=33, amount=1200M) for the same plan
        mock_df = _load_mock("repurchase.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            client.get_repurchase("600887.SH")

        stored = client._store.get("repurchase")
        # 实施 records should be gone (完成 takes priority for high_limit=33)
        executing = stored[stored["proc"] == "实施"]
        assert len(executing) == 0, (
            f"expected 0 实施 records (完成 takes priority), got {len(executing)}")

    def test_repurchase_warning_annotation(self):
        """Verify 注销型 warning is appended to output."""
        client = _make_client()
        mock_df = _load_mock("repurchase.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_repurchase("600887.SH")

        assert "注销型回购" in result
        assert "Phase 3" in result

    def test_repurchase_empty(self):
        """Verify graceful handling of no repurchase data."""
        client = _make_client()
        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=pd.DataFrame())
            result = client.get_repurchase("600887.SH")
        assert "无回购记录" in result

    def test_repurchase_permission_error(self):
        """Verify graceful handling of API permission error."""
        client = _make_client()
        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(side_effect=RuntimeError("no permission"))
            result = client.get_repurchase("600887.SH")
        assert "无权限" in result or "数据缺失" in result


# --- Feature #86: Share pledge statistics ---

class TestPledgeStat:
    def test_pledge_stat_output(self):
        """Verify pledge statistics section output."""
        client = _make_client()
        mock_df = _load_mock("pledge_stat.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_pledge_stat("600887.SH")

        assert "## 16. 股权质押" in result
        assert "质押笔数" in result
        assert "无限售质押" in result
        assert "有限售质押" in result
        assert "质押比例" in result
        assert "5.19" in result  # pledge_ratio

    def test_pledge_stat_empty(self):
        """Verify graceful handling of empty pledge data."""
        client = _make_client()
        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=pd.DataFrame())
            result = client.get_pledge_stat("600887.SH")
        assert "数据缺失" in result

    def test_pledge_stat_permission_error(self):
        """Verify graceful handling of API permission error."""
        client = _make_client()
        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(side_effect=RuntimeError("no permission"))
            result = client.get_pledge_stat("600887.SH")
        assert "无权限" in result or "数据缺失" in result


# --- Feature #36: WarningsCollector wired into assemble_data_pack ---

class TestAssembleDataPackWarnings:
    """Verify §13 in assembly output has auto-warnings and agent placeholder."""

    def _assemble_with_mock(self, safe_call_side_effect=None):
        """Helper: run assemble_data_pack with a custom _safe_call mock."""
        client = _make_client()
        with patch("tushare_collector.time.sleep"):
            if safe_call_side_effect is not None:
                client._safe_call = MagicMock(side_effect=safe_call_side_effect)
            else:
                client._safe_call = MagicMock(return_value=pd.DataFrame())
            result = client.assemble_data_pack("600887.SH")
        return result

    def test_section_13_has_auto_warnings_subsection(self):
        """§13 output must contain '13.1 脚本自动检测'."""
        result = self._assemble_with_mock()
        assert "13.1 脚本自动检测" in result

    def test_section_13_has_agent_supplement_placeholder(self):
        """§13 output must contain '13.2 Agent WebSearch'."""
        result = self._assemble_with_mock()
        assert "13.2 Agent WebSearch" in result
        assert "§13.2 待Agent WebSearch补充" in result

    def test_empty_data_triggers_missing_warnings(self):
        """Empty DataFrames should trigger DATA_MISSING warnings."""
        result = self._assemble_with_mock()
        assert "DATA_MISSING" in result

    def test_high_debt_ratio_triggers_warning(self):
        """80% debt ratio should trigger LEVERAGE_RISK warning."""
        def mock_safe_call(api_name, **kwargs):
            if api_name == "balancesheet":
                return pd.DataFrame([{
                    "ts_code": "600887.SH",
                    "end_date": "20231231",
                    "total_assets": 1000000,
                    "total_liab": 800000,
                    "goodwill": 10000,
                }])
            if api_name == "income":
                return pd.DataFrame([{
                    "ts_code": "600887.SH",
                    "end_date": "20231231",
                    "revenue": 100000,
                    "n_income_attr_p": 50000,
                    "n_cashflow_act": 30000,
                }])
            if api_name == "cashflow":
                return pd.DataFrame([{
                    "ts_code": "600887.SH",
                    "end_date": "20231231",
                    "n_cashflow_act": 30000,
                }])
            if api_name == "fina_audit":
                return pd.DataFrame([{
                    "ts_code": "600887.SH",
                    "end_date": "20231231",
                    "audit_agency": "普华永道",
                    "audit_result": "标准无保留意见",
                }])
            return pd.DataFrame()

        result = self._assemble_with_mock(safe_call_side_effect=mock_safe_call)
        assert "LEVERAGE_RISK" in result

    def test_audit_risk_triggers_warning(self):
        """Non-standard audit opinion should trigger AUDIT_RISK warning."""
        def mock_safe_call(api_name, **kwargs):
            if api_name == "fina_audit":
                return pd.DataFrame([{
                    "ts_code": "600887.SH",
                    "end_date": "20231231",
                    "audit_agency": "某会计所",
                    "audit_result": "保留意见",
                }])
            if api_name == "balancesheet":
                return pd.DataFrame([{
                    "ts_code": "600887.SH",
                    "end_date": "20231231",
                    "total_assets": 1000000,
                    "total_liab": 500000,
                    "goodwill": 10000,
                }])
            if api_name in ("income", "cashflow"):
                return pd.DataFrame([{
                    "ts_code": "600887.SH",
                    "end_date": "20231231",
                    "revenue": 100000,
                    "n_income_attr_p": 50000,
                    "n_cashflow_act": 30000,
                }])
            return pd.DataFrame()

        result = self._assemble_with_mock(safe_call_side_effect=mock_safe_call)
        assert "AUDIT_RISK" in result

    def test_no_anomalies_shows_clean_message(self):
        """Normal data should show '未检测到异常'."""
        def mock_safe_call(api_name, **kwargs):
            if api_name == "balancesheet":
                return pd.DataFrame([{
                    "ts_code": "600887.SH",
                    "end_date": "20231231",
                    "total_assets": 1000000,
                    "total_liab": 400000,
                    "goodwill": 10000,
                }])
            if api_name == "fina_audit":
                return pd.DataFrame([{
                    "ts_code": "600887.SH",
                    "end_date": "20231231",
                    "audit_agency": "普华永道",
                    "audit_result": "标准无保留意见",
                }])
            if api_name in ("income", "cashflow"):
                return pd.DataFrame([{
                    "ts_code": "600887.SH",
                    "end_date": "20231231",
                    "revenue": 100000,
                    "n_income_attr_p": 50000,
                    "n_cashflow_act": 30000,
                }])
            return pd.DataFrame()

        result = self._assemble_with_mock(safe_call_side_effect=mock_safe_call)
        assert "未检测到异常" in result
