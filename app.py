"""
Space Strategy Workbench
========================
A Streamlit app that treats architectural space as inventory (WMS logic).
Upload a space list (Excel), tune strategy parameters with sliders, and
instantly generate a prioritized "Actionable Task List" for master planning.

Run:
    streamlit run app.py

Architecture (modular — logic decoupled from UI):
    1. Data Ingestor   -> load_uploaded_file / clean_data / resolve_columns
    2. Task Engine     -> generate_tasks / calculate_task_priority
    3. Dashboard UI    -> main()
    4. Export          -> build_excel_report
"""

from __future__ import annotations

import io
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import streamlit as st

# Reuse cleaning helpers from the existing pipeline module when available.
try:
    from space_programming_pipeline import _strip_dataframe_strings
except ImportError:  # fallback so app.py also works standalone
    def _strip_dataframe_strings(df: pd.DataFrame) -> pd.DataFrame:
        cleaned = df.copy()
        cleaned.columns = [str(col).strip() for col in cleaned.columns]
        object_cols = cleaned.select_dtypes(include=["object", "string"]).columns
        for col in object_cols:
            cleaned[col] = cleaned[col].map(
                lambda v: v.strip() if isinstance(v, str) else v
            )
        return cleaned


# ---------------------------------------------------------------------------
# 0. Constants
# ---------------------------------------------------------------------------

LOGICAL_FIELDS: Dict[str, dict] = {
    # logical name -> {label, candidates (auto-detect), required}
    "room_code": {
        "label": "Room Code",
        "candidates": ["Room Code", "Room Number", "Room", "RM"],
        "required": True,
    },
    "room_type": {
        "label": "Room Type",
        "candidates": ["Room Type", "Space Type", "Room Use", "Type"],
        "required": True,
    },
    "calculated_area": {
        "label": "Calculated Area (sqft)",
        "candidates": ["Calculated Area", "Area", "Net Area", "ASF"],
        "required": True,
    },
    "building": {
        "label": "Building",
        "candidates": ["Building Code", "Building", "Bldg"],
        "required": False,
    },
    "department": {
        "label": "Department",
        "candidates": ["Department", "Dept", "Org", "Division"],
        "required": False,
    },
    "room_area": {
        "label": "Room Area (gross)",
        "candidates": ["Room Area"],
        "required": False,
    },
    "percentage": {
        "label": "Percentage of Space",
        "candidates": ["Percentage of Space", "Percentage", "Pct"],
        "required": False,
    },
    "occupancy": {
        "label": "Occupancy (headcount)",
        "candidates": ["Occupancy", "Headcount", "Occupants", "Seats"],
        "required": False,
    },
}

NOT_MAPPED = "— not mapped —"


# ---------------------------------------------------------------------------
# 1. Data Ingestor (The Gatekeeper)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner="Reading Excel file...")
def load_uploaded_file(file_bytes: bytes, sheet_name: str, header_row: int) -> pd.DataFrame:
    """Read an uploaded Excel file into a raw DataFrame."""
    return pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet_name, header=header_row)


def auto_detect_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """Case-insensitive auto-detection of a physical column name."""
    lookup = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lookup:
            return lookup[cand.lower()]
    return None


def resolve_columns(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    """Auto-map logical fields; fall back to manual selectboxes when unresolved."""
    mapping: Dict[str, Optional[str]] = {}
    unresolved: List[str] = []

    for logical, spec in LOGICAL_FIELDS.items():
        found = auto_detect_column(df, spec["candidates"])
        mapping[logical] = found
        if found is None and spec["required"]:
            unresolved.append(logical)

    if unresolved:
        st.warning(
            "Some required columns could not be auto-detected. "
            "Please map them manually below."
        )

    with st.expander("Column mapping", expanded=bool(unresolved)):
        options = [NOT_MAPPED] + list(df.columns.astype(str))
        for logical, spec in LOGICAL_FIELDS.items():
            current = mapping[logical]
            index = options.index(current) if current in options else 0
            chosen = st.selectbox(
                f"{spec['label']}" + (" *" if spec["required"] else ""),
                options,
                index=index,
                key=f"map_{logical}",
            )
            mapping[logical] = None if chosen == NOT_MAPPED else chosen

    return mapping


def clean_data(df_raw: pd.DataFrame, mapping: Dict[str, Optional[str]]) -> pd.DataFrame:
    """Strip whitespace, coerce numerics, normalize key fields into logical columns."""
    df = _strip_dataframe_strings(df_raw)

    # Rename mapped physical columns to stable logical names.
    # Headers were whitespace-stripped above, so strip the mapped keys too.
    rename = {str(phys).strip(): logical for logical, phys in mapping.items() if phys}
    df = df.rename(columns=rename)

    for col in ["calculated_area", "room_area", "percentage", "occupancy"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "room_code" in df.columns:
        df["room_code"] = df["room_code"].astype("string").str.strip()

    # Drop rows with no usable area — they cannot participate in the strategy.
    if "calculated_area" in df.columns:
        df = df[df["calculated_area"].notna()]

    return df.reset_index(drop=True)


def compute_area_mismatch(df: pd.DataFrame) -> pd.Series:
    """|Calculated Area − Room Area × Percentage| (data-integrity audit)."""
    if not {"room_area", "percentage"}.issubset(df.columns):
        return pd.Series(0.0, index=df.index)

    pct = df["percentage"]
    normalized_pct = np.where(pct.abs() > 1, pct / 100.0, pct)
    expected = df["room_area"] * normalized_pct
    return (df["calculated_area"] - expected).abs().fillna(0.0)


# ---------------------------------------------------------------------------
# 2. Task Engine (WMS "Slotting Optimization" logic)
# ---------------------------------------------------------------------------

def calculate_task_priority(row: pd.Series) -> str:
    """Three-dimension priority score: integrity, opportunity, occupancy state."""
    score = 0
    # Dimension 1: data integrity (must-fix) — highest weight
    if row["area_mismatch"] > 50:
        score += 50
    # Dimension 2: subdivision/reallocation gain (quick win)
    if row["potential_gain"] > 100:
        score += 30
    # Dimension 3: utilization state
    if row.get("is_unoccupied", False):
        score += 20

    if score >= 80:
        return "🔥 Urgent (Immediate Audit)"
    if score >= 50:
        return "⚡ High (Subdivision Candidate)"
    return "📅 Planning (Future Restack)"


def generate_tasks(
    df: pd.DataFrame,
    area_threshold: float,
    target_density: float,
) -> pd.DataFrame:
    """Apply strategy rules row-by-row and emit an actionable task list."""
    work = df.copy()
    work["area_mismatch"] = compute_area_mismatch(work)

    has_occupancy = "occupancy" in work.columns
    work["is_unoccupied"] = work["occupancy"].fillna(0).eq(0) if has_occupancy else False

    room_type = (
        work["room_type"].astype("string").str.lower()
        if "room_type" in work.columns
        else pd.Series("", index=work.index, dtype="string")
    )
    area = work["calculated_area"]

    # Rule 1 — SUBDIVIDE: large offices above the threshold.
    is_office = room_type.str.contains("office", na=False)
    subdivide_mask = is_office & (area > area_threshold)

    # Rule 2 — REALLOCATE: unoccupied rooms, or tiny dead spaces (< 50 sqft).
    reallocate_mask = (work["is_unoccupied"] & has_occupancy) | (area < 50)
    reallocate_mask &= ~subdivide_mask  # one action per room

    # Rule 3 — AUDIT: significant data mismatch (> 50 sqft) blocks planning.
    audit_mask = (work["area_mismatch"] > 50) & ~subdivide_mask & ~reallocate_mask

    tasks: List[pd.DataFrame] = []

    def _mk(mask: pd.Series, action: str, gain: pd.Series) -> None:
        if not mask.any():
            return
        sub = work.loc[mask].copy()
        sub["action"] = action
        sub["potential_gain"] = gain.loc[mask].clip(lower=0).round(0)
        tasks.append(sub)

    occupants = (
        work["occupancy"].clip(lower=1) if has_occupancy
        else pd.Series(1.0, index=work.index)
    )
    _mk(subdivide_mask, "SUBDIVIDE", area - target_density * occupants)
    _mk(reallocate_mask, "REALLOCATE", area)
    _mk(audit_mask, "AUDIT DATA", pd.Series(0.0, index=work.index))

    if not tasks:
        return pd.DataFrame(
            columns=["room_code", "action", "potential_gain", "priority", "notes"]
        )

    result = pd.concat(tasks, ignore_index=True)
    result["priority"] = result.apply(calculate_task_priority, axis=1)
    result["priority_rank"] = result["priority"].map(
        {"🔥 Urgent (Immediate Audit)": 0,
         "⚡ High (Subdivision Candidate)": 1,
         "📅 Planning (Future Restack)": 2}
    )
    result = result.sort_values(
        ["priority_rank", "potential_gain"], ascending=[True, False]
    ).reset_index(drop=True)
    result["notes"] = ""

    display_cols = [
        c for c in [
            "room_code", "building", "department", "room_type",
            "calculated_area", "action", "potential_gain",
            "area_mismatch", "priority", "notes",
        ] if c in result.columns
    ]
    return result[display_cols]


# ---------------------------------------------------------------------------
# 3. Export
# ---------------------------------------------------------------------------

def build_excel_report(tasks: pd.DataFrame, inventory: pd.DataFrame) -> bytes:
    """Package tasks (with user notes) + cleaned inventory into one Excel file."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        tasks.to_excel(writer, sheet_name="Strategy Task List", index=False)
        inventory.to_excel(writer, sheet_name="Cleaned Inventory", index=False)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 4. Dashboard UI
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="Space Strategy Workbench",
        page_icon="🏗️",
        layout="wide",
    )
    st.title("🏗️ Space Strategy Workbench")
    st.caption(
        "Space-as-Inventory: upload a space list, tune the strategy knobs, "
        "and get an actionable task list — WMS logic for master planning."
    )

    # --- Sidebar: ingest + strategy sandbox -------------------------------
    with st.sidebar:
        st.header("1️⃣ Data Ingestor")
        uploaded = st.file_uploader("Upload space list (Excel)", type=["xlsx", "xls"])

        sheet_name = st.text_input("Sheet name", value="Rooms Pct")
        header_row = st.number_input(
            "Header row (0-indexed)", min_value=0, max_value=20, value=2,
            help="UPitt space lists start headers on row 3 → enter 2.",
        )

        st.header("2️⃣ Strategy Sandbox")
        area_threshold = st.slider(
            "Area Threshold (sqft)", 150, 500, 250, step=10,
            help="Offices larger than this are subdivision candidates.",
        )
        target_density = st.slider(
            "Target Density (sqft / person)", 80, 200, 120, step=5,
            help="Ideal allocation per occupant.",
        )

    if uploaded is None:
        st.info("⬅️ Upload an Excel space list to begin. No data ever leaves your browser session.")
        st.stop()

    # --- Load + clean ------------------------------------------------------
    try:
        df_raw = load_uploaded_file(uploaded.getvalue(), sheet_name, int(header_row))
    except ValueError as exc:
        st.error(
            f"Could not read sheet '{sheet_name}': {exc}\n\n"
            "Check the sheet name and header row in the sidebar."
        )
        st.stop()

    mapping = resolve_columns(df_raw)
    missing_required = [
        spec["label"]
        for k, spec in LOGICAL_FIELDS.items()
        if spec["required"] and mapping.get(k) is None
    ]
    if missing_required:
        st.error("Required columns not mapped: " + ", ".join(missing_required))
        st.stop()

    df = clean_data(df_raw, mapping)

    # --- Sidebar filters (need data to populate options) -------------------
    with st.sidebar:
        st.header("3️⃣ Priority Filters")
        if "building" in df.columns:
            buildings = sorted(df["building"].dropna().astype(str).unique())
            picked_b = st.multiselect("Buildings", buildings, default=[])
            if picked_b:
                df = df[df["building"].astype(str).isin(picked_b)]
        if "department" in df.columns:
            depts = sorted(df["department"].dropna().astype(str).unique())
            picked_d = st.multiselect("Departments", depts, default=[])
            if picked_d:
                df = df[df["department"].astype(str).isin(picked_d)]

    # --- Task engine (reactive: reruns on every slider move) ---------------
    tasks = generate_tasks(df, area_threshold, target_density)

    # --- Top metrics --------------------------------------------------------
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Potential Area Gains", f"{tasks['potential_gain'].sum():,.0f} sqft"
              if not tasks.empty else "0 sqft")
    m2.metric("Tasks Identified", f"{len(tasks):,}")
    m3.metric("Rooms in Scope", f"{len(df):,}")

    # --- Split view ---------------------------------------------------------
    left, right = st.columns([1, 1])

    with left:
        st.subheader("📦 Inventory View")
        st.caption("Cleaned, filtered room list — searchable and sortable.")
        st.dataframe(df, use_container_width=True, height=430)

        if "department" in tasks.columns and not tasks.empty:
            st.subheader("📊 Potential Gains by Department")
            gains = (
                tasks.groupby("department")["potential_gain"]
                .sum()
                .sort_values(ascending=False)
                .head(15)
            )
            st.bar_chart(gains)

    with right:
        st.subheader("🚚 Actionable Tasks")
        st.caption(
            "Sorted by priority. Add Architectural Notes directly in the "
            "table — they are included in the export."
        )
        if tasks.empty:
            st.success("No optimization tasks under the current strategy. Try lowering the Area Threshold.")
        else:
            edited_tasks = st.data_editor(
                tasks,
                use_container_width=True,
                height=430,
                disabled=[c for c in tasks.columns if c != "notes"],
                column_config={
                    "notes": st.column_config.TextColumn(
                        "Architectural Notes", width="large"
                    ),
                    "potential_gain": st.column_config.NumberColumn(
                        "Potential Gain (sqft)", format="%.0f"
                    ),
                    "calculated_area": st.column_config.NumberColumn(
                        "Area (sqft)", format="%.0f"
                    ),
                    "area_mismatch": st.column_config.NumberColumn(
                        "Mismatch (sqft)", format="%.1f"
                    ),
                },
                key="task_editor",
            )

            st.download_button(
                "⬇️ Download Final Strategy Task List (Excel)",
                data=build_excel_report(edited_tasks, df),
                file_name="Space_Strategy_Task_List.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )


if __name__ == "__main__":
    main()
