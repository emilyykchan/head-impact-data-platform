"""Unit tests for the offline source-summary importer (no research data committed)."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
from unittest import TestCase, main, mock
from xml.sax.saxutils import escape
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ingest_strain_summaries as imp


class IngestionTests(TestCase):
    def test_roi_aliases_match_legacy_and_numbered_sources(self):
        self.assertEqual(imp.normalise_roi("CCB"), imp.normalise_roi("10_CorpusCallosum_Body"))
        self.assertEqual(imp.normalise_roi("SLFT_R"), imp.normalise_roi("16_Sup_Longitudinal_Fasc_Temporal_R"))
        self.assertEqual(imp.normalise_roi("BrainStem"), "Brainstem")
        self.assertNotEqual(imp.normalise_roi("CC"), imp.normalise_roi("CC_max"))
        with self.assertRaises(ValueError):
            imp.normalise_roi("unknown_roi")

    def test_scan_filename_overrides_mislabelled_95_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "2_Strain_95Perc.csv"
            path.write_text("subject,90PercStrain,90PercStrainRate,10_CorpusCallosum_Body,BrainStem\n"
                            "MMA1,0.12,0.02,0.10,0.11\n")
            scan = imp.scan_sources(Path(directory))
            self.assertFalse(scan.issues)
            self.assertEqual(len(scan.records), 3)  # no rate until unit confirmed
            self.assertEqual({r.metric_name for r in scan.records}, {"MPS95"})
            self.assertEqual({r.summary_method for r in scan.records}, {"source-reported 95th percentile"})
            self.assertTrue(any(r.roi_name == "Whole Brain" for r in scan.records))
            scan_rate = imp.scan_sources(Path(directory), "1/ms")
            self.assertEqual(len(scan_rate.records), 4)
            self.assertTrue(any(r.metric_name == "MPSR95" and r.unit == "1/ms" for r in scan_rate.records))

    def test_selected_roi_only_percentile_qc_and_no_lr_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "2_MMA_March_strain.csv").write_text(
                "subject,90PercStrain,CCB,SLFT_R,BrainStem,CC\nMMA1,0.10,0.25,0.18,0.11,0.12\n")
            (folder / "2_Strain_95Perc.csv").write_text(
                "subject,90PercStrain,10_CorpusCallosum_Body,16_Sup_Longitudinal_Fasc_Temporal_R,BrainStem\nMMA1,0.11,0.24,0.15,0.12\n")
            (folder / "2_MMA_March_strain_noLR.csv").write_text(
                "subject,90PercStrain,CCB\nMMA1,0.99,0.9\n")
            scan = imp.scan_sources(folder)
            self.assertEqual(len(scan.stats), 2)
            self.assertEqual(len(scan.records), 6)
            self.assertEqual(len(scan.percentile_issues), 1)
            self.assertEqual(scan.percentile_issues[0][2], "Corpus Callosum Body")
            self.assertFalse({"Superior Longitudinal Fasciculus Temporal (Right)",
                              "Corpus Callosum (combined, source CC)"}
                             & {r.roi_name for r in scan.records})

    def test_staging_roundtrip_preserves_only_allowed_metrics_and_qc(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "2_MMA_March_strain.csv").write_text(
                "subject,90PercStrain,90PercStrainRate,CCG,UF_R,BrainStem\n"
                "MMA1,0.10,0.02,0.08,0.09,0.05\n")
            (folder / "2_Strain_95Perc.csv").write_text(
                "subject,90PercStrain,90PercStrainRate,11_CorpusCallosum_Genu,BrainStem\n"
                "MMA1,0.12,0.025,0.07,0.06\n")
            scan = imp.scan_sources(folder)
            path = folder / "private_staging.csv"
            imp.write_staging(scan, path)
            reloaded = imp.read_staging(path)
            self.assertEqual(scan.records, reloaded.records)
            self.assertEqual(scan.percentile_issues, reloaded.percentile_issues)
            self.assertEqual(len(reloaded.records), 6)
            self.assertEqual(len(reloaded.percentile_issues), 1)
            self.assertEqual(reloaded.percentile_issues[0][2], "Corpus Callosum Genu")
            self.assertFalse(reloaded.issues)

    def test_refuse_invalid_prepared_metric(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prepared.csv"
            path.write_text(
                ",".join(imp.STAGING_COLUMNS) + "\n" +
                "DS002,MMA1,,MPS90,Uncinate Fasciculus,source-reported 90th percentile,0.1,strain,a.csv,UF,sha\n")
            scan = imp.read_staging(path)
            self.assertEqual(len(scan.records), 0)
            self.assertEqual(len(scan.issues), 1)

    def test_xlsx_source_read_by_stdlib(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "13_LeedsRhinos_Strain_95Perc.xlsx"
            root = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                    '<row r="1">'
                    '<c r="A1" t="inlineStr"><is><t>subject</t></is></c>'
                    '<c r="B1" t="inlineStr"><is><t>ImpactID</t></is></c>'
                    '<c r="C1" t="inlineStr"><is><t>90PercStrain</t></is></c>'
                    '<c r="D1" t="inlineStr"><is><t>BrainStem</t></is></c>'
                    '</row><row r="2">'
                    '<c r="A2" t="inlineStr"><is><t>MGsomeID</t></is></c>'
                    '<c r="B2" t="inlineStr"><is><t>ID1</t></is></c>'
                    '<c r="C2"><v>0.12</v></c><c r="D2"><v>0.11</v></c>'
                    '</row></sheetData></worksheet>')
            with ZipFile(path, "w") as z:
                z.writestr("xl/worksheets/sheet1.xml", root)
            scan = imp.scan_sources(Path(directory))
            self.assertFalse(scan.issues)
            self.assertEqual(len(scan.records), 2)
            self.assertEqual({r.metric_name for r in scan.records}, {"MPS95"})
            self.assertEqual(scan.records[0].alternate_key, "ID1")

    def test_rugby_legacy_and_canonical_impact_resolution(self):
        r3 = imp.SourceRecord("DS003", "Rugby2", "", "MPS90", "Whole Brain", "source-reported 90th percentile", .12, "strain", "3.csv", "90PercStrain", "abcd")
        r4 = imp.SourceRecord("DS004", "Rugby2", "", "MPS90", "Whole Brain", "source-reported 90th percentile", .12, "strain", "4.csv", "90PercStrain", "abcd")
        legacy = {"DS003": [("IMP_DS003_Rugby2", None, None)], "DS004": [("IMP_DS004_Rugby2", None, None)]}
        canonical = {"DS003_RFU1701": [("IMP_DS003_RFU1701_Rugby2", None, None), ("IMP_DS003_RFU1701_Rugby548", None, None)]}
        self.assertEqual(imp._match_impact(r3, legacy, False)[0], "IMP_DS003_Rugby2")
        self.assertEqual(imp._match_impact(r4, legacy, False)[0], "IMP_DS004_Rugby2")
        self.assertEqual(imp._match_impact(r3, canonical, True)[0], "IMP_DS003_RFU1701_Rugby2")
        self.assertEqual(imp._match_impact(r4, canonical, True)[0], "IMP_DS003_RFU1701_Rugby548")

    def test_ds004_local_source_to_global_db_numbering(self):
        impact_lookup = {"DS004": [
            (f"IMP_DS004_Rugby{i}", None, None) for i in range(547, 1702)
        ]}
        offset = imp._ds004_impact_offset(impact_lookup)
        self.assertEqual(offset, 546)
        for local, expected in [(1, 547), (500, 1046), (501, 1047), (1155, 1701)]:
            rec = imp.SourceRecord("DS004", f"Rugby{local}", "", "MPS90",
                "Whole Brain", "source-reported 90th percentile", .12,
                "strain", "4.csv", "90PercStrain", "abcd")
            result, state = imp._match_impact(rec, impact_lookup, False,
                                              ds004_offset=offset)
            self.assertEqual(result, f"IMP_DS004_Rugby{expected}")
            self.assertEqual(state, "matched")

    def test_ds013_leeds_rhinos_leading_zero_ids(self):
        # Original source Excel: 95959-0802; DB: 95959-802.
        source = imp.SourceRecord("DS013", "MG95959-0802-2025-05-31T170635+167",
            "95959-0802", "MPS95", "Whole Brain", "source-reported 95th percentile",
            .12, "strain", "13.csv", "90PercStrain", "abcd")
        lookup = {"DS013": [("IMP_DS013_95959-802", "95959-802", "802")]}
        uid, status = imp._match_impact(source, lookup, False)
        self.assertEqual((uid, status), ("IMP_DS013_95959-802", "matched"))

        # Already unpadded IDs must continue to match unchanged.
        source2 = imp.SourceRecord("DS013", "MG96313-1304-2025-05-31T171313+934",
            "96313-1304", "MPS90", "Whole Brain", "source-reported 90th percentile",
            .08, "strain", "13.csv", "90PercStrain", "abcd")
        lookup2 = {"DS013": [("IMP_DS013_96313-1304", "96313-1304", "1304")]}
        uid2, status2 = imp._match_impact(source2, lookup2, False)
        self.assertEqual((uid2, status2), ("IMP_DS013_96313-1304", "matched"))

        # Never normalise away the device ID; a bare sample number is not enough.
        other = {"DS013": [("IMP_DS013_99999-802", "99999-802", "802")]}
        _, status3 = imp._match_impact(source, other, False)
        self.assertEqual(status3, "no_impact_match")

    def test_ds004_local_db_numbering_is_unchanged(self):
        impact_lookup = {"DS004": [
            (f"IMP_DS004_Rugby{i}", None, None) for i in range(1, 1156)
        ]}
        self.assertEqual(imp._ds004_impact_offset(impact_lookup), 0)

    def test_ds004_mixed_numbering_refuses_to_guess(self):
        impact_lookup = {"DS004": [
            (f"IMP_DS004_Rugby{i}", None, None) for i in range(2, 1157)
        ]}
        with self.assertRaises(ValueError):
            imp._ds004_impact_offset(impact_lookup)

    def test_database_matching_idempotence(self):
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            (d / "2_MMA_March_strain.csv").write_text(
                "subject,90PercStrain,CCB\nMMA1,0.10,0.09\n")
            scan = imp.scan_sources(d)
            db = d / "dummy.duckdb"
            c = sqlite3.connect(db)
            c.executescript('''
                CREATE TABLE impacts(impact_uid TEXT, dataset_id TEXT, source_impact_id TEXT, source_sample_id TEXT);
                CREATE TABLE simulation_runs(simulation_uid TEXT,impact_uid TEXT,model_name TEXT,model_version TEXT);
                CREATE TABLE brain_metric_results(brain_metric_uid TEXT PRIMARY KEY,simulation_uid TEXT,roi_name TEXT,metric_name TEXT,summary_method TEXT,"value" REAL,unit TEXT,source_artifact_uid TEXT,extraction_version TEXT,notes TEXT);
                INSERT INTO impacts VALUES('IMP_DS002_MMA1','DS002',NULL,NULL);
                INSERT INTO simulation_runs VALUES('SIM1','IMP_DS002_MMA1','IC FE','1');
            ''')
            c.close()
            fake = SimpleNamespace(connect=lambda path, read_only=False: sqlite3.connect(path))
            with mock.patch.dict(sys.modules, {"duckdb": fake}):
                plan, counts, issues, new_sims = imp.plan_db(scan, db, model_name="IC FE")
                self.assertEqual(len(plan), 2)
                self.assertEqual(counts["ready_to_insert"], 2)
                self.assertFalse(issues)
                self.assertFalse(new_sims)
                self.assertEqual(imp.do_apply(db, plan, new_sims, False, counts, [], [], False), 2)
                plan2, counts2, issues2, new_sims2 = imp.plan_db(scan, db, model_name="IC FE")
                self.assertEqual(len(plan2), 0)
                self.assertEqual(counts2["already_present"], 2)
                self.assertFalse(issues2)
                self.assertFalse(new_sims2)


    def test_registration_explicit_safe_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            (d / "2_MMA_March_strain.csv").write_text(
                "subject,90PercStrain,90PercStrainRate,CCB\nMMA1,0.10,0.022,0.09\n")
            scan = imp.scan_sources(d, "1/ms")
            self.assertEqual(len(scan.records), 3)
            db = d / "dummy.duckdb"
            c = sqlite3.connect(db)
            c.executescript('''
                CREATE TABLE impacts(impact_uid TEXT, dataset_id TEXT, source_impact_id TEXT, source_sample_id TEXT);
                CREATE TABLE simulation_runs(simulation_uid TEXT PRIMARY KEY,impact_uid TEXT,model_name TEXT,model_version TEXT, simulation_status TEXT, notes TEXT);
                CREATE TABLE brain_metric_results(brain_metric_uid TEXT PRIMARY KEY,simulation_uid TEXT,roi_name TEXT,metric_name TEXT,summary_method TEXT,"value" REAL,unit TEXT,source_artifact_uid TEXT,extraction_version TEXT,notes TEXT);
                INSERT INTO impacts VALUES('IMP_DS002_MMA1','DS002',NULL,NULL);
            ''')
            c.close()
            fake = SimpleNamespace(connect=lambda path, read_only=False: sqlite3.connect(path))
            with mock.patch.dict(sys.modules, {"duckdb": fake}):
                _, statuses0, _, sims0 = imp.plan_db(scan, db, model_name="IC FE model")
                self.assertEqual(statuses0["no_simulation_match"], 3)
                self.assertEqual(sims0, [])
                plan, statuses, issues, sims = imp.plan_db(scan, db, model_name="IC FE model", register_missing_simulations=True)
                self.assertEqual(len(plan), 3)
                self.assertEqual(len(sims), 1)
                self.assertEqual(statuses["simulations_to_register"], 1)
                self.assertEqual(issues, [])
                self.assertEqual(imp.do_apply(db, plan, sims, False, statuses, [], [], False), 3)
                plan2, statuses2, issues2, sims2 = imp.plan_db(scan, db, model_name="IC FE model", register_missing_simulations=True)
                self.assertEqual((len(plan2), len(sims2)), (0, 0))
                self.assertEqual(statuses2["already_present"], 3)
                self.assertFalse(issues2)
                c = sqlite3.connect(db)
                self.assertEqual(c.execute("SELECT count(*) FROM simulation_runs").fetchone()[0], 1)
                self.assertEqual(c.execute("SELECT count(*) FROM brain_metric_results WHERE unit='1/ms'").fetchone()[0], 1)
                c.close()

    def test_registration_never_duplicates_existing_other_model(self):
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            (d / "2_MMA_March_strain.csv").write_text("subject,90PercStrain\nMMA1,0.10\n")
            scan = imp.scan_sources(d)
            db = d / "dummy.duckdb"
            c = sqlite3.connect(db)
            c.executescript('''
                CREATE TABLE impacts(impact_uid TEXT, dataset_id TEXT, source_impact_id TEXT, source_sample_id TEXT);
                CREATE TABLE simulation_runs(simulation_uid TEXT,impact_uid TEXT,model_name TEXT,model_version TEXT);
                CREATE TABLE brain_metric_results(simulation_uid TEXT,roi_name TEXT,metric_name TEXT,summary_method TEXT,"value" REAL,unit TEXT);
                INSERT INTO impacts VALUES('IMP_DS002_MMA1','DS002',NULL,NULL);
                INSERT INTO simulation_runs VALUES('S1','IMP_DS002_MMA1','Other model','1');
            ''')
            c.close()
            fake = SimpleNamespace(connect=lambda path, read_only=False: sqlite3.connect(path))
            with mock.patch.dict(sys.modules, {"duckdb": fake}):
                plan, statuses, issues, sims = imp.plan_db(scan, db, model_name="IC FE model", register_missing_simulations=True)
                self.assertEqual(len(plan), 0)
                self.assertFalse(sims)
                self.assertEqual(statuses["other_simulation_present"], 1)
                with self.assertRaises(SystemExit):
                    imp.do_apply(db, plan, sims, False, statuses, [], [], False)

    def test_rate_percentile_inversions_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            (d / "2_MMA_March_strain.csv").write_text(
                "subject,90PercStrain,90PercStrainRate\nMMA1,0.1,0.04\n")
            (d / "2_Strain_95Perc.csv").write_text(
                "subject,90PercStrain,90PercStrainRate\nMMA1,0.12,0.02\n")
            scan = imp.scan_sources(d, "1/ms")
            self.assertEqual(len(scan.percentile_issues), 1)
            self.assertEqual(scan.percentile_issues[0][3], "MPSR")


if __name__ == "__main__":
    main()
