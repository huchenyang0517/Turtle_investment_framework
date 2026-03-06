"""Tests for scripts/config.py — token, stock codes, PDF utilities."""

import os

import pytest

from config import get_token, validate_stock_code, check_local_pdf, validate_pdf


# --- get_token() ---

class TestGetToken:
    def test_returns_token_when_set(self, monkeypatch):
        monkeypatch.setenv("TUSHARE_TOKEN", "abc123")
        assert get_token() == "abc123"

    def test_raises_when_not_set(self, monkeypatch):
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="TUSHARE_TOKEN"):
            get_token()

    def test_raises_when_empty(self, monkeypatch):
        monkeypatch.setenv("TUSHARE_TOKEN", "")
        with pytest.raises(RuntimeError, match="TUSHARE_TOKEN"):
            get_token()


# --- validate_stock_code() ---

class TestValidateStockCode:
    # A-share with suffix
    def test_sh_code(self):
        assert validate_stock_code("600887.SH") == "600887.SH"

    def test_sz_code(self):
        assert validate_stock_code("000858.SZ") == "000858.SZ"

    def test_sz_gem_code(self):
        assert validate_stock_code("300750.SZ") == "300750.SZ"

    # HK with suffix
    def test_hk_code(self):
        assert validate_stock_code("00700.HK") == "00700.HK"

    # Plain digit codes
    def test_plain_sh(self):
        assert validate_stock_code("600887") == "600887.SH"

    def test_plain_sz(self):
        assert validate_stock_code("000858") == "000858.SZ"

    def test_plain_gem(self):
        assert validate_stock_code("300750") == "300750.SZ"

    def test_plain_hk(self):
        assert validate_stock_code("00700") == "00700.HK"

    # Case insensitive
    def test_lowercase(self):
        assert validate_stock_code("600887.sh") == "600887.SH"

    # Whitespace
    def test_whitespace(self):
        assert validate_stock_code("  600887.SH  ") == "600887.SH"

    # Invalid codes
    def test_invalid_prefix(self):
        with pytest.raises(ValueError, match="Unrecognized"):
            validate_stock_code("900123")

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="Unrecognized"):
            validate_stock_code("AAPL")

    def test_empty(self):
        with pytest.raises(ValueError, match="Unrecognized"):
            validate_stock_code("")

    def test_too_short(self):
        with pytest.raises(ValueError, match="Unrecognized"):
            validate_stock_code("123")


# --- check_local_pdf() ---

class TestCheckLocalPdf:
    def test_finds_matching_pdf(self, tmp_path):
        pdf = tmp_path / "600887_2023_annual.pdf"
        pdf.write_text("fake")
        result = check_local_pdf("600887.SH", 2023, str(tmp_path))
        assert result is not None
        assert "600887" in result

    def test_finds_chinese_pattern(self, tmp_path):
        pdf = tmp_path / "伊利600887_2023年报.pdf"
        pdf.write_text("fake")
        result = check_local_pdf("600887", 2023, str(tmp_path))
        assert result is not None

    def test_returns_none_when_no_match(self, tmp_path):
        result = check_local_pdf("600887.SH", 2023, str(tmp_path))
        assert result is None

    def test_returns_none_wrong_year(self, tmp_path):
        pdf = tmp_path / "600887_2022_annual.pdf"
        pdf.write_text("fake")
        result = check_local_pdf("600887.SH", 2023, str(tmp_path))
        assert result is None

    def test_strips_suffix(self, tmp_path):
        pdf = tmp_path / "600887_2023_report.pdf"
        pdf.write_text("fake")
        result = check_local_pdf("600887.SH", 2023, str(tmp_path))
        assert result is not None


# --- validate_pdf() ---

class TestValidatePdf:
    def test_valid_pdf(self, tmp_path):
        pdf = tmp_path / "report.pdf"
        # Write PDF magic bytes + enough content to exceed 100KB
        with open(pdf, "wb") as f:
            f.write(b"%PDF-1.4 ")
            f.write(b"\x00" * (101 * 1024))
        is_valid, reason = validate_pdf(str(pdf))
        assert is_valid is True
        assert "Valid" in reason

    def test_file_not_found(self):
        is_valid, reason = validate_pdf("/nonexistent/file.pdf")
        assert is_valid is False
        assert "not found" in reason

    def test_file_too_small(self, tmp_path):
        pdf = tmp_path / "tiny.pdf"
        pdf.write_bytes(b"%PDF-1.4 tiny content")
        is_valid, reason = validate_pdf(str(pdf))
        assert is_valid is False
        assert "too small" in reason.lower()

    def test_wrong_magic_bytes(self, tmp_path):
        pdf = tmp_path / "fake.pdf"
        with open(pdf, "wb") as f:
            f.write(b"NOT A PDF FILE")
            f.write(b"\x00" * (101 * 1024))
        is_valid, reason = validate_pdf(str(pdf))
        assert is_valid is False
        assert "magic" in reason.lower() or "%PDF" in reason
