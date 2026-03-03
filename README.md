# space-planning-python

Modular Python pipeline for architectural space programming with a **framework-wall** architecture (logic decoupled from institution-specific Excel schemas).

## Install dependencies

```bash
python -m pip install -r requirements.txt
```

## 门卫逻辑（文件缺失友好提示）

如果源文件不存在，代码会抛出友好提示：

> 找不到文件！请确认 '20251101 UPitt Space List - In Scope.xlsx' 是否已放在目录下。

并附带下一步建议：
- 文件名不同：修改 `file_path`
- 多个文件来源：使用 `sources=[...]`
- 数据库来源：使用 `source_type='database'`

## 单文件（UPitt）示例

```python
from space_programming_pipeline import run_pitt_pipeline

PITT_CONFIG = {
    "file_path": "20251101 UPitt Space List - In Scope.xlsx",
    "sheet_name": "Rooms Pct",
    "header": 2,
    "columns": {
        "building": "Building Code",
        "floor": "Floor Code",
        "room_code": "Room Code",
        "room_area": "Room Area",
        "percentage": "Percentage of Space",
        "calculated_area": "Calculated Area",
    },
    "id_components": ["building", "floor"],
    # 支持 logical names 或 Excel 实际列名
    "numeric_cols": ["Room Area", "Percentage of Space", "Calculated Area"],
    "truth_area_col": "calculated_area",
    "room_code_col": "room_code",
}

stages = run_pitt_pipeline(PITT_CONFIG, export_path="Floor_Summary_Result.xlsx")
```

## 多源配置（多个文件 + 数据库）

```python
from space_programming_pipeline import run_space_programming_pipeline_staged

config = {
    "columns": {
        "building": "Building Code",
        "floor": "Floor Code",
        "room_code": "Room Code",
        "room_area": "Room Area",
        "percentage": "Percentage of Space",
        "calculated_area": "Calculated Area",
    },
    "id_components": ["building", "floor"],
    "numeric_cols": ["Room Area", "Percentage of Space", "Calculated Area"],
    "truth_area_col": "calculated_area",
    "room_code_col": "room_code",
    "sources": [
        {
            "source_name": "upitt_excel",
            "source_type": "excel",
            "file_path": "20251101 UPitt Space List - In Scope.xlsx",
            "sheet_name": "Rooms Pct",
            "header": 2,
        },
        {
            "source_name": "facility_db",
            "source_type": "database",
            "sqlite_path": "facility.db",
            "query": "SELECT * FROM roompct_view",
        },
    ],
}

stages = run_space_programming_pipeline_staged(config)
print(stages["df_raw"].head())
print(stages["df_final"].head())
```

> 多源模式会自动添加 `__source_name` 字段用于追踪数据来源。

## Core outputs

- `df_clean`: cleaned row-level data
- `df_final`: floor summary table
- `discrepancy_outliers`: rows with discrepancy > 1 sqft

