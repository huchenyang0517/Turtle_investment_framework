# CLAUDE.md - Turtle Investment Framework (龟龟投资策略 v1.0)

## Project Overview
AI-assisted fundamental analysis system for Chinese/HK stocks. Hybrid architecture:
Python scripts for deterministic data collection + LLM prompts for qualitative analysis.

## Session Start Checklist
Every new session MUST begin with these steps in order:
1. `pwd` -- confirm you are in the project root
2. `cat claude-progress.txt` -- understand what has been completed
3. Check remaining features:
   ```
   python3 -c "import json; features=[f for f in json.load(open('feature_list.json'))['features'] if not f['passes']]; print(f'{len(features)} features remaining'); [print(f'  [{f[\"id\"]}] {f[\"description\"]}') for f in features[:5]]"
   ```
4. `bash init.sh` -- verify environment
5. `python3 -m pytest tests/ -x -q` -- verify existing tests pass

## Implementation Rules
- Work on ONE feature at a time from feature_list.json
- Follow the feature's `steps` array sequentially
- Write tests BEFORE or ALONGSIDE implementation (never skip tests)
- After completing a feature, mark `passes: true` in feature_list.json
- Commit after each completed feature with descriptive message
- Update claude-progress.txt at end of session
- NEVER remove or edit existing tests -- this is unacceptable

## Architecture Notes
- **Python scripts**: `scripts/tushare_collector.py`, `scripts/pdf_preprocessor.py`
- **LLM prompts**: `prompts/` directory (coordinator, phase1-3, references/)
- **All financial amounts**: millions RMB (Tushare raw yuan / 1e6), formatted with commas
- **Token**: `TUSHARE_TOKEN` environment variable (never hardcode)
- **Dependencies**: see `scripts/requirements.txt`
- **snowball-report-downloader**: sibling project at ../SKILL_snowball_report_download/

## Key Design Decisions
- Phase 1 split: 1A (Python/Tushare, deterministic) + 1B (Agent/WebSearch, qualitative)
- Phase 2 split: 2A (Python/pdfplumber, keyword matching) + 2B (Agent, structured extraction)
- PDF extraction reduced from 18 items to 5 (P2/P3/P4/P6/P13)
- Parent company data from Tushare report_type=4 (not PDF)
- yfinance kept as fallback when Tushare fails

## File Conventions
- Output files: `output/` directory (gitignored)
- Test fixtures: `tests/fixtures/`
- Design doc: `龟龟投资策略_workflow_设计方案.md` (reference only, do not modify)
- Original prompts: `龟龟投资策略_v0.16_alpha/` (reference only, do not modify)
- Working prompts: `prompts/` (these are the ones to edit)

## Testing Strategy
- **Unit tests**: pytest, mock Tushare responses from `tests/fixtures/`
- **Integration tests**: live API (require TUSHARE_TOKEN), mark with `@pytest.mark.integration`
- **End-to-end**: full pipeline on Yili (600887.SH) as reference stock
- **Prompt testing**: manual via Claude Code execution (not automated)

## Common Commands
```bash
# Environment setup
bash init.sh

# Run tushare collector
python3 scripts/tushare_collector.py --code 600887.SH --output output/data_pack_market.md

# Run PDF preprocessor
python3 scripts/pdf_preprocessor.py --pdf path/to/report.pdf --output output/pdf_sections.json

# Run tests
python3 -m pytest tests/ -v

# Run single test file
python3 -m pytest tests/test_config.py -v

# Full analysis (Claude Code slash command)
# /turtle-analysis 600887
```

## Commit Convention
- `feat(category): description [feature #N]`
- `fix(category): description [feature #N]`
- `test(category): description [feature #N]`
- Category matches feature_list.json categories

## Milestone Tags
- v1.0-alpha: after infrastructure (features 1-8)
- v1.0-beta: after all scripts working (features 1-47)
- v1.0-rc1: after integration tests pass (features 1-76)
- v1.0: after packaging complete (features 1-78)
