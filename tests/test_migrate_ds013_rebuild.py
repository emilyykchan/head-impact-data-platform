"""No-database tests of the safe DuckDB EXPORT/IMPORT rebuild helpers."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

script = Path(__file__).resolve().parents[1] / 'scripts' / 'migrate_ds013_zero_padding.py'
spec = importlib.util.spec_from_file_location('ds013_migration_rebuild', script)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class RebuildHelpersTests(unittest.TestCase):
    def test_projection_preserves_secondary_primary_keys(self):
        val = mod.corrected_projection(['kinematic_uid','impact_uid','data_stage'], 'kinematic_records')
        self.assertIn('t."kinematic_uid" AS "kinematic_uid"', val)
        self.assertIn('COALESCE(m.new_uid, t.impact_uid) AS "impact_uid"', val)
        self.assertNotIn('new_source', val)

    def test_projection_source_field_only_for_impacts(self):
        val = mod.corrected_projection(['impact_uid','source_impact_id', 'source_sample_id'], 'impacts')
        self.assertIn('COALESCE(m.new_uid, t.impact_uid)', val)
        self.assertIn('COALESCE(m.new_source, t.source_impact_id)', val)
        self.assertIn('t."source_sample_id" AS "source_sample_id"', val)

    def test_parquet_paths_from_quoted_and_unquoted_copy(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p/'impacts.parquet').write_bytes(b'p')
            (p/'kinematic_records.parquet').write_bytes(b'p')
            (p/'staging_foo.parquet').write_bytes(b'p')
            (p/'load.sql').write_text(
                f'''COPY "main"."impacts" FROM '{p}/impacts.parquet' (FORMAT PARQUET);\n'''
                "COPY main.kinematic_records FROM 'kinematic_records.parquet' (FORMAT PARQUET);\n"
                "COPY staging.foo FROM 'staging_foo.parquet' (FORMAT PARQUET);\n"
            )
            paths = mod.exported_parquet_paths(p)
            self.assertEqual(set(paths), {'impacts', 'kinematic_records'})
            self.assertEqual(paths['impacts'], (p/'impacts.parquet').resolve())

    def test_export_path_cannot_escape(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            (p/'load.sql').write_text("COPY main.impacts FROM '../secret.parquet' (FORMAT PARQUET);")
            with self.assertRaisesRegex(ValueError, 'outside export directory'):
                mod.exported_parquet_paths(p)

    def test_duplicate_export_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            (p/'a.parquet').write_bytes(b'p')
            (p/'b.parquet').write_bytes(b'p')
            (p/'load.sql').write_text(
                "COPY main.impacts FROM 'a.parquet';\nCOPY \"main\".\"impacts\" FROM 'b.parquet';\n")
            with self.assertRaisesRegex(ValueError, 'Multiple export files'):
                mod.exported_parquet_paths(p)

if __name__ == '__main__':
    unittest.main()
