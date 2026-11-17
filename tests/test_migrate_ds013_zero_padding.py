"""Unit tests for source-defined DS013 ID migration mapping (no DuckDB writes)."""
import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'migrate_ds013_zero_padding.py'
spec = importlib.util.spec_from_file_location('ds013_migration', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class MigrationMappingTests(unittest.TestCase):
    def test_sample_id_padding_and_collision_guard(self):
        source = {'95959-802': '95959-0802', '96313-1304': '96313-1304'}
        rows = [('IMP_DS013_95959-802', '95959-802'),
                ('IMP_DS013_96313-1304', '96313-1304')]
        actual = mod.plan_mapping(rows, source, set(u for u, _ in rows), expected_impacts=2)
        self.assertEqual(actual, [('IMP_DS013_95959-802', 'IMP_DS013_95959-0802', '95959-0802')])
    def test_source_id_already_correct(self):
        source = {'95959-802': '95959-0802'}
        rows = [('IMP_DS013_95959-0802', '95959-0802')]
        self.assertEqual(mod.plan_mapping(rows, source, {rows[0][0]}, expected_impacts=1), [])
    def test_conflicting_source_impact_id_blocks(self):
        source = {'95959-802': '95959-0802'}
        with self.assertRaisesRegex(ValueError, 'conflict'):
            mod.plan_mapping([('IMP_DS013_95959-802', '99999-802')], source,
                             {'IMP_DS013_95959-802'}, expected_impacts=1)
    def test_existing_target_uid_collision_blocks(self):
        source = {'95959-802': '95959-0802'}
        with self.assertRaisesRegex(ValueError, 'already exists'):
            mod.plan_mapping([('IMP_DS013_95959-802', '95959-802')], source,
                             {'IMP_DS013_95959-802', 'IMP_DS013_95959-0802'},
                             expected_impacts=1)
    def test_requires_expected_dataset_count(self):
        with self.assertRaisesRegex(ValueError, 'exactly'):
            mod.plan_mapping([], {}, set(), expected_impacts=492)
    def test_source_staging_exact_4_digit_prefix_and_consistent_subject(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'source.csv'
            with p.open('w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['dataset_id', 'alternate_key', 'source_key'])
                writer.writeheader()
                writer.writerow({'dataset_id': 'DS013', 'alternate_key': '95959-0802',
                                 'source_key':'MG95959-0802-2025-05-31T170635+167'})
            old_count = mod.EXPECTED_IMPACTS
            try:
                mod.EXPECTED_IMPACTS = 1
                self.assertEqual(mod.source_ids_from_staging(p), {'95959-802': '95959-0802'})
            finally:
                mod.EXPECTED_IMPACTS = old_count

if __name__ == '__main__':
    unittest.main()
