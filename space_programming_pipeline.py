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
    """Trim whitespace from headers and all string-like cell values."""
    cleaned = df.copy()
    cleaned.columns = [str(col).strip() for col in cleaned.columns]
    object_cols = cleaned.select_dtypes(include=["object", "string"]).columns
    for col in object_cols:
        cleaned[col] = cleaned[col].map(
            lambda value: value.strip() if isinstance(value, str) else value
        )
    return cleaned


def _get_column_name(config: ProjectConfig, logical_name: str) -> str:
    return str(config.get("columns", {}).get(logical_name, logical_name)).strip()


def _existing_columns(df: pd.DataFrame, names: Iterable[str]) -> List[str]:
    return [name for name in names if name in df.columns]


def _resolve_configured_column_names(
    config: ProjectConfig, configured_cols: Iterable[str]
) -> List[str]:
    physical_values = set(config.get("columns", {}).values())
    return [
        str(col).strip() if col in physical_values else _get_column_name(config, col)
        for col in configured_cols
    ]


def _build_dtype_map(config: ProjectConfig) -> Mapping[str, str]:
    room_code_col = _get_column_name(
        config, config.get("room_code_col", "room_code")
    )
    return {room_code_col: "string"}


def _normalize_room_code(value: Any) -> Any:
    if pd.isna(value):
        return pd.NA
    cleaned = str(value).strip()
    return cleaned if cleaned else pd.NA


def _resolve_file_path(source: ProjectConfig) -> str:
    if source.get("file_path"):
        return str(source["file_path"])

    file_glob = source.get("file_glob")
    if not file_glob:
        raise ValueError("Missing 'file_path' (or optional 'file_glob') in source config.")

    matched = sorted(glob.glob(file_glob))
    if len(matched) == 1:
        return matched[0]
    if not matched:
        expected = source.get("expected_file_name", DEFAULT_EXPECTED_FILE)
        raise FileNotFoundError(
            f"找不到文件！请确认 '{expected}' 是否已放在目录下。\n"
            f"提示：当前 file_glob='{file_glob}' 未匹配到任何文件。"
        )
    raise ValueError(
        "检测到多个文件来源，请使用 config['sources'] 逐个声明，"
        "或把 file_path 设置为唯一文件。\n"
        f"当前 file_glob='{file_glob}' 匹配到: {matched}"
    )


def _guard_file_exists(file_path: str, expected_file_name: str | None = None) -> None:
    if Path(file_path).exists():
        return
    expected = expected_file_name or DEFAULT_EXPECTED_FILE
    raise FileNotFoundError(
        f"找不到文件！请确认 '{expected}' 是否已放在目录下。\n"
        f"当前路径: {file_path}\n"
        "文件名不同：修改 file_path；多个来源：使用 sources；"
        "数据库来源：使用 source_type='database'。"
    )


def _load_source_data(source: ProjectConfig, root_config: ProjectConfig) -> pd.DataFrame:
    source_type = str(source.get("source_type", "excel")).lower()

    if source_type == "excel":
        file_path = _resolve_file_path(source)
        _guard_file_exists(file_path, source.get("expected_file_name"))
        return pd.read_excel(
            file_path,
            sheet_name=source.get("sheet_name", root_config.get("sheet_name", "roompct")),
            header=source.get("header", root_config.get("header", 2)),
            dtype=_build_dtype_map(root_config),
        )

    if source_type == "csv":
        file_path = _resolve_file_path(source)
        _guard_file_exists(file_path, source.get("expected_file_name"))
        return pd.read_csv(file_path, dtype=_build_dtype_map(root_config))

    if source_type == "database":
        query = source.get("query")
        if not query:
            raise ValueError("Database source requires 'query'.")
        sqlite_path = source.get("sqlite_path")
        if sqlite_path:
            _guard_file_exists(sqlite_path, str(sqlite_path))
            with sqlite3.connect(sqlite_path) as conn:
                return pd.read_sql_query(query, conn)
        if source.get("connection") is not None:
            return pd.read_sql_query(query, source["connection"])
        if source.get("connection_uri") is not None:
            return pd.read_sql(query, source["connection_uri"])
        raise ValueError(
            "Database source requires sqlite_path, connection, or connection_uri."
        )

    if source_type == "dataframe":
        frame = source.get("dataframe")
        if not isinstance(frame, pd.DataFrame):
            raise ValueError("dataframe source requires a pandas DataFrame.")
        return frame.copy()

    raise ValueError(f"Unsupported source_type: '{source_type}'")


def _load_raw_data(config: ProjectConfig) -> pd.DataFrame:
    if config.get("sources"):
        frames: List[pd.DataFrame] = []
        for index, source in enumerate(config["sources"]):
            loaded = _load_source_data(source, config).copy()
            loaded["__source_name"] = source.get("source_name", f"source_{index + 1}")
            frames.append(loaded)
        return pd.concat(frames, ignore_index=True, sort=False)

    source = {
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
    return _load_source_data(source, config)


def clean_dataframe(df: pd.DataFrame, config: ProjectConfig) -> pd.DataFrame:
    """Apply the Framework-Wall cleaning rules to an in-memory DataFrame."""
    cleaned = _strip_dataframe_strings(df)

    room_code_col = _get_column_name(
        config, config.get("room_code_col", "room_code")
    )
    if room_code_col in cleaned.columns:
        cleaned[room_code_col] = cleaned[room_code_col].map(_normalize_room_code)
    else:
        logger.warning("Missing room code column '%s'.", room_code_col)

    numeric_cols = _resolve_configured_column_names(
        config, config.get("numeric_cols", [])
    )
    for col in numeric_cols:
        if col in cleaned.columns:
            cleaned[col] = pd.to_numeric(cleaned[col], errors="coerce")
        else:
            logger.warning("Missing numeric column '%s'; coercion skipped.", col)

    return cleaned


def load_and_clean_data(config: ProjectConfig) -> pd.DataFrame:
    """Load configured data and apply the shared cleaning layer."""
    return clean_dataframe(_load_raw_data(config), config)


def build_canonical_inventory(
    df: pd.DataFrame, config: ProjectConfig
) -> pd.DataFrame:
    """Map physical source columns to the logical schema consumed by the app."""
    cleaned = clean_dataframe(df, config)
    canonical = pd.DataFrame(index=cleaned.index)

    for logical_name, physical_name in config.get("columns", {}).items():
        physical = str(physical_name).strip()
        canonical[logical_name] = (
            cleaned[physical] if physical in cleaned.columns else pd.NA
        )

    for provenance_col in ["__source_name"]:
        if provenance_col in cleaned.columns:
            canonical[provenance_col] = cleaned[provenance_col]
    canonical["__source_row"] = cleaned.index.astype(int)

    for col in ["room_code", "floor_code", "room_type", "department", "building"]:
        if col in canonical.columns:
            canonical[col] = canonical[col].astype("string").str.strip()

    for col in ["calculated_area", "room_area", "percentage", "occupancy"]:
        if col in canonical.columns:
            canonical[col] = pd.to_numeric(canonical[col], errors="coerce")

    if "calculated_area" in canonical.columns:
        canonical = canonical[canonical["calculated_area"].notna()].copy()

    return canonical.reset_index(drop=True)


def audit_canonical_inventory(canonical: pd.DataFrame) -> pd.DataFrame:
    """Return row-level expected area and discrepancy evidence.

    Percentage is interpreted as either a fraction or a percent. When the
    percentage field is absent, Room Area is treated as a 100% allocation.
    """
    audit = pd.DataFrame(index=canonical.index)
    for col in ["__source_name", "__source_row", "room_code"]:
        if col in canonical.columns:
            audit[col] = canonical[col]

    calculated = pd.to_numeric(
        canonical.get("calculated_area", pd.Series(np.nan, index=canonical.index)),
        errors="coerce",
    )
    room_area = pd.to_numeric(
        canonical.get("room_area", pd.Series(np.nan, index=canonical.index)),
        errors="coerce",
    )
    percentage = pd.to_numeric(
        canonical.get("percentage", pd.Series(np.nan, index=canonical.index)),
        errors="coerce",
    )
    factor = pd.Series(
        np.where(percentage.abs() > 1, percentage / 100.0, percentage),
        index=canonical.index,
        dtype="float64",
    ).fillna(1.0)

    audit["calculated_area"] = calculated
    audit["expected_area"] = room_area * factor
    audit["discrepancy_sqft"] = calculated - audit["expected_area"]
    audit["discrepancy_abs_sqft"] = audit["discrepancy_sqft"].abs()
    audit["integrity_source"] = np.where(
        room_area.notna() & percentage.notna(),
        "Room Area × Percentage",
        np.where(room_area.notna(), "Room Area (100%)", "Unavailable"),
    )
    return audit.reset_index(drop=True)


def generate_floor_summaries(
    df: pd.DataFrame, config: ProjectConfig
) -> pd.DataFrame:
    id_components = [
        _get_column_name(config, component)
        for component in config.get("id_components", [])
    ]
    existing_components = _existing_columns(df, id_components)
    working = df.copy()

    if existing_components:
        working["building_floor_key"] = (
            working[existing_components]
            .fillna("UNKNOWN")
            .astype(str)
            .apply(lambda row: "-".join(value.strip() for value in row.values), axis=1)
        )
    else:
        logger.warning("No id_components available; using a single UNKNOWN group.")
        working["building_floor_key"] = "UNKNOWN"

    truth_area_col = _get_column_name(
        config, config.get("truth_area_col", "calculated_area")
    )
    room_code_col = _get_column_name(
        config, config.get("room_code_col", "room_code")
    )
    agg_spec: Dict[str, Tuple[str, Any]] = {}
    if truth_area_col in working.columns:
        agg_spec["total_calculated_area"] = (truth_area_col, "sum")
    if room_code_col in working.columns:
        agg_spec["distinct_room_count"] = (room_code_col, pd.Series.nunique)

    if not agg_spec:
        return working[["building_floor_key"]].drop_duplicates().reset_index(drop=True)

    return (
        working.groupby("building_floor_key", dropna=False)
        .agg(**agg_spec)
        .reset_index()
        .sort_values("building_floor_key")
        .reset_index(drop=True)
    )


def detect_discrepancy_outliers(
    df: pd.DataFrame, config: ProjectConfig, threshold: float = 1.0
) -> pd.DataFrame:
    canonical = build_canonical_inventory(df, config)
    audit = audit_canonical_inventory(canonical)
    return audit[audit["discrepancy_abs_sqft"] > threshold].reset_index(drop=True)


def run_space_programming_pipeline(
    config: ProjectConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    stages = run_space_programming_pipeline_staged(config)
    return stages["df_clean"], stages["df_final"], stages["discrepancy_outliers"]


def run_space_programming_pipeline_staged(config: ProjectConfig) -> PipelineStages:
    """Load once, then expose raw, clean, canonical, summary, and audit stages."""
    df_raw = _load_raw_data(config)
    df_clean = clean_dataframe(df_raw, config)
    canonical_inventory = build_canonical_inventory(df_raw, config)
    df_final = generate_floor_summaries(df_clean, config)
    discrepancy_outliers = detect_discrepancy_outliers(df_raw, config)
    return {
        "df_raw": df_raw,
        "df_clean": df_clean,
        "canonical_inventory": canonical_inventory,
        "df_final": df_final,
        "discrepancy_outliers": discrepancy_outliers,
    }


def run_pitt_pipeline(
    config: ProjectConfig, export_path: str = "Floor_Summary_Result.xlsx"
) -> PipelineStages:
    stages = run_space_programming_pipeline_staged(config)
    stages["df_final"].to_excel(export_path, index=False)
    logger.info("Floor summary exported to: %s", export_path)
    return stages


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("Pipeline module ready.")

