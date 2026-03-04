# Space Strategy Workbench

A Streamlit app for architectural **space strategy + logistics** with a WMS-like operational workflow.

## Install

```bash
python -m pip install -r requirements.txt
```

## Run the web app

```bash
streamlit run app.py
```

## What it does

- **Robust Data Ingestor (Gatekeeper)**
  - Upload Excel with `st.file_uploader`
  - Auto header detection: skips leading blank rows and uses the first non-empty row as header (e.g., table starts on row 3)
  - Auto cleaning: trim headers/string values, numeric coercion for `Calculated Area` and `Percentage of Space`
  - Dynamic mapping UI (`st.selectbox`) for required columns: Room Code / Floor Code / Room Type / Calculated Area
  - Optional manual header-row override switch when auto-detection is wrong
  - Mapping defaults hide `Unnamed:*` columns (toggle available to show advanced columns)
- **Strategy Sandbox (Sidebar)**
  - Target Density slider (`80–200 sqft/person`)
  - Department + Building filters now use dropdown mode (`All` / `Select`) + multi-select
- **Rating Engine (Current Phase)**
  - **Integrity Score**: `Critical` when `|Calculated Area - Room Area| > 25 sqft` (if Room Area is mapped)
  - **Opportunity Score**: based on `Calculated Area - benchmark area` (FICM-like room-type references)
  - Actionable in this phase: rows marked `Critical` integrity or `High` opportunity
  - Reallocate logic is intentionally disabled in this phase
- **Staged Confirmation Flow**
  - Step 1: Confirm header alignment before anything else
  - Step 2: Confirm required-field mapping and data readiness
  - Step 3: Review/edit benchmark table by room type
  - Step 4: Explicitly run rating engine before actionable output
  - Actionable Task cards are paged in batches of 20 per page
- **Interactive Dashboard**
  - Top metrics (`st.metric`): total potential gains, total tasks
  - Split view: searchable inventory table + actionable task cards
  - Bar chart: potential gains by department
- **Export & Feedback Loop**
  - Per-task architectural notes via `st.text_input`
  - One-click Excel export of final strategy task list

## Notes

- If `Occupancy`, `Net Area`, `Department`, or `Building` are missing in uploaded data, they can be left unmapped.
- The legacy pipeline module (`space_programming_pipeline.py`) is retained for script-based workflows.
