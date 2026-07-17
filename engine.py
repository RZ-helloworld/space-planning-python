from __future__ import annotations

import hashlib
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from space_programming_pipeline import audit_canonical_inventory, normalize_ficm_code

ScoreHook = Callable[[pd.Series], object]

FICM_PREFIX_BENCHMARKS: Dict[str, Dict[str, object]] = {
    "2": {"ficm_family": "Laboratory", "benchmark_area": 300.0},
    "3": {"ficm_family": "Office / Conference", "benchmark_area": 240.0},
    "6": {"ficm_family": "General Use", "benchmark_area": 240.0},
}
DEFAULT_BENCHMARK_AREA = 240.0

INTEGRITY_GAP_THRESHOLD = 25.0
OPPORTUNITY_HIGH_THRESHOLD = 50.0


def _ficm_family(ficm_code: object) -> str:
    normalized = normalize_ficm_code(ficm_code)
    prefix = normalized[:1] if normalized else ""
    configured = FICM_PREFIX_BENCHMARKS.get(prefix)
    return str(configured["ficm_family"]) if configured else "Unmapped FICM"


def suggested_benchmark(ficm_code: object) -> float:
    """Return a demo benchmark using only the normalized FICM code prefix."""
    normalized = normalize_ficm_code(ficm_code)
    prefix = normalized[:1] if normalized else ""
    configured = FICM_PREFIX_BENCHMARKS.get(prefix)
    if configured:
        return float(configured["benchmark_area"])
    return DEFAULT_BENCHMARK_AREA


def build_benchmark_table(inventory: pd.DataFrame) -> pd.DataFrame:
    display = pd.DataFrame(index=inventory.index)
    display["ficm_code"] = inventory.get(
        "ficm_code", pd.Series(pd.NA, index=inventory.index, dtype="string")
    ).map(normalize_ficm_code)
    display["room_type"] = inventory.get(
        "room_type", pd.Series(pd.NA, index=inventory.index, dtype="string")
    ).astype("string").str.strip()
    display = display[display["ficm_code"] != ""].copy()

    if display.empty:
        return pd.DataFrame(
            columns=[
                "ficm_code",
                "ficm_family",
                "room_type",
                "benchmark_area",
                "source",
            ]
        )

    descriptions = (
        display.groupby("ficm_code", sort=True)["room_type"]
        .agg(
            lambda values: " / ".join(
                sorted(
                    {
                        str(value)
                        for value in values.dropna()
                        if str(value).strip() and str(value) != "<NA>"
                    }
                )
            )
        )
        .reset_index()
    )
    descriptions["ficm_family"] = descriptions["ficm_code"].map(_ficm_family)
    descriptions["benchmark_area"] = descriptions["ficm_code"].map(
        suggested_benchmark
    )
    descriptions["source"] = "Demo Default"
    return descriptions[
        ["ficm_code", "ficm_family", "room_type", "benchmark_area", "source"]
    ]


def mark_benchmark_sources(
    edited: pd.DataFrame, defaults: pd.DataFrame
) -> pd.DataFrame:
    """Mark edited demo values as session overrides without changing engine logic."""
    result = edited.copy()
    result["benchmark_area"] = pd.to_numeric(
        result.get("benchmark_area"), errors="coerce"
    )
    default_map = {
        normalize_ficm_code(row.ficm_code): float(row.benchmark_area)
        for row in defaults.itertuples(index=False)
    }
    result["source"] = [
        "Session Override"
        if pd.notna(area) and default_map.get(normalize_ficm_code(ficm_code)) != float(area)
        else "Demo Default"
        for ficm_code, area in zip(result["ficm_code"], result["benchmark_area"])
    ]
    return result


def _task_id(row: pd.Series) -> str:
    parts = [
        row.get("__source_name", "upload"),
        row.get("__source_row", ""),
        row.get("building", ""),
        row.get("floor_code", ""),
        row.get("room_code", ""),
        row.get("department", ""),
        row.get("action", ""),
    ]
    payload = "|".join("" if pd.isna(value) else str(value) for value in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _apply_hook(
    work: pd.DataFrame, hook: Optional[ScoreHook]
) -> pd.Series:
    if hook is None:
        return pd.Series(pd.NA, index=work.index, dtype="object")
    return work.apply(hook, axis=1)


def generate_tasks(
    inventory: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    alignment: Optional[ScoreHook] = None,
    relocation_difficulty: Optional[ScoreHook] = None,
    integrity_threshold: float = INTEGRITY_GAP_THRESHOLD,
    opportunity_threshold: float = OPPORTUNITY_HIGH_THRESHOLD,
) -> pd.DataFrame:
    """Generate the current-phase Integrity and Opportunity task list.

    Alignment and relocation difficulty are deliberately optional hooks. They
    are visible in the result contract but do not block the preview workflow.
    """
    work = inventory.copy().reset_index(drop=True)
    audit = audit_canonical_inventory(work)
    for col in [
        "expected_area",
        "discrepancy_sqft",
        "discrepancy_abs_sqft",
        "integrity_source",
    ]:
        work[col] = audit[col]

    has_integrity_input = work["expected_area"].notna()
    is_critical = (
        work["discrepancy_abs_sqft"].gt(float(integrity_threshold)).fillna(False)
    )
    work["integrity_score"] = np.select(
        [has_integrity_input & is_critical, has_integrity_input],
        ["Critical", "Normal"],
        default="Unknown",
    )

    benchmark_map = {
        normalize_ficm_code(row.ficm_code): float(row.benchmark_area)
        for row in benchmark_df.itertuples(index=False)
        if normalize_ficm_code(row.ficm_code) and pd.notna(row.benchmark_area)
    }
    benchmark_source_map = {
        normalize_ficm_code(row.ficm_code): str(getattr(row, "source", "Provided"))
        for row in benchmark_df.itertuples(index=False)
        if normalize_ficm_code(row.ficm_code)
    }
    ficm_key = work.get(
        "ficm_code", pd.Series(pd.NA, index=work.index, dtype="string")
    ).map(normalize_ficm_code)
    work["benchmark_reference"] = ficm_key.map(benchmark_map)
    work["benchmark_source"] = ficm_key.map(benchmark_source_map)
    work["potential_area_released"] = (
        work["calculated_area"] - work["benchmark_reference"]
    )
    has_benchmark_input = work["benchmark_reference"].notna()
    is_high_opportunity = work["potential_area_released"].ge(
        float(opportunity_threshold)
    ).fillna(False)
    work["opportunity_score"] = np.select(
        [has_benchmark_input & is_high_opportunity, has_benchmark_input],
        ["High", "Low"],
        default="Unknown",
    )

    work["strategic_alignment_score"] = _apply_hook(work, alignment)
    work["relocation_difficulty_score"] = _apply_hook(
        work, relocation_difficulty
    )

    actionable = (work["integrity_score"] == "Critical") | (
        work["opportunity_score"] == "High"
    )
    tasks = work[actionable].copy()
    tasks["action"] = np.select(
        [
            (tasks["integrity_score"] == "Critical").to_numpy(dtype=bool),
            (tasks["opportunity_score"] == "High").to_numpy(dtype=bool),
        ],
        ["AUDIT DATA", "CAPTURE OPPORTUNITY"],
        default="REVIEW",
    )
    tasks["priority_rank"] = np.where(tasks["action"] == "AUDIT DATA", 0, 1)
    tasks["__task_id"] = tasks.apply(_task_id, axis=1)
    tasks = tasks.sort_values(
        ["priority_rank", "potential_area_released"],
        ascending=[True, False],
        na_position="last",
    ).reset_index(drop=True)

    preferred: List[str] = [
        "__task_id",
        "__source_name",
        "__source_row",
        "room_code",
        "floor_code",
        "ficm_code",
        "room_type",
        "department",
        "building",
        "calculated_area",
        "room_area",
        "percentage",
        "expected_area",
        "discrepancy_abs_sqft",
        "integrity_source",
        "integrity_score",
        "benchmark_reference",
        "benchmark_source",
        "potential_area_released",
        "opportunity_score",
        "strategic_alignment_score",
        "relocation_difficulty_score",
        "action",
    ]
    return tasks[[col for col in preferred if col in tasks.columns]]

