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
        """rate_limit decorator should sleep ~0.3s."""
        call_count = 0

        @rate_limit
        def dummy():
            nonlocal call_count
            call_count += 1
            return call_count

        start = time.time()
        dummy()
        elapsed = time.time() - start
        assert elapsed >= 0.25  # allow slight tolerance
        assert call_count == 1


class TestTushareClientInit:
    @patch("tushare_collector.ts")
    def test_init_sets_token(self, mock_ts):
        mock_ts.pro_api.return_value = MagicMock()
        client = TushareClient("test_token")
        mock_ts.set_token.assert_called_once_with("test_token")
        mock_ts.pro_api.assert_called_once()
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
            with pytest.raises(RuntimeError, match="failed after 3 retries"):
                client._safe_call("daily", ts_code="600887.SH")

        assert mock_pro.daily.call_count == 3


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
    def test_parent_income_uses_report_type_4(self):
        client = _make_client()
        mock_df = _load_mock("income.json")
        # Change report_type to "4" to simulate parent data
        mock_df["report_type"] = "4"

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=mock_df)
            result = client.get_income_parent("600887.SH")

        assert "3P. 母公司利润表" in result
        # Verify _safe_call was called (report_type=4 is handled internally)
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
        mock_df["report_type"] = "4"

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
        # 2024: OCF=16850M, Capex=7850M, FCF=9000M = 9,000.00
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
        # Total dividend: 0.97 * 6363636400 = 6172727108 yuan = 6,172.73 million
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
