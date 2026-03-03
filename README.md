# space-planning-python

Modular Python pipeline for architectural space programming with a **framework-wall** architecture (logic decoupled from institution-specific Excel schemas).

## Install dependencies

```bash
python -m pip install -r requirements.txt
```


## Codespaces 里看不到 `requirement.txt` 的原因

最常见原因是文件名记错：项目标准文件名是 **`requirements.txt`**（复数）。

为避免混淆，仓库也提供了兼容别名 **`requirement.txt`**，其内容会转发到 `requirements.txt`。

在 Codespaces 里请确认：

```bash
pwd
ls -la
```

你应能看到这两个文件：
- `requirements.txt`
- `requirement.txt`

安装命令（两者都可）：

```bash
python -m pip install -r requirements.txt
# 或
python -m pip install -r requirement.txt
```

## 一键产出“干净数据池”

直接运行：

```bash
python run_stage1.py
```

输出文件：
- `data_pool_clean.parquet`（推荐给下一阶段）
- `data_pool_clean.csv`
- `df_final_floor_summary.xlsx`
- `discrepancy_outliers.csv`

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

