"""Configuration and utility functions for Turtle Investment Framework."""

from __future__ import annotations

import os
import re
import glob
from typing import Optional


def get_token() -> str:
    """Get Tushare Pro API token from environment variable.

    Returns:
        str: The Tushare API token.

    Raises:
        RuntimeError: If TUSHARE_TOKEN is not set.
    """
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise RuntimeError(
            "TUSHARE_TOKEN environment variable is not set.\n"
            "Set it with: export TUSHARE_TOKEN='your_token_here'\n"
            "Get a token at: https://tushare.pro/register"
        )
    return token


def validate_stock_code(code: str) -> str:
    """Validate and normalize a stock code to Tushare format.

    Supports:
        - A-share: 600887.SH, 000858.SZ, 300750.SZ
        - HK: 00700.HK, 09988.HK
        - Plain codes: 600887 -> 600887.SH, 000858 -> 000858.SZ

    Args:
        code: Stock code string.

    Returns:
        str: Normalized Tushare-format code (e.g., '600887.SH').

    Raises:
        ValueError: If the code format is not recognized.
    """
    code = code.strip().upper()

    # Already in Tushare format
    if re.match(r"^\d{6}\.(SH|SZ)$", code):
        return code
    if re.match(r"^\d{5}\.HK$", code):
        return code

    # Plain 6-digit A-share code
    if re.match(r"^\d{6}$", code):
        if code.startswith("6"):
            return f"{code}.SH"
        elif code.startswith(("0", "3")):
            return f"{code}.SZ"
        else:
            raise ValueError(
                f"Unrecognized A-share code prefix: {code}. "
                "Expected 6xxxxx (SH), 0xxxxx or 3xxxxx (SZ)."
            )

    # Plain 5-digit HK code
    if re.match(r"^\d{5}$", code):
        return f"{code}.HK"

    raise ValueError(
        f"Unrecognized stock code format: '{code}'. "
        "Expected: 600887.SH, 000858.SZ, 00700.HK, or plain digits."
    )


def check_local_pdf(stock_code: str, year: int, search_dir: str = ".") -> Optional[str]:
    """Check if an annual report PDF exists locally.

    Args:
        stock_code: Stock code (e.g., '600887' or '600887.SH').
        year: Fiscal year to look for.
        search_dir: Directory to search in.

    Returns:
        Path to the PDF if found, None otherwise.
    """
    # Extract numeric part of code
    numeric_code = stock_code.split(".")[0]

    patterns = [
        f"*{numeric_code}*{year}*.pdf",
        f"*{numeric_code}*{year}*年报*.pdf",
        f"{numeric_code}_{year}_*.pdf",
    ]

    for pattern in patterns:
        matches = glob.glob(os.path.join(search_dir, pattern))
        if matches:
            return matches[0]

    return None


def validate_pdf(filepath: str) -> "tuple[bool, str]":
    """Validate that a file is a real PDF.

    Args:
        filepath: Path to the file.

    Returns:
        Tuple of (is_valid, reason).
    """
    if not os.path.exists(filepath):
        return False, f"File not found: {filepath}"

    size = os.path.getsize(filepath)
    if size < 100 * 1024:  # 100KB minimum
        return False, f"File too small ({size} bytes), likely not a real annual report"

    with open(filepath, "rb") as f:
        magic = f.read(5)
        if magic != b"%PDF-":
            return False, "File does not start with %PDF- magic bytes"

    return True, "Valid PDF"
