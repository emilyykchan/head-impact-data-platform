"""Tests for the export builder's read-only bulk impact selection."""
import pathlib
import sys
import unittest

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))
from impact_selection import parse_impact_ids, resolve_impact_ids


class ImpactSelectionTests(unittest.TestCase):
    def setUp(self):
        self.rows = pd.DataFrame([
            {"dataset_id": "DS002", "impact_uid": "IMP_DS002_MMA374", "source_impact_id": "374"},
            {"dataset_id": "DS002", "impact_uid": "IMP_DS002_MMA377", "source_impact_id": "377"},
            {"dataset_id": "DS003", "impact_uid": "IMP_DS003_MMA374", "source_impact_id": "374"},
        ])

    def test_parses_excel_and_comma_semicolon_inputs(self):
        self.assertEqual(parse_impact_ids("MMA374\nMMA377, MMA374;\t377"),
                         ["MMA374", "MMA377", "377"])

    def test_exact_case_insensitive_and_no_prefix_matches(self):
        matched, missing, ambiguous = resolve_impact_ids(
            "imp_ds002_mma377, MMA37", self.rows)
        self.assertEqual(matched, ["IMP_DS002_MMA377"])
        self.assertEqual(missing, ["MMA37"])
        self.assertEqual(ambiguous, {})

    def test_resolves_source_and_short_form_in_single_dataset(self):
        matched, missing, ambiguous = resolve_impact_ids("MMA374 377", self.rows.iloc[:2])
        self.assertEqual(matched, ["IMP_DS002_MMA374", "IMP_DS002_MMA377"])
        self.assertEqual(missing, [])
        self.assertEqual(ambiguous, {})

    def test_reports_ambiguity_for_multiple_datasets(self):
        matched, missing, ambiguous = resolve_impact_ids("MMA374", self.rows)
        self.assertEqual(matched, [])
        self.assertEqual(missing, [])
        self.assertEqual(ambiguous["MMA374"], ["IMP_DS002_MMA374", "IMP_DS003_MMA374"])

    def test_empty_text(self):
        self.assertEqual(resolve_impact_ids("  ", self.rows), ([], [], {}))


if __name__ == "__main__":
    unittest.main()
