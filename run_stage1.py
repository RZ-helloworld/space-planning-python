"""Stage-1 runner: produce a clean data pool for downstream workflow."""

from __future__ import annotations

import logging

from space_programming_pipeline import run_space_programming_pipeline_staged

logging.basicConfig(level=logging.INFO)

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
    # Both logical names and physical Excel header names are supported.
    "numeric_cols": ["Room Area", "Percentage of Space", "Calculated Area"],
    "truth_area_col": "calculated_area",
    "room_code_col": "room_code",
}


def main() -> None:
    stages = run_space_programming_pipeline_staged(PITT_CONFIG)

    df_clean = stages["df_clean"]
    df_final = stages["df_final"]
    discrepancy_outliers = stages["discrepancy_outliers"]

    # Clean data pool for next stage.
    df_clean.to_parquet("data_pool_clean.parquet", index=False)
    df_clean.to_csv("data_pool_clean.csv", index=False, encoding="utf-8-sig")

    # Secondary outputs.
    df_final.to_excel("df_final_floor_summary.xlsx", index=False)
    discrepancy_outliers.to_csv("discrepancy_outliers.csv", index=False, encoding="utf-8-sig")

    print("✅ Done")
    print(f"clean rows: {len(df_clean)}")
    print(f"floor groups: {len(df_final)}")
    print(f"outliers: {len(discrepancy_outliers)}")


if __name__ == "__main__":
    main()
