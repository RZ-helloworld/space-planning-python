# space-planning-python

Modular Python pipeline for architectural space programming with a **framework-wall** architecture (logic decoupled from institution-specific Excel schemas).

## Install dependencies

```bash
python -m pip install -r requirements.txt
```

> If your editor still shows `Import "numpy" could not be resolved` or `Import "pandas" could not be resolved`, select the same Python interpreter/venv where you installed the requirements.

## Troubleshooting: missing `requirements.txt`

If you cannot find the dependency file, verify you are in the repository root:

```bash
pwd
ls
```

You should see:

- `requirements.txt`
- `space_programming_pipeline.py`
- `README.md`

Then install with:

```bash
python -m pip install -r requirements.txt
```


## UPitt phased run (你的配置可直接用)

```python
import logging
from space_programming_pipeline import run_pitt_pipeline

PITT_CONFIG = {
    "file_path": "20251101 UPitt Space List - In Scope.xlsx",
    "sheet_name": "Rooms Pct",
    "header": 2,
    "columns": {
        "building": "Building Code",
        "floor": "Floor Code",
        "room_code": "Room Code",
        "room_area": "Room Area",
        "percentage": "Percentage of Space",
        "calculated_area": "Calculated Area",
    },
    "id_components": ["building", "floor"],
    # 支持 logical names 或 physical Excel names 两种写法
    "numeric_cols": ["Room Area", "Percentage of Space", "Calculated Area"],
    "truth_area_col": "calculated_area",
    "room_code_col": "room_code",
}

logging.basicConfig(level=logging.INFO)
stages = run_pitt_pipeline(PITT_CONFIG, export_path="Floor_Summary_Result.xlsx")

print(stages["df_raw"].shape)
print(stages["df_clean"].shape)
print(stages["df_final"].head())
print(stages["discrepancy_outliers"].head())
```

## What is included

- `ProjectConfig` dictionary-driven ingestion (file path, sheet, header row, column mapping).
- `load_and_clean_data(config)` for whitespace cleanup, robust numeric coercion, and decay-tolerant missing-column warnings.
- `generate_floor_summaries(df, config)` for bottom-up aggregation by composite Building-Floor key.
- `detect_discrepancy_outliers(df, config)` to audit `Calculated Area` against `Room Area * Percentage`.
- `run_space_programming_pipeline(config)` orchestration returning:
  - `df_clean`
  - `df_final`
  - `discrepancy_outliers`

## Quick start

```python
from space_programming_pipeline import (
    run_space_programming_pipeline,
    run_space_programming_pipeline_staged,
)

PROJECT_CONFIG = {
    "file_path": "./data/space_program.xlsx",
    "sheet_name": "roompct",
    "header": 2,
    "columns": {
        "building": "Building",
        "floor": "Floor",
        "room_code": "Room Code",
        "room_area": "Room Area",
        "percentage": "Percentage",
        "calculated_area": "Calculated Area",
    },
    "id_components": ["building", "floor"],
    "numeric_cols": ["room_area", "percentage", "calculated_area"],
    "truth_area_col": "calculated_area",
    "room_code_col": "room_code",
}

df_clean, df_final, discrepancy_outliers = run_space_programming_pipeline(PROJECT_CONFIG)

# staged execution (for debugging / QA)
stages = run_space_programming_pipeline_staged(PROJECT_CONFIG)
print(stages["df_raw"].shape)
print(stages["df_clean"].shape)
print(stages["df_final"].head())
print(stages["discrepancy_outliers"].head())
```

`df_final` is prepared for adjacency analysis and BI/export workflows.
