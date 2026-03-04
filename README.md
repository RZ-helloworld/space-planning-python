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
  - Dynamic mapping UI (`st.selectbox`) for required columns: Room Code / Room Type / Calculated Area
  - Optional manual header-row override switch when auto-detection is wrong
  - Mapping defaults hide `Unnamed:*` columns (toggle available to show advanced columns)
- **Strategy Sandbox (Sidebar)**
  - Area Threshold slider (`150–500 sqft`)
  - Target Density slider (`80–200 sqft/person`)
  - Department + Building multiselect filters
- **Recommendation Engine**
  - `Subdivide`: Office and area > threshold
  - `Reallocate`: occupancy == 0 or net area < 50
  - Potential gain: `max(Current Area - Target Density, 0)`
- **Staged Confirmation Flow**
  - Step 1: Confirm header alignment before anything else
  - Step 2: Confirm required-field mapping and data readiness
  - Step 3: Explicitly run task engine before actionable output
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
