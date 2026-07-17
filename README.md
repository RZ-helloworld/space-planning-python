# Space Strategy Workbench

A small Streamlit preview for turning a room inventory into an explainable
Integrity and Opportunity task list.

## Current flow

```text
Upload Excel
  → or load the bundled synthetic inventory for a no-data preview
  → confirm the header
  → map source columns to the Framework-Wall schema
  → review/edit FICM-code benchmarks
  → run Integrity and Opportunity
  → review task evidence and export Excel
```

This version deliberately does **not** implement relocation, alignment, or a
space-assignment solver. Their callable inputs are reserved in `engine.py` so
real data can be connected later without changing the current flow.

## Run

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

Click **Use demo inventory** to walk through the full interface before using a
real workbook.

## Logic

- **Framework-Wall:** `space_programming_pipeline.py` is the single cleaning
  and physical-to-logical mapping layer used by both scripts and the app.
- **Integrity:** compares `Calculated Area` with
  `Room Area × Percentage of Space`. If percentage is unavailable, Room Area
  is treated as a 100% allocation. A critical Integrity task takes priority
  over an Opportunity task.
- **Opportunity:** compares Calculated Area with an editable benchmark keyed by
  canonical `ficm_code`. Room Type is display-only and never selects a benchmark.
- **FICM defaults:** `2xx` uses the Laboratory default (300 sqft), `3xx` uses
  Office / Conference (240 sqft), and `6xx` uses General Use (240 sqft).
- **Benchmark override:** the shipped values are explicitly labeled
  `Demo Default`. Editing a number for a FICM code marks it as `Session Override`.
- **Future interfaces:** optional Alignment and Relocation Difficulty hooks
  exist in the engine but are not required to run the preview.

## Test

```bash
python -m unittest discover -s tests -v
```

The tests use synthetic data only. They cover room-share percentages,
Integrity precedence, FICM benchmark overrides, leading-zero room codes,
name-independent FICM selection, and the future scoring hooks.

## Privacy note

Real client data is not stored in this repository. In Streamlit deployment,
uploaded workbooks are transmitted to the app server for processing in the
current session. This app does not intentionally write uploaded data to disk
or commit it to GitHub.

