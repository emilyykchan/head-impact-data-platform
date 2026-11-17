import importlib.util
from pathlib import Path
import unittest

P=Path(__file__).resolve().parents[1] / 'scripts' / 'normalise_ds013_future_ingestion_sql.py'
spec=importlib.util.spec_from_file_location('normalize_ingest_sql', P)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class SQLNormalizationTest(unittest.TestCase):
    def test_only_impact_keys_change_not_kinematic_uids(self):
        input_sql = "\n".join([
          "'IMP_DS013_' || s.impact_key,",
          "'IMP_DS013_' || impact_key,",
          "'IMP_DS013_' || impact_key,",
          "NULLIF(s.source_impact_id, ''),",
          "'KIN_IMP_DS013_' || impact_key || '_' || UPPER(data_stage),",
        ])
        result, changed=m.transform(input_sql)
        self.assertTrue(changed)
        self.assertIn("lpad(split_part(impact_key, '-', 2), 4, '0')", result)
        self.assertIn("'KIN_IMP_DS013_' || impact_key", result)
        self.assertNotIn("'IMP_DS013_' || impact_key", result)
        self.assertFalse(m.transform(result)[1])
    def test_unexpected_source_file_fails_closed(self):
        with self.assertRaisesRegex(ValueError,'Unexpected DS013'):
            m.transform('select 1')

if __name__=='__main__':unittest.main()
