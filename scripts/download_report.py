#!/usr/bin/env python3
"""Download financial report PDFs from given URLs."""

import argparse
import os
import sys
import requests


def parse_args():
    parser = argparse.ArgumentParser(description="Download financial report PDF")
    parser.add_argument("--url", required=True, help="PDF URL to download")
    parser.add_argument("--stock-code", required=True, help="Stock code (e.g., SH600887)")
    parser.add_argument("--report-type", default="年报", help="Report type")
    parser.add_argument("--year", default="", help="Report year")
    parser.add_argument("--save-dir", default=".", help="Directory to save the file")
    return parser.parse_args()


def download_pdf(url, save_path):
    """Download a PDF from URL to save_path."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/pdf,*/*",
        "Referer": "https://xueqiu.com/",
    }
    resp = requests.get(url, headers=headers, timeout=60, stream=True)
    resp.raise_for_status()

    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    with open(save_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    return os.path.getsize(save_path)


def main():
    args = parse_args()

    # Build filename
    code = args.stock_code.replace("SH", "").replace("SZ", "")
    filename = f"{code}_{args.year}_{args.report_type}.pdf"
    save_path = os.path.join(args.save_dir, filename)

    print(f"Downloading: {args.url}")
    print(f"Saving to: {save_path}")

    try:
        filesize = download_pdf(args.url, save_path)
        filepath = os.path.abspath(save_path)
        print("\n---RESULT---")
        print(f"status: SUCCESS")
        print(f"filepath: {filepath}")
        print(f"filesize: {filesize}")
        print(f"message: Downloaded successfully")
        print("---END---")
    except Exception as e:
        print("\n---RESULT---")
        print(f"status: FAILED")
        print(f"filepath: N/A")
        print(f"filesize: 0")
        print(f"message: {e}")
        print("---END---")
        sys.exit(1)


if __name__ == "__main__":
    main()
