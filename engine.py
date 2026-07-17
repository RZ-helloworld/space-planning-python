from __future__ import annotations

import hashlib
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from space_programming_pipeline import audit_canonical_inventory

ScoreHook = Callable[[pd.Series], object]

DEMO_BENCHMARKS: Dict[str, float] = {
    "office": 240.0,
    "lab": 300.0,
    "conference": 180.0,
    "classroom": 350.0,
    "support": 120.0,
}

INTEGRITY_GAP_THRESHOLD = 25.0
OPPORTUNITY_HIGH_THRESHOLD = 50.0


def suggested_benchmark(room_type: object) -> float:
    """Return a clearly labeled demo benchmark for UI preview."""
    normalized = str(room_type).strip().lower()
    for keyword, value in DEMO_BENCHMARKS.items():
        if keyword in normalized:
            return value
    return 240.0


def build_benchmark_table(inventory: pd.DataFrame) -> pd.DataFrame:
    room_types = sorted(
        value
        for value in inventory.get("room_type", pd.Series(dtype="string"))
        .dropna()
        .astype(str)
        .unique()
        .tolist()
        if value and value != "<NA>"
    )
    return pd.DataFrame(
        {
            "room_type": room_types,
            "benchmark_area": [suggested_benchmark(value) for value in room_types],
            "source": ["Demo Default"] * len(room_types),
        }
    )


def mark_benchmark_sources(
    edited: pd.DataFrame, defaults: pd.DataFrame
) -> pd.DataFrame:
    """Mark edited demo values as session overrides without changing engine logic."""
    result = edited.copy()
    result["benchmark_area"] = pd.to_numeric(
        result.get("benchmark_area"), errors="coerce"
    )
    default_map = {
        str(row.room_type): float(row.benchmark_area)
        for row in defaults.itertuples(index=False)
    }
    result["source"] = [
        "Session Override"
        if pd.notna(area) and default_map.get(str(room_type)) != float(area)
        else "Demo Default"
        for room_type, area in zip(result["room_type"], result["benchmark_area"])
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
        str(row.room_type).strip().lower(): float(row.benchmark_area)
        for row in benchmark_df.itertuples(index=False)
        if pd.notna(row.room_type) and pd.notna(row.benchmark_area)
    }
    benchmark_source_map = {
        str(row.room_type).strip().lower(): str(getattr(row, "source", "Provided"))
        for row in benchmark_df.itertuples(index=False)
        if pd.notna(row.room_type)
    }
    room_type_key = work["room_type"].astype("string").str.strip().str.lower()
    work["benchmark_reference"] = room_type_key.map(benchmark_map)
    work["benchmark_source"] = room_type_key.map(benchmark_source_map)
    work["potential_area_released"] = (
        work["calculated_area"] - work["benchmark_reference"]
    )
    work["opportunity_score"] = np.where(
        work["potential_area_released"].ge(float(opportunity_threshold)).fillna(False),
        "High",
        "Low",
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

