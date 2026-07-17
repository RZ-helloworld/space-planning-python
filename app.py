from __future__ import annotations

import hashlib
import io
import json
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

from engine import (
    build_benchmark_table,
    generate_tasks,
    mark_benchmark_sources,
)
from space_programming_pipeline import build_canonical_inventory

st.set_page_config(page_title="Space Strategy Workbench", page_icon="🏗️", layout="wide")

REQUIRED_FIELDS: Dict[str, str] = {
    "room_code": "Room Code",
    "room_type": "Room Type",
    "calculated_area": "Calculated Area",
}

OPTIONAL_FIELDS: Dict[str, str] = {
    "floor_code": "Floor Code",
    "department": "Department",
    "building": "Building",
    "room_area": "Room Area",
    "percentage": "Percentage of Space",
    "occupancy": "Occupancy",
}

CANDIDATES: Dict[str, List[str]] = {
    "room_code": ["Room Code", "Room Number", "Room", "RM"],
    "room_type": ["Room Type", "Space Type", "Room Use", "Type"],
    "calculated_area": ["Calculated Area", "Area", "Net Area", "ASF"],
    "floor_code": ["Floor Code", "Floor", "Level"],
    "department": ["Department", "Dept", "Org", "Division"],
    "building": ["Building Code", "Building", "Bldg"],
    "room_area": ["Room Area", "Gross Room Area"],
    "percentage": ["Percentage of Space", "Percentage", "Pct"],
    "occupancy": ["Occupancy", "Headcount", "Occupants", "Seats"],
}

TASK_PAGE_SIZE = 20
NOT_PROVIDED = "-- Not Provided --"


def build_demo_raw_excel() -> pd.DataFrame:
    """Return a small workbook-shaped preview with its header on Excel row 3."""
    demo = pd.DataFrame(
        {
            "Room Code": ["03", "102", "201", "S-1"],
            "Floor Code": ["01", "01", "02", "B1"],
            "Room Type": ["Office", "Office", "Lab", "Support"],
            "Calculated Area": [60, 320, 400, 120],
            "Room Area": [100, 220, 400, 120],
            "Percentage": [60, 100, 100, 100],
            "Department": ["Strategy", "Strategy", "Research", "Operations"],
            "Building": ["Demo", "Demo", "Demo", "Demo"],
        }
    )
    blank = [np.nan] * len(demo.columns)
    return pd.DataFrame([blank, blank, list(demo.columns), *demo.values.tolist()])


def _first_non_empty_row(raw_df: pd.DataFrame) -> int:
    for index, row in raw_df.iterrows():
        non_empty = row.dropna().astype(str).str.strip()
        if (non_empty != "").any():
            return int(index)
    return 0


def _normalize_header_names(header_values: pd.Series) -> List[str]:
    names: List[str] = []
    seen: Dict[str, int] = {}
    for index, value in enumerate(header_values.fillna("").astype(str).str.strip()):
        base_name = value if value else f"Column_{index + 1}"
        seen[base_name] = seen.get(base_name, 0) + 1
        suffix = "" if seen[base_name] == 1 else f"_{seen[base_name]}"
        names.append(f"{base_name}{suffix}")
    return names


def parse_excel_with_header_row(
    raw_df: pd.DataFrame, header_row_index: int
) -> pd.DataFrame:
    if raw_df.empty:
        return pd.DataFrame()
    safe_index = min(max(header_row_index, 0), len(raw_df.index) - 1)
    parsed = raw_df.iloc[safe_index + 1 :].copy()
    parsed.columns = _normalize_header_names(raw_df.iloc[safe_index])
    parsed = parsed.dropna(axis=0, how="all").dropna(axis=1, how="all")
    return parsed.reset_index(drop=True)


def read_uploaded_excel(uploaded_file: object) -> Tuple[pd.DataFrame, int]:
    raw = pd.read_excel(io.BytesIO(uploaded_file.getvalue()), header=None)
    return raw, _first_non_empty_row(raw)


def smart_guess_column(columns: List[str], candidates: List[str]) -> Optional[str]:
    lowered = {str(column).strip().lower(): column for column in columns}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return str(lowered[candidate.lower()])
    for column in columns:
        if any(candidate.lower() in column.lower() for candidate in candidates):
            return column
    return None


def render_mapping_ui(df: pd.DataFrame, state_key: str) -> Dict[str, Optional[str]]:
    columns = [str(column) for column in df.columns]
    mapping: Dict[str, Optional[str]] = {}

    for logical, label in REQUIRED_FIELDS.items():
        guessed = smart_guess_column(columns, CANDIDATES[logical])
        default_index = columns.index(guessed) if guessed in columns else 0
        mapping[logical] = st.selectbox(
            f"Required · {label}",
            options=columns,
            index=default_index,
            key=f"{state_key}_required_{logical}",
        )

    for logical, label in OPTIONAL_FIELDS.items():
        guessed = smart_guess_column(columns, CANDIDATES[logical])
        options = [NOT_PROVIDED] + columns
        default_value = guessed if guessed in columns else NOT_PROVIDED
        selected = st.selectbox(
            f"Optional · {label}",
            options=options,
            index=options.index(default_value),
            key=f"{state_key}_optional_{logical}",
        )
        mapping[logical] = None if selected == NOT_PROVIDED else selected

    return mapping


def project_config(mapping: Dict[str, Optional[str]]) -> Dict[str, object]:
    columns = {key: value for key, value in mapping.items() if value}
    numeric_cols = [
        logical
        for logical in ["calculated_area", "room_area", "percentage", "occupancy"]
        if logical in columns
    ]
    return {
        "columns": columns,
        "numeric_cols": numeric_cols,
        "room_code_col": "room_code",
        "truth_area_col": "calculated_area",
        "id_components": [
            logical for logical in ["building", "floor_code"] if logical in columns
        ],
    }


def mapping_fingerprint(mapping: Dict[str, Optional[str]]) -> str:
    payload = json.dumps(mapping, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def benchmark_fingerprint(benchmark: pd.DataFrame) -> str:
    stable = benchmark[["room_type", "benchmark_area"]].copy()
    stable = stable.sort_values("room_type").reset_index(drop=True)
    payload = stable.to_json(orient="records", force_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_excel_report(
    tasks: pd.DataFrame, inventory: pd.DataFrame, benchmark: pd.DataFrame
) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        tasks.to_excel(writer, sheet_name="Strategy Tasks", index=False)
        inventory.to_excel(writer, sheet_name="Canonical Inventory", index=False)
        benchmark.to_excel(writer, sheet_name="Benchmark Inputs", index=False)
    return output.getvalue()


def render_filters(frame: pd.DataFrame) -> pd.DataFrame:
    filtered = frame.copy()
    with st.sidebar:
        st.header("Result Filters")
        for column, label in [("building", "Building"), ("department", "Department")]:
            if column not in filtered.columns:
                continue
            options = sorted(
                value
                for value in filtered[column].dropna().astype(str).unique().tolist()
                if value != "<NA>"
            )
            mode = st.selectbox(
                f"{label} filter",
                ["All", "Select"],
                key=f"filter_mode_{column}",
            )
            if mode == "Select":
                selected = st.multiselect(
                    label, options, default=options, key=f"filter_values_{column}"
                )
                filtered = filtered[filtered[column].astype(str).isin(selected)]
    return filtered


def main() -> None:
    st.title("Space Strategy Workbench")
    st.caption(
        "Upload → confirm mapping → review benchmarks → run Integrity & Opportunity."
    )
    st.info(
        "Preview mode: benchmark values are editable demo defaults. Uploaded data is "
        "processed in the current app session and is not written to this repository."
    )

    upload_column, demo_column = st.columns([3, 1])
    with upload_column:
        uploaded_file = st.file_uploader(
            "Upload an Excel room inventory", type=["xlsx", "xls"]
        )
    with demo_column:
        st.write("")
        st.write("")
        if st.button("Use demo inventory", use_container_width=True):
            st.session_state["use_demo_inventory"] = True

    if uploaded_file is not None:
        file_bytes = uploaded_file.getvalue()
        file_key = hashlib.sha256(file_bytes).hexdigest()[:16]
        try:
            raw_excel, auto_header_index = read_uploaded_excel(uploaded_file)
        except Exception as exc:
            st.error(f"Unable to read this workbook: {exc}")
            st.stop()
    elif st.session_state.get("use_demo_inventory"):
        raw_excel = build_demo_raw_excel()
        auto_header_index = _first_non_empty_row(raw_excel)
        file_key = "bundled-demo-v1"
        st.caption("Using a bundled synthetic inventory for interface preview.")
    else:
        st.caption("No workbook ready? Use the synthetic demo to preview the full flow.")
        st.stop()

    with st.expander("Step 1 · Confirm header", expanded=True):
        st.caption(f"Suggested header: Excel row {auto_header_index + 1}")
        manual = st.toggle("Choose a different header row", value=False)
        header_row = auto_header_index + 1
        if manual:
            header_row = int(
                st.number_input(
                    "Header row in Excel (1-based)",
                    min_value=1,
                    max_value=max(len(raw_excel), 1),
                    value=min(auto_header_index + 1, max(len(raw_excel), 1)),
                )
            )
        header_index = int(header_row - 1)
        parsed = parse_excel_with_header_row(raw_excel, header_index)
        st.write("Detected columns", list(parsed.columns))
        st.dataframe(parsed.head(5), use_container_width=True, hide_index=True)

        header_key = f"{file_key}:{header_index}"
        if st.button("Confirm header", type="primary"):
            st.session_state["confirmed_header"] = header_key
            st.session_state.pop("confirmed_mapping", None)
            st.session_state.pop("run_result", None)

    if st.session_state.get("confirmed_header") != header_key:
        st.warning("Confirm the header to continue.")
        st.stop()

    if parsed.empty or len(parsed.columns) == 0:
        st.error("No data columns were found below this header.")
        st.stop()

    with st.expander("Step 2 · Confirm column mapping", expanded=True):
        mapping = render_mapping_ui(parsed, header_key)
        current_mapping_key = f"{header_key}:{mapping_fingerprint(mapping)}"
        if st.button("Confirm mapping", type="primary"):
            st.session_state["confirmed_mapping"] = current_mapping_key
            st.session_state.pop("run_result", None)

    if st.session_state.get("confirmed_mapping") != current_mapping_key:
        st.warning("Confirm the required column mapping to continue.")
        st.stop()

    inventory = build_canonical_inventory(parsed, project_config(mapping))
    if inventory.empty:
        st.error("No rows with a usable Calculated Area were found.")
        st.stop()

    default_benchmark = build_benchmark_table(inventory)
    with st.expander("Step 3 · Review benchmark inputs", expanded=True):
        st.caption(
            "These are preview defaults. Edit any value to create a session override; "
            "the engine logic does not change."
        )
        edited_benchmark = st.data_editor(
            default_benchmark,
            use_container_width=True,
            hide_index=True,
            disabled=["room_type", "source"],
            column_config={
                "benchmark_area": st.column_config.NumberColumn(
                    "Benchmark Area (sqft)", min_value=0.0, format="%.1f"
                )
            },
            key=f"benchmark_editor_{current_mapping_key}",
        )
        benchmark = mark_benchmark_sources(edited_benchmark, default_benchmark)
        st.dataframe(benchmark, use_container_width=True, hide_index=True)

    with st.expander("Future inputs · interfaces reserved", expanded=False):
        st.write("Alignment: no input connected")
        st.write("Relocation difficulty: no input connected")
        st.caption("These optional inputs can be connected later without changing this flow.")

    run_key = (
        f"{current_mapping_key}:{benchmark_fingerprint(benchmark)}"
    )
    with st.expander("Step 4 · Run rating", expanded=True):
        if st.button("Run Integrity & Opportunity", type="primary"):
            st.session_state["run_result"] = generate_tasks(inventory, benchmark)
            st.session_state["run_fingerprint"] = run_key

    if st.session_state.get("run_fingerprint") != run_key:
        st.info("Run the rating engine. If an input changes, run it again.")
        st.stop()

    tasks = st.session_state.get("run_result", pd.DataFrame()).copy()
    filtered_inventory = render_filters(inventory)
    filtered_tasks = tasks.copy()
    if "__source_row" in filtered_inventory.columns and "__source_row" in tasks.columns:
        allowed_rows = set(filtered_inventory["__source_row"].tolist())
        filtered_tasks = tasks[tasks["__source_row"].isin(allowed_rows)].copy()

    metrics = st.columns(3)
    metrics[0].metric("Rooms in scope", f"{len(filtered_inventory):,}")
    metrics[1].metric("Tasks", f"{len(filtered_tasks):,}")
    potential = (
        filtered_tasks.get("potential_area_released", pd.Series(dtype=float))
        .clip(lower=0)
        .sum()
    )
    metrics[2].metric("Potential area", f"{potential:,.0f} sqft")

    left, right = st.columns([1.15, 1])
    with left:
        st.subheader("Canonical inventory")
        search = st.text_input("Search room, floor, department, or building")
        display_inventory = filtered_inventory.copy()
        if search:
            query = search.strip().lower()
            searchable = [
                column
                for column in ["room_code", "floor_code", "department", "building"]
                if column in display_inventory.columns
            ]
            mask = pd.Series(False, index=display_inventory.index)
            for column in searchable:
                mask |= display_inventory[column].astype(str).str.lower().str.contains(
                    query, na=False
                )
            display_inventory = display_inventory[mask]
        st.dataframe(display_inventory, use_container_width=True, hide_index=True)

    with right:
        st.subheader("Actionable tasks")
        if filtered_tasks.empty:
            st.success("No Integrity or Opportunity tasks under the current inputs.")
        else:
            notes = st.session_state.setdefault("task_notes", {})
            page_count = max(int(np.ceil(len(filtered_tasks) / TASK_PAGE_SIZE)), 1)
            page = int(
                st.number_input(
                    "Task page", min_value=1, max_value=page_count, value=1, step=1
                )
            )
            start = (page - 1) * TASK_PAGE_SIZE
            page_tasks = filtered_tasks.iloc[start : start + TASK_PAGE_SIZE]
            st.caption(
                f"Showing {start + 1}-{start + len(page_tasks)} of {len(filtered_tasks)}"
            )

            for _, row in page_tasks.iterrows():
                task_id = str(row["__task_id"])
                with st.container(border=True):
                    st.markdown(
                        f"**{row['action']} · Room {row.get('room_code', '')}**  \n"
                        f"Integrity: **{row['integrity_score']}** · "
                        f"Opportunity: **{row['opportunity_score']}** · "
                        f"Potential: **{row['potential_area_released']:.1f} sqft**"
                    )
                    st.caption(
                        f"Integrity evidence: {row.get('integrity_source', 'Unavailable')} · "
                        f"Benchmark: {row.get('benchmark_source', 'Unavailable')}"
                    )
                    notes[task_id] = st.text_input(
                        "Architectural notes",
                        value=notes.get(task_id, ""),
                        key=f"note_{task_id}",
                    )

            export_tasks = filtered_tasks.copy()
            export_tasks["architectural_notes"] = [
                notes.get(str(task_id), "") for task_id in export_tasks["__task_id"]
            ]
            st.download_button(
                "Download task workbook",
                data=build_excel_report(export_tasks, filtered_inventory, benchmark),
                file_name="Space_Strategy_Task_List.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

    st.subheader("Potential area by department")
    if "department" in filtered_tasks.columns and not filtered_tasks.empty:
        chart = (
            filtered_tasks.groupby("department", dropna=False)["potential_area_released"]
            .sum()
            .clip(lower=0)
            .sort_values(ascending=False)
            .rename_axis("department")
            .reset_index()
        )
        st.bar_chart(chart, x="department", y="potential_area_released")
    else:
        st.caption("Department data is not available for this chart.")


if __name__ == "__main__":
    main()

