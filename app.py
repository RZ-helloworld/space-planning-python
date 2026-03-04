from __future__ import annotations

from io import BytesIO
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Space Strategy Workbench", layout="wide")

REQUIRED_FIELDS = {
    "room_code": "Room Code",
    "floor_code": "Floor Code",
    "room_type": "Room Type",
    "calculated_area": "Calculated Area",
}
OPTIONAL_FIELDS = {
    "department": "Department",
    "building": "Building",
    "occupancy": "Occupancy",
    "net_area": "Net Area",
}
TASK_PAGE_SIZE = 20


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    cleaned = df.copy()
    cleaned.columns = [str(c).strip() for c in cleaned.columns]

    object_cols = cleaned.select_dtypes(include=["object", "string"]).columns
    for col in object_cols:
        cleaned[col] = cleaned[col].map(lambda v: v.strip() if isinstance(v, str) else v)

    for numeric_col in ["Calculated Area", "Percentage of Space"]:
        if numeric_col in cleaned.columns:
            cleaned[numeric_col] = pd.to_numeric(cleaned[numeric_col], errors="coerce")

    return cleaned


def _first_non_empty_row(raw_df: pd.DataFrame) -> int:
    for idx, row in raw_df.iterrows():
        non_empty = row.dropna().astype(str).str.strip()
        if (non_empty != "").any():
            return int(idx)
    return 0


def _build_column_names(header_values: pd.Series) -> List[str]:
    column_names: List[str] = []
    seen: Dict[str, int] = {}

    for i, header_val in enumerate(header_values.fillna("").astype(str).str.strip()):
        base_name = header_val if header_val else f"Column_{i+1}"
        if base_name in seen:
            seen[base_name] += 1
            final_name = f"{base_name}_{seen[base_name]}"
        else:
            seen[base_name] = 1
            final_name = base_name
        column_names.append(final_name)

    return column_names


def parse_excel_with_header_row(raw_df: pd.DataFrame, header_row_idx: int) -> pd.DataFrame:
    if raw_df.empty:
        return pd.DataFrame()

    max_idx = len(raw_df.index) - 1
    safe_header_idx = min(max(header_row_idx, 0), max_idx)

    header_values = raw_df.iloc[safe_header_idx]
    column_names = _build_column_names(header_values)

    data_df = raw_df.iloc[safe_header_idx + 1 :].copy()
    data_df.columns = column_names
    data_df = data_df.dropna(axis=0, how="all").dropna(axis=1, how="all")
    return data_df.reset_index(drop=True)


def load_excel_with_auto_header(uploaded_file: object) -> Tuple[pd.DataFrame, int, pd.DataFrame]:
    file_bytes = uploaded_file.getvalue()
    raw_df = pd.read_excel(BytesIO(file_bytes), header=None)
    header_row_idx = _first_non_empty_row(raw_df)
    data_df = parse_excel_with_header_row(raw_df, header_row_idx)
    return data_df, header_row_idx, raw_df


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
    st.subheader("Column Mapping")
    st.caption("Map your file columns to the strategy engine schema.")

    show_advanced_cols = st.checkbox("Show advanced columns (incl. Unnamed)", value=False)
    all_cols = list(df.columns)
    cols = [c for c in all_cols if not str(c).lower().startswith("unnamed:")]
    if show_advanced_cols or not cols:
        cols = all_cols
    none_option = "-- Not Provided --"

    mapping: Dict[str, Optional[str]] = {}

    for key, label in REQUIRED_FIELDS.items():
        guessed = smart_guess_column(cols, [label])
        default_idx = cols.index(guessed) if guessed in cols else 0
        chosen = st.selectbox(
            f"Map required field: {label}",
            options=cols,
            index=default_idx,
            key=f"map_{key}",
        )
        mapping[key] = chosen

    for key, label in OPTIONAL_FIELDS.items():
        guessed = smart_guess_column(cols, [label])
        options = [none_option] + cols
        default_value = guessed if guessed in cols else none_option
        default_idx = options.index(default_value)
        chosen = st.selectbox(
            f"Map optional field: {label}",
            options=options,
            index=default_idx,
            key=f"map_opt_{key}",
        )
        mapping[key] = None if chosen == none_option else chosen

    return mapping


def build_working_df(df: pd.DataFrame, mapping: Dict[str, Optional[str]]) -> pd.DataFrame:
    working = pd.DataFrame(index=df.index)

    for key in REQUIRED_FIELDS:
        source_col = mapping.get(key)
        working[key] = df[source_col] if source_col in df.columns else np.nan

    for key in OPTIONAL_FIELDS:
        source_col = mapping.get(key)
        if source_col and source_col in df.columns:
            working[key] = df[source_col]
        else:
            working[key] = np.nan

    working["room_code"] = working["room_code"].astype("string")
    working["floor_code"] = working["floor_code"].astype("string")
    working["room_type"] = working["room_type"].astype("string")
    working["department"] = working["department"].astype("string")
    working["building"] = working["building"].astype("string")

    working["calculated_area"] = pd.to_numeric(working["calculated_area"], errors="coerce")
    working["occupancy"] = pd.to_numeric(working["occupancy"], errors="coerce").fillna(0)
    working["net_area"] = pd.to_numeric(working["net_area"], errors="coerce")

    missing_net_area = working["net_area"].isna()
    working.loc[missing_net_area, "net_area"] = working.loc[missing_net_area, "calculated_area"]

    return working


def required_fields_ready(working_df: pd.DataFrame) -> Tuple[bool, List[str]]:
    missing_reasons: List[str] = []
    for field_key, field_label in REQUIRED_FIELDS.items():
        if field_key not in working_df.columns:
            missing_reasons.append(f"Missing required mapped column: {field_label}")
            continue

        has_values = working_df[field_key].notna().any()
        if field_key in {"room_code", "floor_code", "room_type"}:
            as_text = working_df[field_key].astype("string").str.strip()
            has_values = as_text.replace("<NA>", pd.NA).notna().any()

        if not has_values:
            missing_reasons.append(f"Required field has no usable values: {field_label}")

    return len(missing_reasons) == 0, missing_reasons


def generate_tasks(
    working_df: pd.DataFrame,
    area_threshold: int,
    target_density: int,
) -> pd.DataFrame:
    df = working_df.copy()

    office_mask = df["room_type"].str.lower().eq("office").fillna(False).to_numpy(dtype=bool)
    area_gt_threshold = (df["calculated_area"] > area_threshold).fillna(False).to_numpy(dtype=bool)
    subdivide_mask = office_mask & area_gt_threshold

    occupancy_zero = (df["occupancy"] == 0).fillna(False).to_numpy(dtype=bool)
    net_area_small = (df["net_area"] < 50).fillna(False).to_numpy(dtype=bool)
    reallocate_mask = occupancy_zero | net_area_small

    action = np.select(
        [subdivide_mask, reallocate_mask],
        ["Subdivide", "Reallocate"],
        default="",
    )

    tasks = df[action != ""].copy()
    tasks["action"] = action[action != ""]
    tasks["current_area"] = tasks["calculated_area"].fillna(0)
    tasks["potential_area_released"] = (tasks["current_area"] - float(target_density)).clip(lower=0)

    tasks = tasks[
        [
            "room_code",
            "floor_code",
            "room_type",
            "department",
            "building",
            "occupancy",
            "net_area",
            "current_area",
            "action",
            "potential_area_released",
        ]
    ].reset_index(drop=True)

    return tasks


def tasks_to_excel_bytes(df_tasks: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_tasks.to_excel(writer, index=False, sheet_name="strategy_tasks")
    return output.getvalue()


def main() -> None:
    st.title("Space Strategy Workbench")
    st.caption("WMS-style operational task generator for campus space strategy.")

    uploaded_file = st.file_uploader("Upload Excel inventory", type=["xlsx", "xls"])
    if uploaded_file is None:
        st.info("Upload a file to begin scenario analysis.")
        return

    file_signature = f"{uploaded_file.name}_{uploaded_file.size}"
    st.session_state.setdefault("header_confirmed", None)
    st.session_state.setdefault("mapping_confirmed", None)
    st.session_state.setdefault("engine_confirmed", None)

    try:
        auto_df, auto_header_row_idx, raw_excel_df = load_excel_with_auto_header(uploaded_file)
    except Exception as exc:
        st.error(f"Failed to read Excel file: {exc}")
        return

    max_excel_row = max(len(raw_excel_df.index), 1)
    with st.expander("Step 1 · Header Alignment", expanded=True):
        st.caption(f"Auto-detected header row: Excel row {auto_header_row_idx + 1}.")
        manual_override = st.toggle("Manually override header row", value=False)

        selected_excel_row = auto_header_row_idx + 1
        if manual_override:
            selected_excel_row = st.number_input(
                "Header row number in Excel (1-based)",
                min_value=1,
                max_value=max_excel_row,
                value=min(auto_header_row_idx + 1, max_excel_row),
                step=1,
            )

        selected_header_idx = int(selected_excel_row - 1)
        raw_df = parse_excel_with_header_row(raw_excel_df, selected_header_idx)

        st.write("Detected columns:", list(raw_df.columns))
        st.dataframe(raw_df.head(5), use_container_width=True, hide_index=True)

        if st.button("Confirm header alignment", type="primary"):
            st.session_state["header_confirmed"] = f"{file_signature}_{selected_header_idx}"
            st.session_state["mapping_confirmed"] = None
            st.session_state["engine_confirmed"] = None

    header_key = f"{file_signature}_{selected_header_idx}"
    if st.session_state.get("header_confirmed") != header_key:
        st.warning("Please confirm header alignment to continue to column mapping.")
        return

    clean_df = clean_dataframe(raw_df)

    with st.expander("Step 2 · Column Mapping & Required Field Check", expanded=True):
        mapping = render_mapping_ui(clean_df)
        working_df = build_working_df(clean_df, mapping)

        is_ready, reasons = required_fields_ready(working_df)
        if is_ready:
            st.success("Required fields are mapped and contain usable values.")
        else:
            for reason in reasons:
                st.error(reason)

        if st.button("Confirm mapping and unlock strategy step", type="primary", disabled=not is_ready):
            st.session_state["mapping_confirmed"] = header_key
            st.session_state["engine_confirmed"] = None

    if st.session_state.get("mapping_confirmed") != header_key:
        st.warning("Please complete Step 2 before running strategy and generating actionable tasks.")
        return

    working_df = build_working_df(clean_df, mapping)

    with st.sidebar:
        st.header("Strategy Sandbox")
        area_threshold = st.slider("Area Threshold (sqft)", min_value=150, max_value=500, value=250)
        target_density = st.slider("Target Density (sqft/person)", min_value=80, max_value=200, value=120)

    dept_options = sorted([d for d in working_df["department"].dropna().unique().tolist() if str(d) != "<NA>"])
    bldg_options = sorted([b for b in working_df["building"].dropna().unique().tolist() if str(b) != "<NA>"])

    with st.sidebar:
        selected_depts = st.multiselect("Department Filter", options=dept_options, default=dept_options)
        selected_bldgs = st.multiselect("Building Filter", options=bldg_options, default=bldg_options)

    scoped = working_df.copy()
    if selected_depts:
        scoped = scoped[scoped["department"].isin(selected_depts)]
    if selected_bldgs:
        scoped = scoped[scoped["building"].isin(selected_bldgs)]

    with st.expander("Step 3 · Run Recommendation Engine", expanded=True):
        st.caption("Task generation is locked until you explicitly run this step.")
        if st.button("Run task engine", type="primary"):
            st.session_state["engine_confirmed"] = header_key

    if st.session_state.get("engine_confirmed") != header_key:
        st.info("Run Step 3 to generate actionable tasks.")
        return

    tasks = generate_tasks(scoped, area_threshold=area_threshold, target_density=target_density)

    total_gain = float(tasks["potential_area_released"].sum()) if not tasks.empty else 0.0
    total_tasks = int(len(tasks))

    m1, m2 = st.columns(2)
    m1.metric("Total Potential Area Gains (sqft)", f"{total_gain:,.0f}")
    m2.metric("Total Tasks Identified", f"{total_tasks}")

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
            st.success("No tasks identified for current strategy knobs.")
        else:
            notes_store = st.session_state.setdefault("task_notes", {})
            total_pages = int(np.ceil(len(tasks) / TASK_PAGE_SIZE))
            page = st.number_input("Task page", min_value=1, max_value=max(total_pages, 1), value=1, step=1)
            start_idx = int((page - 1) * TASK_PAGE_SIZE)
            end_idx = min(start_idx + TASK_PAGE_SIZE, len(tasks))
            tasks_page = tasks.iloc[start_idx:end_idx]
            st.caption(
                f"Showing tasks {start_idx + 1}-{end_idx} of {len(tasks)} "
                f"(page {page}/{total_pages}, {TASK_PAGE_SIZE} per page)."
            )

            for idx, row in tasks_page.iterrows():
                task_id = f"{row['room_code']}_{idx}_{row['action']}"
                with st.container(border=True):
                    st.markdown(
                        f"**[{row['room_code']}]** | **Floor: {row['floor_code']}** | **Action: {row['action']}** | "
                        f"**Potential Gain: {row['potential_area_released']:.1f} sqft**"
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
