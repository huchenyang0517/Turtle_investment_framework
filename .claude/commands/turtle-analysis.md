Run a full Turtle Investment Framework analysis on stock: $ARGUMENTS

Read prompts/coordinator.md and execute the full multi-phase analysis pipeline:
1. Phase 0: Check/download annual report PDF
2. Phase 1A: Run python3 scripts/tushare_collector.py --code $ARGUMENTS --output output/data_pack_market.md
3. Phase 1B: WebSearch for qualitative data (management, industry, MD&A)
4. Phase 2A: Run python3 scripts/pdf_preprocessor.py (if PDF available)
5. Phase 2B: Extract structured data from PDF sections (if available)
6. Phase 3: Execute 4-factor analysis and generate report

Output: output/{company}_{code}_分析报告.md

Usage: /turtle-analysis 600887 or /turtle-analysis 00700.HK
