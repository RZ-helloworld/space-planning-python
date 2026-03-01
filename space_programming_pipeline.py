from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Mapping, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ProjectConfig = Dict[str, Any]
PipelineStages = Dict[str, pd.DataFrame]


def _strip_dataframe_strings(df: pd.DataFrame) -> pd.DataFrame:
    """Trim whitespace from column names and all string-like cell values."""
    cleaned = df.copy()
    cleaned.columns = [str(col).strip() for col in cleaned.columns]

    object_cols = cleaned.select_dtypes(include=["object", "string"]).columns
    for col in object_cols:
        cleaned[col] = cleaned[col].map(lambda v: v.strip() if isinstance(v, str) else v)

    return cleaned


def _get_column_name(config: ProjectConfig, logical_name: str) -> str:
    """Resolve logical field names into physical column names."""
    columns = config.get("columns", {})
    return columns.get(logical_name, logical_name)


def _existing_columns(df: pd.DataFrame, column_names: Iterable[str]) -> List[str]:
    return [col for col in column_names if col in df.columns]


def _build_dtype_map(config: ProjectConfig) -> Mapping[str, str]:
    """Build pandas dtype map for critical fields that must not lose formatting."""
    room_code_col = _get_column_name(config, config.get("room_code_col", "room_code"))
    return {room_code_col: "string"}


def _normalize_room_code(value: Any) -> Any:
    """Normalize room-code values while preserving explicit strings and nulls."""
    if pd.isna(value):
        return np.nan
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else np.nan
    return str(value).strip()


def load_and_clean_data(config: ProjectConfig) -> pd.DataFrame:
    """
    Load Excel data using config, then normalize/clean with decay-tolerant behavior.

    Required config keys:
      - file_path
      - sheet_name
      - header
      - columns: logical_name -> physical_excel_column
      - numeric_cols: list of logical names to coerce to numeric
      - room_code_col: logical name for the room code field
    """
    df = pd.read_excel(
        config["file_path"],
        sheet_name=config.get("sheet_name", "roompct"),
        header=config.get("header", 2),
        dtype=_build_dtype_map(config),
    )
    df = _strip_dataframe_strings(df)

    room_code_col = _get_column_name(config, config.get("room_code_col", "room_code"))
    if room_code_col in df.columns:
        df[room_code_col] = df[room_code_col].map(_normalize_room_code)
    else:
        logger.warning("Missing room code column '%s'.", room_code_col)

    numeric_logical_cols = config.get("numeric_cols", [])
    numeric_cols = [_get_column_name(config, name) for name in numeric_logical_cols]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            logger.warning("Missing numeric column '%s'; coercion skipped.", col)

    return df


def generate_floor_summaries(df: pd.DataFrame, config: ProjectConfig) -> pd.DataFrame:
    """
    Build floor-level summaries from a decoupled building-floor composite key.

    Aggregation rules:
      - truth_area_col: sum
      - room_code_col: nunique (distinct physical room count)
    """
    id_components = [_get_column_name(config, c) for c in config.get("id_components", [])]
    existing_id_components = _existing_columns(df, id_components)

    if not existing_id_components:
        logger.warning("No id_components available; creating a single 'UNKNOWN' group.")
        working = df.copy()
        working["building_floor_key"] = "UNKNOWN"
    else:
        working = df.copy()
        working["building_floor_key"] = (
            working[existing_id_components]
            .fillna("UNKNOWN")
            .astype(str)
            .apply(lambda row: "-".join([v.strip() for v in row.values]), axis=1)
        )

    truth_area_col = _get_column_name(config, config.get("truth_area_col", "calculated_area"))
    room_code_col = _get_column_name(config, config.get("room_code_col", "room_code"))

    agg_spec: Dict[str, Tuple[str, str]] = {}
    if truth_area_col in working.columns:
        agg_spec["total_calculated_area"] = (truth_area_col, "sum")
    else:
        logger.warning("Missing truth area column '%s'; area totals unavailable.", truth_area_col)

    if room_code_col in working.columns:
        agg_spec["distinct_room_count"] = (room_code_col, pd.Series.nunique)
    else:
        logger.warning("Missing room code column '%s'; distinct count unavailable.", room_code_col)

    if not agg_spec:
        return (
            working[["building_floor_key"]]
            .drop_duplicates()
            .reset_index(drop=True)
            .sort_values("building_floor_key")
        )

    return (
        working.groupby("building_floor_key", dropna=False)
        .agg(**agg_spec)
        .reset_index()
        .sort_values("building_floor_key")
        .reset_index(drop=True)
    )


def detect_discrepancy_outliers(df: pd.DataFrame, config: ProjectConfig) -> pd.DataFrame:
    """
    Compare Calculated Area vs (Room Area * Percentage) and return outliers > 1 sqft.

    Supports percentage values stored as decimal fractions (0.25) or percentages (25).
    """
    truth_area_col = _get_column_name(config, config.get("truth_area_col", "calculated_area"))
    room_area_col = _get_column_name(config, "room_area")
    pct_col = _get_column_name(config, "percentage")

    required = [truth_area_col, room_area_col, pct_col]
    missing = [col for col in required if col not in df.columns]
    if missing:
        logger.warning(
            "Unable to run discrepancy audit. Missing required columns: %s", ", ".join(missing)
        )
        return pd.DataFrame(
            columns=[
                "row_index",
                "calculated_area",
                "expected_area",
                "discrepancy_sqft",
                "discrepancy_abs_sqft",
            ]
        )

    audit = df[[truth_area_col, room_area_col, pct_col]].copy()
    pct = pd.to_numeric(audit[pct_col], errors="coerce")
    normalized_pct = np.where(np.abs(pct) > 1, pct / 100.0, pct)
    audit["expected_area"] = pd.to_numeric(audit[room_area_col], errors="coerce") * normalized_pct
    audit["discrepancy_sqft"] = pd.to_numeric(audit[truth_area_col], errors="coerce") - audit["expected_area"]
    audit["discrepancy_abs_sqft"] = audit["discrepancy_sqft"].abs()

    outliers = audit[audit["discrepancy_abs_sqft"] > 1.0].copy()
    outliers = outliers.reset_index().rename(columns={"index": "row_index", truth_area_col: "calculated_area"})

    return outliers[
        [
            "row_index",
            "calculated_area",
            "expected_area",
            "discrepancy_sqft",
            "discrepancy_abs_sqft",
        ]
    ]


def run_space_programming_pipeline(
    config: ProjectConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    End-to-end pipeline orchestration.

    Returns:
      - df_clean: cleaned row-level data
      - df_final: floor summary table for downstream tools
      - discrepancy_outliers: audit table where discrepancy > 1 sqft
    """
    stages = run_space_programming_pipeline_staged(config)
    df_clean = stages["df_clean"]
    df_final = stages["df_final"]
    discrepancy_outliers = stages["discrepancy_outliers"]
    return df_clean, df_final, discrepancy_outliers


def run_space_programming_pipeline_staged(config: ProjectConfig) -> PipelineStages:
    """
    Run pipeline in explicit stages and return each stage result.

    Useful for debugging and step-by-step QA in notebooks or scripts.
    """
    df_raw = pd.read_excel(
        config["file_path"],
        sheet_name=config.get("sheet_name", "roompct"),
        header=config.get("header", 2),
        dtype=_build_dtype_map(config),
    )
    df_clean = _strip_dataframe_strings(df_raw)

    room_code_col = _get_column_name(config, config.get("room_code_col", "room_code"))
    if room_code_col in df_clean.columns:
        df_clean[room_code_col] = df_clean[room_code_col].map(_normalize_room_code)
    else:
        logger.warning("Missing room code column '%s'.", room_code_col)

    numeric_logical_cols = config.get("numeric_cols", [])
    numeric_cols = [_get_column_name(config, name) for name in numeric_logical_cols]
    for col in numeric_cols:
        if col in df_clean.columns:
            df_clean[col] = pd.to_numeric(df_clean[col], errors="coerce")
        else:
            logger.warning("Missing numeric column '%s'; coercion skipped.", col)

    df_final = generate_floor_summaries(df_clean, config)
    discrepancy_outliers = detect_discrepancy_outliers(df_clean, config)

    return {
        "df_raw": df_raw,
        "df_clean": df_clean,
        "df_final": df_final,
        "discrepancy_outliers": discrepancy_outliers,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    PROJECT_CONFIG: ProjectConfig = {
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

    logger.info("Pipeline module ready. Call run_space_programming_pipeline(PROJECT_CONFIG).")
