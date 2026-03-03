from __future__ import annotations

import glob
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ProjectConfig = Dict[str, Any]
PipelineStages = Dict[str, pd.DataFrame]
DEFAULT_EXPECTED_FILE = "20251101 UPitt Space List - In Scope.xlsx"


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


def _resolve_configured_column_names(config: ProjectConfig, configured_cols: Iterable[str]) -> List[str]:
    """Accept either logical names or physical Excel column names in config lists."""
    columns_map = config.get("columns", {})
    physical_values = set(columns_map.values())

    resolved: List[str] = []
    for col in configured_cols:
        if col in physical_values:
            resolved.append(col)
        else:
            resolved.append(_get_column_name(config, col))
    return resolved


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


def _resolve_file_path(source: ProjectConfig) -> str:
    """Resolve explicit file path or glob patterns with clear multi-file guidance."""
    file_path = source.get("file_path")
    if file_path:
        return str(file_path)

    file_glob = source.get("file_glob")
    if not file_glob:
        raise ValueError("Missing 'file_path' (or optional 'file_glob') in source config.")

    matched = sorted(glob.glob(file_glob))
    if len(matched) == 1:
        return matched[0]
    if len(matched) == 0:
        expected = source.get("expected_file_name", DEFAULT_EXPECTED_FILE)
        raise FileNotFoundError(
            f"找不到文件！请确认 '{expected}' 是否已放在目录下。\n"
            f"提示：当前 file_glob='{file_glob}' 未匹配到任何文件。"
        )

    raise ValueError(
        "检测到多个文件来源，请改用 config['sources'] 逐个声明，或把 file_path 设置为唯一文件。\n"
        f"当前 file_glob='{file_glob}' 匹配到: {matched}"
    )


def _guard_file_exists(file_path: str, expected_file_name: str | None = None) -> None:
    """Gatekeeper for friendly file-not-found diagnostics."""
    path = Path(file_path)
    if path.exists():
        return

    expected = expected_file_name or DEFAULT_EXPECTED_FILE
    raise FileNotFoundError(
        f"找不到文件！请确认 '{expected}' 是否已放在目录下。\n"
        f"当前路径: {file_path}\n"
        "如果文件名不同：请在 config['file_path'] 或 source['file_path'] 中改成真实文件名。\n"
        "如果有多个来源：请使用 config['sources']=[...]。\n"
        "如果来源是数据库：请使用 source={'source_type':'database', ...}。"
    )


def _load_source_data(source: ProjectConfig, root_config: ProjectConfig) -> pd.DataFrame:
    """Load one source: excel/csv/database/dataframe."""
    source_type = source.get("source_type", "excel").lower()

    if source_type == "excel":
        file_path = _resolve_file_path(source)
        _guard_file_exists(file_path, source.get("expected_file_name", DEFAULT_EXPECTED_FILE))
        return pd.read_excel(
            file_path,
            sheet_name=source.get("sheet_name", root_config.get("sheet_name", "roompct")),
            header=source.get("header", root_config.get("header", 2)),
            dtype=_build_dtype_map(root_config),
        )

    if source_type == "csv":
        file_path = _resolve_file_path(source)
        _guard_file_exists(file_path, source.get("expected_file_name", DEFAULT_EXPECTED_FILE))
        return pd.read_csv(file_path, dtype=_build_dtype_map(root_config))

    if source_type == "database":
        query = source.get("query")
        if not query:
            raise ValueError("Database source requires 'query'.")

        sqlite_path = source.get("sqlite_path")
        if sqlite_path:
            _guard_file_exists(sqlite_path, source.get("expected_file_name", sqlite_path))
            with sqlite3.connect(sqlite_path) as conn:
                return pd.read_sql_query(query, conn)

        connection = source.get("connection")
        if connection is not None:
            return pd.read_sql_query(query, connection)

        connection_uri = source.get("connection_uri")
        if connection_uri is not None:
            return pd.read_sql(query, connection_uri)

        raise ValueError(
            "Database source requires one of: 'sqlite_path', 'connection', or 'connection_uri'."
        )

    if source_type == "dataframe":
        df = source.get("dataframe")
        if not isinstance(df, pd.DataFrame):
            raise ValueError("dataframe source requires a pandas DataFrame in source['dataframe']")
        return df.copy()

    raise ValueError(f"Unsupported source_type: '{source_type}'")


def _load_raw_data(config: ProjectConfig) -> pd.DataFrame:
    """Load raw data from one or multiple sources."""
    if config.get("sources"):
        sources = config["sources"]
        frames: List[pd.DataFrame] = []
        for idx, source in enumerate(sources):
            loaded = _load_source_data(source, config)
            loaded = loaded.copy()
            loaded["__source_name"] = source.get("source_name", f"source_{idx + 1}")
            frames.append(loaded)

        return pd.concat(frames, ignore_index=True, sort=False)

    # backward-compatible single-source config
    single_source = {
        "source_type": config.get("source_type", "excel"),
        "file_path": config.get("file_path"),
        "file_glob": config.get("file_glob"),
        "sheet_name": config.get("sheet_name", "roompct"),
        "header": config.get("header", 2),
        "query": config.get("query"),
        "sqlite_path": config.get("sqlite_path"),
        "connection": config.get("connection"),
        "connection_uri": config.get("connection_uri"),
        "expected_file_name": config.get("expected_file_name", DEFAULT_EXPECTED_FILE),
    }
    return _load_source_data(single_source, config)


def load_and_clean_data(config: ProjectConfig) -> pd.DataFrame:
    """
    Load source data using config, then normalize/clean with decay-tolerant behavior.

    Supports:
      - single source (legacy): file_path/sheet_name/header
      - multi-source: config['sources'] with source_type excel/csv/database/dataframe
    """
    df = _load_raw_data(config)
    df = _strip_dataframe_strings(df)

    room_code_col = _get_column_name(config, config.get("room_code_col", "room_code"))
    if room_code_col in df.columns:
        df[room_code_col] = df[room_code_col].map(_normalize_room_code)
    else:
        logger.warning("Missing room code column '%s'.", room_code_col)

    numeric_config_cols = config.get("numeric_cols", [])
    numeric_cols = _resolve_configured_column_names(config, numeric_config_cols)
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            logger.warning("Missing numeric column '%s'; coercion skipped.", col)

    return df


def generate_floor_summaries(df: pd.DataFrame, config: ProjectConfig) -> pd.DataFrame:
    """Build floor-level summaries from a decoupled building-floor composite key."""
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
    """Compare Calculated Area vs (Room Area * Percentage) and return outliers > 1 sqft."""
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


def run_space_programming_pipeline(config: ProjectConfig) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """End-to-end pipeline orchestration."""
    stages = run_space_programming_pipeline_staged(config)
    return stages["df_clean"], stages["df_final"], stages["discrepancy_outliers"]


def run_space_programming_pipeline_staged(config: ProjectConfig) -> PipelineStages:
    """Run pipeline in explicit stages and return each stage result."""
    df_raw = _load_raw_data(config)
    df_clean = load_and_clean_data(config)
    df_final = generate_floor_summaries(df_clean, config)
    discrepancy_outliers = detect_discrepancy_outliers(df_clean, config)

    return {
        "df_raw": df_raw,
        "df_clean": df_clean,
        "df_final": df_final,
        "discrepancy_outliers": discrepancy_outliers,
    }


def run_pitt_pipeline(config: ProjectConfig, export_path: str = "Floor_Summary_Result.xlsx") -> PipelineStages:
    """Convenience wrapper for UPitt-style execution and export."""
    logger.info("🚀 Start loading UPitt space data...")
    stages = run_space_programming_pipeline_staged(config)
    stages["df_final"].to_excel(export_path, index=False)
    logger.info("💾 Floor summary exported to: %s", export_path)
    return stages


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("Pipeline module ready. Import and call run_space_programming_pipeline(...).")
