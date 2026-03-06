#!/bin/bash
# init.sh - Turtle Investment Framework environment setup
# Run at the start of each Claude Code session

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

echo "=== Turtle Investment Framework - Environment Setup ==="
echo "Project root: $PROJECT_ROOT"
echo ""

# 1. Python environment
echo "[1/6] Checking Python environment..."
if [ ! -d ".venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv .venv
fi
source .venv/bin/activate
echo "  Python: $(python3 --version)"
echo "  venv: $VIRTUAL_ENV"

# 2. Install dependencies
echo "[2/6] Installing Python dependencies..."
pip install -q -r scripts/requirements.txt

# 3. Verify Tushare token
echo "[3/6] Checking Tushare token..."
if [ -z "$TUSHARE_TOKEN" ]; then
    echo "  WARNING: TUSHARE_TOKEN not set in environment"
    echo "  Set it with: export TUSHARE_TOKEN='your_token_here'"
    echo "  Tests requiring live API will be skipped"
else
    echo "  TUSHARE_TOKEN: set (${#TUSHARE_TOKEN} chars)"
fi

# 4. Verify snowball-report-downloader dependency
echo "[4/6] Checking snowball-report-downloader..."
SNOWBALL_PATH="$(dirname "$PROJECT_ROOT")/SKILL_snowball_report_download"
if [ -d "$SNOWBALL_PATH" ]; then
    echo "  Found at: $SNOWBALL_PATH"
else
    echo "  WARNING: snowball-report-downloader not found at $SNOWBALL_PATH"
    echo "  Phase 0 (PDF auto-download) will not be available"
fi

# 5. Create output directory
echo "[5/6] Ensuring output directory..."
mkdir -p output

# 6. Run basic tests
echo "[6/6] Running verification tests..."
python3 -m pytest tests/ -x -q --tb=short 2>&1 | tail -5

echo ""
echo "=== Setup complete ==="
echo "To run: python3 scripts/tushare_collector.py --code 600887.SH --output output/data_pack_market.md"
