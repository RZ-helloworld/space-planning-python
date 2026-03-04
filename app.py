from __future__ import annotations

from io import BytesIO
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Space Strategy Workbench", layout="wide")

REQUIRED_FIELDS: Dict[str, str] = {
    "room_code": "Room Code",
    "floor_code": "Floor Code",
    "room_type": "Room Type",
    "calculated_area": "Calculated Area",
}

OPTIONAL_FIELDS: Dict[str, str] = {
    "department": "Department",
    "building": "Building",
    "room_area": "Room Area",
}

DEFAULT_ROOM_TYPE_BENCHMARKS: Dict[str, float] = {
    "office": 240.0,
    "lab": 300.0,
    "conference": 180.0,
    "classroom": 350.0,
    "support": 120.0,
}

TASK_PAGE_SIZE = 20
INTEGRITY_GAP_THRESHOLD = 25.0
OPPORTUNITY_HIGH_THRESHOLD = 50.0


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    cleaned = df.copy()
    cleaned.columns = [str(col).strip() for col in cleaned.columns]

    object_cols = cleaned.select_dtypes(include=["object", "string"]).columns
    for col in object_cols:
        cleaned[col] = cleaned[col].map(lambda v: v.strip() if isinstance(v, str) else v)

    for numeric_col in ["Calculated Area", "Percentage of Space", "Room Area"]:
        if numeric_col in cleaned.columns:
            cleaned[numeric_col] = pd.to_numeric(cleaned[numeric_col], errors="coerce")

    return cleaned


def _first_non_empty_row(raw_df: pd.DataFrame) -> int:
    for idx, row in raw_df.iterrows():
        non_empty = row.dropna().astype(str).str.strip()
        if (non_empty != "").any():
            return int(idx)
    return 0


def _normalize_header_names(header_values: pd.Series) -> List[str]:
    names: List[str] = []
    seen: Dict[str, int] = {}

    for i, value in enumerate(header_values.fillna("").astype(str).str.strip()):
        base_name = value if value else f"Column_{i + 1}"
        if base_name in seen:
            seen[base_name] += 1
            names.append(f"{base_name}_{seen[base_name]}")
        else:
            seen[base_name] = 1
            names.append(base_name)

    return names


def parse_excel_with_header_row(raw_df: pd.DataFrame, header_row_idx: int) -> pd.DataFrame:
    if raw_df.empty:
        return pd.DataFrame()

    safe_idx = min(max(header_row_idx, 0), len(raw_df.index) - 1)
    col_names = _normalize_header_names(raw_df.iloc[safe_idx])

    parsed = raw_df.iloc[safe_idx + 1 :].copy()
    parsed.columns = col_names
    parsed = parsed.dropna(axis=0, how="all").dropna(axis=1, how="all")
    return parsed.reset_index(drop=True)


def load_excel_with_auto_header(uploaded_file: object) -> Tuple[pd.DataFrame, int, pd.DataFrame]:
    raw_df = pd.read_excel(BytesIO(uploaded_file.getvalue()), header=None)
    header_idx = _first_non_empty_row(raw_df)
    parsed_df = parse_excel_with_header_row(raw_df, header_idx)
    return parsed_df, header_idx, raw_df


def smart_guess_column(columns: List[str], candidates: List[str]) -> Optional[str]:
    lowered = {c.lower(): c for c in columns}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]

    for col in columns:
        for candidate in candidates:
            if candidate.lower() in col.lower():
                return col
    return None


def render_mapping_ui(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    st.subheader("Step 2 · Column Mapping")
    st.caption("Map uploaded columns to the analysis schema.")

    show_advanced_cols = st.checkbox("Show advanced columns (incl. Unnamed)", value=False)
    all_cols = list(df.columns)
    mapped_cols = [c for c in all_cols if not str(c).lower().startswith("unnamed:")]
    if show_advanced_cols or not mapped_cols:
        mapped_cols = all_cols

    mapping: Dict[str, Optional[str]] = {}
    none_option = "-- Not Provided --"

    for key, label in REQUIRED_FIELDS.items():
        guessed = smart_guess_column(mapped_cols, [label])
        default_idx = mapped_cols.index(guessed) if guessed in mapped_cols else 0
        mapping[key] = st.selectbox(
            f"Required: {label}",
            options=mapped_cols,
            index=default_idx,
            key=f"map_req_{key}",
        )

    for key, label in OPTIONAL_FIELDS.items():
        guessed = smart_guess_column(mapped_cols, [label])
        options = [none_option] + mapped_cols
        default_value = guessed if guessed in mapped_cols else none_option
        selected = st.selectbox(
            f"Optional: {label}",
            options=options,
            index=options.index(default_value),
            key=f"map_opt_{key}",
        )
        mapping[key] = None if selected == none_option else selected

    return mapping


def build_working_df(df: pd.DataFrame, mapping: Dict[str, Optional[str]]) -> pd.DataFrame:
    working = pd.DataFrame(index=df.index)

    for key in REQUIRED_FIELDS:
        source_col = mapping.get(key)
        working[key] = df[source_col] if source_col in df.columns else np.nan

    for key in OPTIONAL_FIELDS:
        source_col = mapping.get(key)
        working[key] = df[source_col] if source_col in df.columns else np.nan

    for text_col in ["room_code", "floor_code", "room_type", "department", "building"]:
        working[text_col] = working[text_col].astype("string")

    working["calculated_area"] = pd.to_numeric(working["calculated_area"], errors="coerce")
    working["room_area"] = pd.to_numeric(working["room_area"], errors="coerce")

    return working


def required_fields_ready(working_df: pd.DataFrame) -> Tuple[bool, List[str]]:
    issues: List[str] = []

    for field_key, field_label in REQUIRED_FIELDS.items():
        if field_key not in working_df.columns:
            issues.append(f"Missing required mapped column: {field_label}")
            continue

        has_values = working_df[field_key].notna().any()
        if field_key in {"room_code", "floor_code", "room_type"}:
            text = working_df[field_key].astype("string").str.strip().replace("<NA>", pd.NA)
            has_values = text.notna().any()

        if not has_values:
            issues.append(f"Required field has no usable values: {field_label}")

    return len(issues) == 0, issues


def build_benchmark_table(working_df: pd.DataFrame) -> pd.DataFrame:
    room_types = sorted([rt for rt in working_df["room_type"].dropna().astype(str).unique().tolist() if rt != "<NA>"])
    rows: List[Dict[str, object]] = []
    for room_type in room_types:
        key = room_type.strip().lower()
        rows.append({
            "room_type": room_type,
            "benchmark_area": DEFAULT_ROOM_TYPE_BENCHMARKS.get(key, 240.0),
        })
    return pd.DataFrame(rows)


def generate_tasks(working_df: pd.DataFrame, target_density: int, benchmark_df: pd.DataFrame) -> pd.DataFrame:
    df = working_df.copy()

    benchmark_map = {
        str(row.room_type).strip().lower(): float(row.benchmark_area)
        for row in benchmark_df.itertuples(index=False)
        if pd.notna(row.room_type) and pd.notna(row.benchmark_area)
    }

    df["benchmark_reference"] = (
        df["room_type"].astype(str).str.strip().str.lower().map(benchmark_map)
    )

    # Integrity score: auditing gap between calculated and physical area.
    df["integrity_gap"] = (df["calculated_area"] - df["room_area"]).abs()
    has_physical_area = df["room_area"].notna().to_numpy(dtype=bool)
    critical_integrity = (df["integrity_gap"] > INTEGRITY_GAP_THRESHOLD).fillna(False).to_numpy(dtype=bool)

    df["integrity_score"] = np.select(
        [has_physical_area & critical_integrity, has_physical_area],
        ["Critical", "Normal"],
        default="Unknown",
    )

    # Opportunity score: current area minus FICM-like room-type benchmark.
    df["potential_area_released"] = (df["calculated_area"] - df["benchmark_reference"]).fillna(0)
    high_opportunity = (df["potential_area_released"] >= OPPORTUNITY_HIGH_THRESHOLD).fillna(False).to_numpy(dtype=bool)

    df["opportunity_score"] = np.select(
        [high_opportunity],
        ["High"],
        default="Low",
    )

    # Reserved slots for future strategic modules.
    df["strategic_alignment_score"] = pd.Series([pd.NA] * len(df), dtype="string")
    df["relocation_difficulty_score"] = pd.Series([pd.NA] * len(df), dtype="string")

    actionable_mask = (df["integrity_score"] == "Critical") | (df["opportunity_score"] == "High")
    tasks = df[actionable_mask].copy()

    tasks["action"] = np.select(
        [
            (tasks["integrity_score"] == "Critical").to_numpy(dtype=bool),
            (tasks["opportunity_score"] == "High").to_numpy(dtype=bool),
        ],
        ["Audit Data", "Capture Opportunity"],
        default="Review",
    )

    tasks["target_density_input"] = float(target_density)

    return tasks[
        [
            "room_code",
            "floor_code",
            "room_type",
            "department",
            "building",
            "calculated_area",
            "room_area",
            "integrity_gap",
            "integrity_score",
            "benchmark_reference",
            "potential_area_released",
            "opportunity_score",
            "strategic_alignment_score",
            "relocation_difficulty_score",
            "action",
            "target_density_input",
        ]
    ].reset_index(drop=True)


def tasks_to_excel_bytes(df_tasks: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_tasks.to_excel(writer, index=False, sheet_name="strategy_tasks")
    return output.getvalue()


def render_filter_sidebar(working_df: pd.DataFrame) -> pd.DataFrame:
    dept_options = sorted([d for d in working_df["department"].dropna().unique().tolist() if str(d) != "<NA>"])
    bldg_options = sorted([b for b in working_df["building"].dropna().unique().tolist() if str(b) != "<NA>"])

    with st.sidebar:
        st.subheader("Filter Mode")

        dept_mode = st.selectbox("Department Filter Mode", ["All Departments", "Select Departments"], index=0)
        selected_depts = (
            dept_options
            if dept_mode == "All Departments"
            else st.multiselect("Department Filter (Multi-select)", dept_options, default=dept_options)
        )

        bldg_mode = st.selectbox("Building Filter Mode", ["All Buildings", "Select Buildings"], index=0)
        selected_bldgs = (
            bldg_options
            if bldg_mode == "All Buildings"
            else st.multiselect("Building Filter (Multi-select)", bldg_options, default=bldg_options)
        )

    scoped = working_df.copy()
    if dept_mode == "Select Departments":
        scoped = scoped[scoped["department"].isin(selected_depts)]
    if bldg_mode == "Select Buildings":
        scoped = scoped[scoped["building"].isin(selected_bldgs)]

    return scoped


def main() -> None:
    st.title("Space Strategy Workbench")
    st.caption("WMS-style operational task generator for campus space strategy.")

    uploaded_file = st.file_uploader("Upload Excel inventory", type=["xlsx", "xls"])
    if uploaded_file is None:
        st.info("Upload a file to begin scenario analysis.")
        return

    st.session_state.setdefault("header_confirmed", None)
    st.session_state.setdefault("mapping_confirmed", None)
    st.session_state.setdefault("engine_confirmed", None)

    file_key = f"{uploaded_file.name}_{uploaded_file.size}"

    try:
        _, auto_header_idx, raw_excel_df = load_excel_with_auto_header(uploaded_file)
    except Exception as exc:
        st.error(f"Failed to read Excel file: {exc}")
        return

    with st.expander("Step 1 · Header Alignment", expanded=True):
        st.caption(f"Auto-detected header row: Excel row {auto_header_idx + 1}.")
        manual_override = st.toggle("Manually override header row", value=False)

        selected_row_1based = auto_header_idx + 1
        if manual_override:
            selected_row_1based = st.number_input(
                "Header row number in Excel (1-based)",
                min_value=1,
                max_value=max(len(raw_excel_df.index), 1),
                value=min(auto_header_idx + 1, max(len(raw_excel_df.index), 1)),
                step=1,
            )

        header_idx = int(selected_row_1based - 1)
        raw_df = parse_excel_with_header_row(raw_excel_df, header_idx)

        st.write("Detected columns:", list(raw_df.columns))
        st.dataframe(raw_df.head(5), use_container_width=True, hide_index=True)

        if st.button("Confirm header alignment", type="primary"):
            st.session_state["header_confirmed"] = f"{file_key}_{header_idx}"
            st.session_state["mapping_confirmed"] = None
            st.session_state["engine_confirmed"] = None

    header_key = f"{file_key}_{header_idx}"
    if st.session_state.get("header_confirmed") != header_key:
        st.warning("Please confirm header alignment to continue.")
        return

    clean_df = clean_dataframe(raw_df)

    with st.expander("Step 2 · Column Mapping & Required Field Check", expanded=True):
        mapping = render_mapping_ui(clean_df)
        working_df = build_working_df(clean_df, mapping)

        ready, issues = required_fields_ready(working_df)
        if ready:
            st.success("Required fields are mapped and usable.")
        else:
            for issue in issues:
                st.error(issue)

        if st.button("Confirm mapping and unlock strategy step", type="primary", disabled=not ready):
            st.session_state["mapping_confirmed"] = header_key
            st.session_state["engine_confirmed"] = None

    if st.session_state.get("mapping_confirmed") != header_key:
        st.warning("Please complete Step 2 before running strategy and tasks.")
        return

    working_df = build_working_df(clean_df, mapping)

    with st.sidebar:
        st.header("Strategy Sandbox")
        target_density = st.slider("Target Density (sqft/person)", 80, 200, 120)

    scoped = render_filter_sidebar(working_df)

    with st.expander("Step 3 · Configure Benchmarks", expanded=False):
        st.caption("Review/edit room-type benchmark area (FICM-like references).")
        benchmark_default = build_benchmark_table(scoped)
        benchmark_df = st.data_editor(
            benchmark_default,
            use_container_width=True,
            num_rows="dynamic",
            key="benchmark_editor",
        )

    with st.expander("Step 4 · Run Rating Engine", expanded=True):
        st.caption("Actionable tasks are generated only after this step is run.")
        if st.button("Run rating engine", type="primary"):
            st.session_state["engine_confirmed"] = header_key

    if st.session_state.get("engine_confirmed") != header_key:
        st.info("Run Step 4 to generate actionable tasks.")
        return

    tasks = generate_tasks(scoped, target_density, benchmark_df)

    total_released = float(tasks["potential_area_released"].clip(lower=0).sum()) if not tasks.empty else 0.0
    st_cols = st.columns(2)
    st_cols[0].metric("Total Potential Area Gains (sqft)", f"{total_released:,.0f}")
    st_cols[1].metric("Total Tasks Identified", f"{len(tasks)}")

    left, right = st.columns([1.2, 1])

    with left:
        st.subheader("Inventory View")
        search = st.text_input("Search inventory (room/floor/department/building)")
        display_df = scoped.copy()
        if search:
            q = search.lower().strip()
            mask = (
                display_df["room_code"].astype(str).str.lower().str.contains(q, na=False)
                | display_df["floor_code"].astype(str).str.lower().str.contains(q, na=False)
                | display_df["department"].astype(str).str.lower().str.contains(q, na=False)
                | display_df["building"].astype(str).str.lower().str.contains(q, na=False)
            )
            display_df = display_df[mask]
        st.dataframe(display_df, use_container_width=True, hide_index=True)

    with right:
        st.subheader("Actionable Tasks")
        if tasks.empty:
            st.success("No tasks identified for current rating setup.")
        else:
            notes_store = st.session_state.setdefault("task_notes", {})
            total_pages = max(int(np.ceil(len(tasks) / TASK_PAGE_SIZE)), 1)
            page = int(st.number_input("Task page", min_value=1, max_value=total_pages, value=1, step=1))
            start_idx = (page - 1) * TASK_PAGE_SIZE
            end_idx = min(start_idx + TASK_PAGE_SIZE, len(tasks))
            page_df = tasks.iloc[start_idx:end_idx]

            st.caption(
                f"Showing tasks {start_idx + 1}-{end_idx} of {len(tasks)} "
                f"(page {page}/{total_pages}, {TASK_PAGE_SIZE} per page)."
            )

            for idx, row in page_df.iterrows():
                task_id = f"{row['room_code']}_{idx}_{row['action']}"
                with st.container(border=True):
                    st.markdown(
                        f"**[{row['room_code']}]** | **Floor: {row['floor_code']}** | **Action: {row['action']}**  \n"
                        f"Integrity: **{row['integrity_score']}** | Opportunity: **{row['opportunity_score']}** | "
                        f"Potential: **{row['potential_area_released']:.1f} sqft**"
                    )
                    notes_store[task_id] = st.text_input(
                        "Architectural Notes",
                        value=notes_store.get(task_id, ""),
                        key=f"note_{task_id}",
                    )

            tasks_export = tasks.copy()
            tasks_export["architectural_notes"] = [
                notes_store.get(f"{r.room_code}_{i}_{r.action}", "") for i, r in tasks.iterrows()
            ]

            st.download_button(
                "Download Final Strategy Task List (Excel)",
                data=tasks_to_excel_bytes(tasks_export),
                file_name="Final_Strategy_Task_List.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

    st.subheader("Potential Area Gains by Department")
    if tasks.empty:
        st.info("No chart data available until tasks are identified.")
    else:
        chart_df = (
            tasks.groupby("department", dropna=False)["potential_area_released"]
            .sum()
            .rename("potential_gain")
            .reset_index()
            .set_index("department")
        )
        st.bar_chart(chart_df)


if __name__ == "__main__":
    main()
