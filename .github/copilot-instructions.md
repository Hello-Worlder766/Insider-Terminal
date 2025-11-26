# Copilot / AI Agent Instructions — Insider Trading Monitor

Purpose
- Provide concise, project-specific guardrails and technical context to help an AI agent work productively in this repository.
- Focus on the data flow, entry points, critical conventions and practical gotchas discoverable from the code.

Quick summary (Big picture)
- This repo scrapes SEC Form 4 filings (EDGAR), extracts insider trades, and exposes the data via a small Flask dashboard.
- Main components (active):
  - `sec_scraper_and_uploader.py` (canonical scraper): Scraper/parser that fetches Form 4 files, parses trades (hyper-aggressive XML parsing), and uploads to the dashboard API.
  - `app.py`: Flask web server that hosts the dashboard and receives uploaded data at `/api/upload_trades`.
  - `config.py`: Central configuration (API keys, user agent, file paths, API endpoint).
 - Archived files (legacy):
  - `sec_monitor_and_upload.py` (moved to `archive/legacy/`): Historical variant kept for reference — do not edit unless migrating features to the canonical `sec_scraper_and_uploader.py`.
  - Example data dumps (archived): `insider_trades_data.json`, `trades_data.json`, `sec_data_for_upload.json`, and the original `insider_trades.json`.
    - Root placeholders now exist for `insider_trades.json` (empty list) and `insider_trades_data.json` (empty trades object) to maintain compatibility with `app.py` and other scripts.
  - `config.py`: Central configuration (API keys, user agent, file paths, API endpoint).
  - `archive/`: Older pipeline variants, utilities, and testing scripts (useful for examples and reference: `archive/final.v1.py`, `archive/upload_to_dashboard.py`, `archive/setup_db.py`).

Run / debug fast paths
- Activate the project's virtualenv (local workspace has `venv311`):

```zsh
source venv311/bin/activate
```

- Start the dashboard (server):

```zsh
python app.py
# Dashboard opens at http://127.0.0.1:5000/
```

 - Run the canonical scraper and uploader locally (posts data to the running dashboard):

 ```zsh
 python sec_scraper_and_uploader.py
 ```

- Use the mock uploader to test the API quickly:

```zsh
python archive/upload_to_dashboard.py
```

Critical data contracts and API behavior
- Endpoint: `/api/upload_trades` (POST). Both scraper and mock uploader call `API_ENDPOINT` in `config.py`.
- Authentication: Header `X-API-KEY` must match `DASHBOARD_API_KEY` in `config.py`.
- Payload shape: MUST be a JSON object {"trades": [ ... trade dictionaries ... ]}.
- Dashboard expects trade objects with these keys for rendering/searching: `ticker`, `company_name`, `filer`, `person_title`, `date`, `code`, `value`, `shares`, `price`.
  - Note: Scripts in `archive/` and older versions use variations (e.g., `issuer`/`issuer_name`/`company`). If you change the parser, ensure the uploaded keys are compatible with `app.py` or adapt the dashboard accordingly.

Parsing & conventions
-- XML Parsing: Use namespace-agnostic matching (the codebase often uses `element.tag.endswith('tagName')` to avoid namespace issues); see `extract_value_via_iteration()` in `sec_scraper_and_uploader.py` (legacy reference: `archive/legacy/sec_monitor_and_upload.py`).
- Target filters: `TARGET_CODES = ['P', 'S', 'M', 'X', 'V']`. `VALUE_CODES = ['P', 'S']` are used to compute estimated trade value (P/S only).
- Rate limiting: Respect SEC limits (~10 requests/sec) — scripts use `REQUEST_DELAY = 0.15`.
- Data files: `DATA_FILE` (default `insider_trades.json`) is used by `app.py` to render the dashboard.

Project-specific patterns & gotchas
- Duplicate code: Several scripts (`sec_*` and `archive/*`) reimplement parsing. When making fixes, update all relevant scripts or extract shared utilities.
- Inconsistent trade keys: The codebase contains fields named `issuer`, `company`, `issuer_name`, `company_name`, `person_title`, `relationship` — confirm the dashboard field names before changing parsers.
- API structure: The dashboard overwrites `DATA_FILE` on each upload (no incremental append). Be cautious if changing this behavior.
- `config.py` stores secrets (API key). Avoid committing real keys to public repos; consider using environment variables if migrating to production.

Note: This repo prefers reading the API key from `DASHBOARD_API_KEY` when present; for backwards compatibility it also supports `DASHBOARD_PRIVATE_KEY`. Keep your `.env` local and do not commit it.

Where to change behavior or add features
- To change parsing logic or target filters: update `sec_scraper_and_uploader.py` (canonical). If you're migrating code from older scripts, use `archive/legacy/sec_monitor_and_upload.py` for reference, then run `python app.py` + `python sec_scraper_and_uploader.py` to validate.
- To support alternate storage (db vs file): use `archive/setup_db.py` and the `archive/` pipelines as examples; `app.py` currently reads/writes JSON only.
- To add tests: `archive/test_*` are useful; add unit tests for `extract_value_via_iteration()` and `clean_and_extract_xml()` to help future changes.

Examples (concrete snippets & patterns to emulate)
- Use the canonical header and API key:
```python
from config import DASHBOARD_API_KEY, SEC_USER_AGENT, API_ENDPOINT
headers = {'User-Agent': SEC_USER_AGENT}
requests.post(API_ENDPOINT, headers={'X-API-KEY': DASHBOARD_API_KEY}, json={'trades': trades})
```

- Namespace-agnostic element matching:
```python
for element in element.iter():
    if element.tag.endswith('transactionShares'):
        ...
```

- Robust value extraction pattern:
```python
def extract_value_via_iteration(parent_element, target_tag_name):
    # (See repo's implementation) — attempts to find container tag and then inner <value>
    ...
```

Tasks AI can do without owner guidance
- Clean up duplicated parsing code into a shared module `insider_parser.py` used by both scrapers and archive scripts.
- Unify trade dict keys across scrapers to match `app.py` expectations (normalize to `company_name` and `person_title`).
- Add unit tests for the XML cleaning/parsing utilities and `upload_trades_to_dashboard()` (mocking requests).

Security & privacy
- `config.py` contains a plain-text API key. If publishing or CI is used, prefer env vars and secrets.
- Users may need to update `SEC_USER_AGENT` to their own contact info for proper SEC usage.

Useful files to inspect
- `app.py` — dashboard server, data rendering & API route.
- `config.py` — essential constants that all scripts rely on.
- `sec_scraper_and_uploader.py` — primary scraper implementation and uploader.
 - `sec_monitor_and_upload.py` — archived legacy variant (see `archive/legacy/sec_monitor_and_upload.py`). Use `sec_scraper_and_uploader.py`.
- `archive/` — utility scripts (upload mock data, db pipeline, varying parsers). In particular: `archive/upload_to_dashboard.py`, `archive/final.v1.py`, `archive/setup_db.py`, `archive/parse_form4.py`.

If unsure, ask the repository owner
- Which script is the canonical scraper vs historical variants in `archive/`? Prefer working against the most updated one(s) (`sec_scraper_and_uploader.py`).
- Confirm expected final JSON schema (field names) used by the dashboard.
- Confirm the policy for storing API keys and what should be replaced by environment variables.

End
- If you update the API schema or parser output keys, update `app.py` accordingly and add a short note in `config.py`.
- For any changes that modify the data contract, also update `archive/upload_to_dashboard.py` and `archive/final.v1.py` to keep test harnesses valid.