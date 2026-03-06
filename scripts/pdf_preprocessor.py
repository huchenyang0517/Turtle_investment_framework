#!/usr/bin/env python3
"""Turtle Investment Framework - PDF Preprocessor (Phase 2A).

Scans annual report PDFs for 5 target sections using keyword matching
and outputs structured JSON for Agent fine-extraction.

Target sections:
    P2: Restricted cash (受限资产)
    P3: AR aging (应收账款账龄)
    P4: Related party transactions (关联方交易)
    P6: Contingent liabilities (或有负债)
    P13: Non-recurring items (非经常性损益)

Usage:
    python3 scripts/pdf_preprocessor.py --pdf report.pdf
    python3 scripts/pdf_preprocessor.py --pdf report.pdf --output output/sections.json
    python3 scripts/pdf_preprocessor.py --pdf report.pdf --verbose --dry-run
"""

import argparse
import sys


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract target sections from annual report PDFs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --pdf 伊利股份_2024_年报.pdf
  %(prog)s --pdf report.pdf --output output/pdf_sections.json --verbose
        """,
    )
    parser.add_argument(
        "--pdf",
        required=True,
        help="Path to the annual report PDF file",
    )
    parser.add_argument(
        "--output",
        default="output/pdf_sections.json",
        help="Output JSON file path (default: output/pdf_sections.json)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print progress messages during extraction",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print parsed arguments and exit without processing",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.dry_run:
        print("=== Dry Run ===")
        print(f"  PDF: {args.pdf}")
        print(f"  Output: {args.output}")
        print(f"  Verbose: {args.verbose}")
        return

    # TODO: Implement PDF processing (feature #37+)
    print(f"Processing {args.pdf}...")
    print("Not yet implemented. See feature_list.json features #37-#47.")


if __name__ == "__main__":
    main()
