from __future__ import annotations

from io import BytesIO
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Space Strategy Workbench", layout="wide")

REQUIRED_FIELDS = {
    "room_code": "Room Code",
    "room_type": "Room Type",
    "calculated_area": "Calculated Area",
}
OPTIONAL_FIELDS = {
    "department": "Department",
    "building": "Building",
    "occupancy": "Occupancy",
    "net_area": "Net Area",
}


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

    cols = list(df.columns)
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
    working["room_type"] = working["room_type"].astype("string")
    working["department"] = working["department"].astype("string")
    working["building"] = working["building"].astype("string")

    working["calculated_area"] = pd.to_numeric(working["calculated_area"], errors="coerce")
    working["occupancy"] = pd.to_numeric(working["occupancy"], errors="coerce").fillna(0)
    working["net_area"] = pd.to_numeric(working["net_area"], errors="coerce")

    missing_net_area = working["net_area"].isna()
    working.loc[missing_net_area, "net_area"] = working.loc[missing_net_area, "calculated_area"]

    return working


def generate_tasks(
    working_df: pd.DataFrame,
    area_threshold: int,
    target_density: int,
) -> pd.DataFrame:
    df = working_df.copy()

    office_mask = df["room_type"].str.lower().eq("office")
    subdivide_mask = office_mask & (df["calculated_area"] > area_threshold)

    reallocate_mask = (df["occupancy"] == 0) | (df["net_area"] < 50)

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

    try:
        raw_df = pd.read_excel(uploaded_file)
    except Exception as exc:
        st.error(f"Failed to read Excel file: {exc}")
        return

    clean_df = clean_dataframe(raw_df)

    with st.sidebar:
        st.header("Strategy Sandbox")
        area_threshold = st.slider("Area Threshold (sqft)", min_value=150, max_value=500, value=250)
        target_density = st.slider("Target Density (sqft/person)", min_value=80, max_value=200, value=120)

    mapping = render_mapping_ui(clean_df)
    working_df = build_working_df(clean_df, mapping)

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

    tasks = generate_tasks(scoped, area_threshold=area_threshold, target_density=target_density)

    total_gain = float(tasks["potential_area_released"].sum()) if not tasks.empty else 0.0
    total_tasks = int(len(tasks))

    m1, m2 = st.columns(2)
    m1.metric("Total Potential Area Gains (sqft)", f"{total_gain:,.0f}")
    m2.metric("Total Tasks Identified", f"{total_tasks}")

    left, right = st.columns([1.2, 1])

    with left:
        st.subheader("Inventory View")
        search = st.text_input("Search inventory (room/department/building)")
        display_df = scoped.copy()
        if search:
            q = search.lower().strip()
            mask = (
                display_df["room_code"].astype(str).str.lower().str.contains(q, na=False)
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

            for idx, row in tasks.iterrows():
                task_id = f"{row['room_code']}_{idx}_{row['action']}"
                with st.container(border=True):
                    st.markdown(
                        f"**[{row['room_code']}]** | **Action: {row['action']}** | "
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
