import unittest

import pandas as pd

from engine import build_benchmark_table, generate_tasks
from space_programming_pipeline import (
    audit_canonical_inventory,
    build_canonical_inventory,
)


class PipelineAndEngineTests(unittest.TestCase):
    def test_room_share_percentage_prevents_false_integrity_error(self) -> None:
        inventory = pd.DataFrame(
            {
                "room_code": ["03"],
                "room_type": ["Office"],
                "calculated_area": [60.0],
                "room_area": [100.0],
                "percentage": [60.0],
            }
        )
        audit = audit_canonical_inventory(inventory)
        self.assertEqual(audit.loc[0, "discrepancy_abs_sqft"], 0.0)
        self.assertEqual(audit.loc[0, "integrity_source"], "Room Area × Percentage")

    def test_integrity_task_wins_over_opportunity_task(self) -> None:
        inventory = pd.DataFrame(
            {
                "__source_row": [0],
                "room_code": ["101"],
                "room_type": ["Office"],
                "calculated_area": [300.0],
                "room_area": [200.0],
                "percentage": [100.0],
            }
        )
        benchmark = pd.DataFrame(
            {
                "room_type": ["Office"],
                "benchmark_area": [100.0],
                "source": ["Session Override"],
            }
        )
        tasks = generate_tasks(inventory, benchmark)
        self.assertEqual(tasks.loc[0, "action"], "AUDIT DATA")
        self.assertEqual(tasks.loc[0, "integrity_score"], "Critical")
        self.assertEqual(tasks.loc[0, "opportunity_score"], "High")

    def test_benchmark_override_changes_opportunity_result(self) -> None:
        inventory = pd.DataFrame(
            {
                "__source_row": [0],
                "room_code": ["102"],
                "room_type": ["Office"],
                "calculated_area": [220.0],
                "room_area": [220.0],
            }
        )
        defaults = build_benchmark_table(inventory)
        self.assertTrue(generate_tasks(inventory, defaults).empty)

        override = defaults.copy()
        override["benchmark_area"] = 150.0
        override["source"] = "Session Override"
        tasks = generate_tasks(inventory, override)
        self.assertEqual(tasks.loc[0, "action"], "CAPTURE OPPORTUNITY")
        self.assertEqual(tasks.loc[0, "potential_area_released"], 70.0)

    def test_framework_wall_preserves_room_code_and_maps_logical_fields(self) -> None:
        raw = pd.DataFrame(
            {
                " Room Number ": ["03"],
                " Space Type ": [" Office "],
                " Allocated Area ": ["240"],
            }
        )
        config = {
            "columns": {
                "room_code": "Room Number",
                "room_type": "Space Type",
                "calculated_area": "Allocated Area",
            },
            "room_code_col": "room_code",
            "numeric_cols": ["calculated_area"],
        }
        canonical = build_canonical_inventory(raw, config)
        self.assertEqual(canonical.loc[0, "room_code"], "03")
        self.assertEqual(canonical.loc[0, "room_type"], "Office")
        self.assertEqual(canonical.loc[0, "calculated_area"], 240.0)

    def test_future_scoring_hooks_are_optional_but_callable(self) -> None:
        inventory = pd.DataFrame(
            {
                "__source_row": [0],
                "room_code": ["103"],
                "room_type": ["Lab"],
                "calculated_area": [400.0],
                "room_area": [400.0],
            }
        )
        benchmark = pd.DataFrame(
            {
                "room_type": ["Lab"],
                "benchmark_area": [300.0],
                "source": ["Demo Default"],
            }
        )
        tasks = generate_tasks(
            inventory,
            benchmark,
            alignment=lambda row: 0.8,
            relocation_difficulty=lambda row: "Medium",
        )
        self.assertEqual(tasks.loc[0, "strategic_alignment_score"], 0.8)
        self.assertEqual(tasks.loc[0, "relocation_difficulty_score"], "Medium")


if __name__ == "__main__":
    unittest.main()

