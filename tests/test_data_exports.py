"""Small synthetic DuckDB integration tests; no real participant or NAS data."""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import data_exports as ex  # noqa: E402
import storage  # noqa: E402


class DynamicExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_path = str(self.root / "tests.duckdb")
        with duckdb.connect(self.db_path) as con:
            con.execute("""
              CREATE TABLE datasets(dataset_id VARCHAR PRIMARY KEY, sport VARCHAR,
                                    device VARCHAR, default_sample_interval_ms DOUBLE)
            """)
            con.execute("INSERT INTO datasets VALUES ('DS004','RFU','Protecht v2',1.0), ('DS006','Boxing','Adidas',0.3125)")
            con.execute("""
              CREATE TABLE impacts(
                dataset_id VARCHAR, impact_uid VARCHAR PRIMARY KEY,
                source_impact_id VARCHAR, source_sample_id VARCHAR,
                participant_uid VARCHAR, event_uid VARCHAR,
                impact_timestamp TIMESTAMP)
            """)
            con.execute("""
              CREATE TABLE kinematic_records(
                kinematic_uid VARCHAR, impact_uid VARCHAR, data_stage VARCHAR,
                sample_interval_ms DOUBLE, n_samples INTEGER,
                storage_root VARCHAR, relative_path VARCHAR)
            """)
            con.execute("""
              CREATE TABLE kinematic_metrics(
                metric_uid VARCHAR, kinematic_uid VARCHAR, metric_name VARCHAR,
                "value" DOUBLE, unit VARCHAR)
            """)
            con.execute("""
              CREATE TABLE simulation_runs(
                simulation_uid VARCHAR, impact_uid VARCHAR,
                model_name VARCHAR, model_version VARCHAR)
            """)
            con.execute("""
              CREATE TABLE brain_metric_results(
                brain_metric_uid VARCHAR, simulation_uid VARCHAR,
                roi_name VARCHAR, metric_name VARCHAR, summary_method VARCHAR,
                "value" DOUBLE, unit VARCHAR)
            """)
            con.execute("""
              CREATE TABLE simulation_artifacts(
                artifact_uid VARCHAR, simulation_uid VARCHAR, artifact_type VARCHAR,
                storage_root VARCHAR, relative_path VARCHAR)
            """)
            con.execute("""
              CREATE TABLE kinematic_waveforms(
                kinematic_uid VARCHAR, linear_acc_x_g DOUBLE[],
                angular_vel_x_rad_s DOUBLE[])
            """)
            con.execute("INSERT INTO impacts VALUES ('DS004','Rugby1','1',NULL,'P1',NULL,NULL), ('DS006','Boxing1','2',NULL,'P2',NULL,NULL)")
            con.execute("INSERT INTO kinematic_records VALUES ('KIN1','Rugby1','processed',1,2,NULL,NULL), ('KIN2','Boxing1','processed',1,2,NULL,NULL)")
            con.execute("INSERT INTO kinematic_metrics VALUES ('a','KIN1','PLA',22.0,'g'),('b','KIN1','PRV',15.0,'rad/s'),('c','KIN2','PLA',17.0,'g')")
            con.execute("INSERT INTO simulation_runs VALUES ('SIM1','Rugby1','IC FE','1.0')")
            con.execute("INSERT INTO brain_metric_results VALUES ('bm1','SIM1','Whole brain','MPS95','95th percentile',0.14,'strain')")
            con.execute("INSERT INTO kinematic_waveforms VALUES ('KIN1',[1.0,2.0],[3.0,4.0]),('KIN2',[2.0,3.0],[4.0,5.0])")

    def _all_impacts(self):
        return ex.impact_rows(self.db_path, ['DS004','DS006'])

    def test_metrics_are_dynamic_and_absence_is_missing_not_zero(self):
        catalog = ex.metric_catalog(self.db_path, ['DS004','DS006'])
        self.assertTrue(any('MPS95' in s for s in catalog.label))
        selected = catalog.loc[catalog.label.str.contains('PLA|MPS95'), 'metric_key'].drop_duplicates().tolist()
        result = ex.build_summary(self.db_path, self._all_impacts(),
                                  ['dataset_id','impact_uid'],selected)
        self.assertEqual(len(result),2)
        self.assertEqual(result['impact_uid'].nunique(),2)
        brain_col = next(x for x in result.columns if 'mps95' in x)
        self.assertIn('kinematics_pla_g', result.columns)
        self.assertTrue(all(' ' not in c and '.' not in c for c in result.columns))
        ds006 = result.loc[result.dataset_id=='DS006'].iloc[0]
        self.assertTrue(pd.isna(ds006[brain_col]))
        self.assertEqual(float(result.loc[result.dataset_id=='DS004',brain_col].iloc[0]),.14)

        # A future metric should appear without editing a hardcoded UI list.
        with duckdb.connect(self.db_path) as con:
            con.execute("INSERT INTO kinematic_metrics VALUES ('d','KIN1','DAMAGE',0.3,'score')")
        catalog2 = ex.metric_catalog(self.db_path, ['DS004'])
        self.assertTrue(any('DAMAGE' in x for x in catalog2.label))

    def test_raw_metrics_do_not_appear_in_scalar_choices(self):
        with duckdb.connect(self.db_path) as con:
            con.execute("INSERT INTO kinematic_records VALUES ('RAW1','Rugby1','raw',1,2,NULL,NULL)")
            con.execute("INSERT INTO kinematic_metrics VALUES ('raw','RAW1','PRA',999,'rad/s2')")
        catalog = ex.metric_catalog(self.db_path, ['DS004'])
        self.assertFalse(any('PRA' in x for x in catalog.label))
        selected = catalog['metric_key'].drop_duplicates().tolist()
        exported = ex.build_summary(self.db_path, ex.impact_rows(self.db_path,['DS004']),
                                    ['source_impact_id'], selected)
        self.assertFalse(any('pra' in c for c in exported.columns))

    def test_dataset_metadata_columns_can_be_selected(self):
        fields = ex.dataset_metadata_options(self.db_path)
        self.assertIn('dataset_device', fields)
        self.assertIn('dataset_sport', fields)
        selected = ex.build_summary(self.db_path, self._all_impacts(),
                                    ['source_impact_id'], [],
                                    ['dataset_device','dataset_sport','dataset_default_sample_interval_ms'])
        self.assertEqual(len(selected), 2)
        self.assertEqual(selected.loc[selected['dataset_id']=='DS004','dataset_device'].iloc[0], 'Protecht v2')
        self.assertEqual(float(selected.loc[selected['dataset_id']=='DS006','dataset_default_sample_interval_ms'].iloc[0]), 0.3125)
        self.assertIn('impact_uid', selected.columns)
        with self.assertRaisesRegex(ex.ExportError,'Unknown dataset-level'):
            ex.build_summary(self.db_path, self._all_impacts(), ['impact_uid'], [],
                             ['dataset_private_password'])

    def test_ambiguous_duplicate_metrics_are_rejected(self):
        with duckdb.connect(self.db_path) as con:
            con.execute("INSERT INTO kinematic_metrics VALUES ('duplicated','KIN1','PLA',99,'g')")
        catalog = ex.metric_catalog(self.db_path, ['DS004'])
        pla = next(k for k in catalog.metric_key if 'PLA' in k)
        with self.assertRaisesRegex(ex.ExportError, 'Multiple scalar records'):
            ex.build_summary(self.db_path,
                             ex.impact_rows(self.db_path,['DS004']),
                             ['dataset_id','impact_uid'],[pla])

    def test_zip_includes_waveforms_and_actual_nas_bytes(self):
        nas = self.root / 'nas'
        (nas / 'DS004').mkdir(parents=True)
        expected = b'\x00NIFTI TEST BYTES\x01'
        (nas / 'DS004' / 'brain.nii.gz').write_bytes(expected)
        with duckdb.connect(self.db_path) as con:
            con.execute("INSERT INTO simulation_artifacts VALUES ('A1','SIM1','spatial_field','NAS_HEAD_IMPACT','DS004/brain.nii.gz')")
        impacts = ex.impact_rows(self.db_path, ['DS004'])
        summary = ex.build_summary(self.db_path, impacts,['dataset_id','impact_uid'],[])
        with patch.dict(os.environ, {'HEAD_IMPACT_ENABLE_NAS_DOWNLOADS':'1'}), \
             patch.dict(storage.STORAGE_ROOTS, {'NAS_HEAD_IMPACT':str(nas)}):
            payload,manifest=ex.build_zip(
                self.db_path, impacts, summary, ['processed'], ['linear_acc_x_g'],
                include_waveforms=True,include_source_files=False,include_nifti=True)
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            files=zf.namelist()
            self.assertIn('impact_summary.csv',files)
            self.assertIn('export_manifest.csv',files)
            self.assertTrue(any(f.startswith('waveforms/') for f in files))
            nifti=next(f for f in files if f.endswith('brain.nii.gz'))
            self.assertEqual(zf.read(nifti),expected)
        self.assertTrue((manifest.status=='included').any())

    def test_nas_access_disabled_by_default(self):
        impacts=ex.impact_rows(self.db_path,['DS004'])
        summary=ex.build_summary(self.db_path,impacts,['dataset_id','impact_uid'],[])
        with patch.dict(os.environ,{'HEAD_IMPACT_ENABLE_NAS_DOWNLOADS':''}):
            with self.assertRaises(PermissionError):
                ex.build_zip(self.db_path,impacts,summary,['processed'],[],False,False,True)

    def test_100_impact_limit(self):
        impacts = pd.DataFrame({'impact_uid':[str(x) for x in range(101)]})
        with self.assertRaisesRegex(ex.ExportError,'100'):
            ex.build_zip(self.db_path,impacts,impacts,[],[],False,False,False)


if __name__=='__main__':
    unittest.main()
