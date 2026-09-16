"""Unit tests for the occupation-code gate (soc.py).

Pure functions, no I/O, so this runs anywhere: `python3 -m unittest test_soc`
or just `python3 test_soc.py`.
"""
import unittest

from soc import infer_soc, required_salary, sponsorable


class InferSoc(unittest.TestCase):
    def test_health_and_safety_advisor_is_3582(self):
        self.assertEqual(infer_soc("Health and Safety Advisor"), "3582")

    def test_hse_coordinator_is_3582(self):
        self.assertEqual(infer_soc("HSE Coordinator"), "3582")

    def test_process_safety_engineer_is_2125(self):
        self.assertEqual(infer_soc("Process Safety Engineer"), "2125")

    def test_data_analyst_is_3544(self):
        self.assertEqual(infer_soc("Data Analyst"), "3544")

    def test_assistant_project_manager_is_2440(self):
        self.assertEqual(infer_soc("Assistant Project Manager"), "2440")

    def test_unmapped_title_is_none(self):
        # There is no accountant rule in TITLE_MAP, so a real advert with this
        # title infers no code and the gate treats it as "code unknown".
        self.assertIsNone(infer_soc("Trainee Accountant"))

    def test_health_safety_separators_all_hit_3582(self):
        # Real titles use "and", a comma, "&" or "/" between the words; every
        # form must resolve to the closed H&S code, not slip to a later rule.
        for title in ("Health and Safety Audit Manager",
                      "Health & Safety Business Partner",
                      "Health, Safety and Environment Lead",
                      "Health/Safety Officer"):
            self.assertEqual(infer_soc(title), "3582", title)

    def test_hs_compliance_title_not_a_false_positive(self):
        # Guards a real-data false positive: the "compliance" in this H&S title
        # must not win 2482 (sponsorable) ahead of the 3582 DEAD rule.
        self.assertEqual(infer_soc("Head of Health, Safety and Compliance"), "3582")
        self.assertFalse(sponsorable(infer_soc("Head of Health, Safety and Compliance"), 70000))


class Sponsorable(unittest.TestCase):
    def test_health_and_safety_advisor_not_sponsorable(self):
        # 3582 is medium skilled and on neither list -> closed, at any salary.
        self.assertFalse(sponsorable("3582", 55000))
        self.assertFalse(sponsorable(infer_soc("Health and Safety Advisor"), 55000))

    def test_hse_coordinator_not_sponsorable(self):
        self.assertFalse(sponsorable(infer_soc("HSE Coordinator"), 60000))

    def test_process_safety_engineer_sponsorable_at_38000(self):
        # 2125 is higher skilled; no bespoke floor, so the 33,400 new-entrant floor applies.
        self.assertTrue(sponsorable("2125", 38000))
        self.assertTrue(sponsorable(infer_soc("Process Safety Engineer"), 38000))

    def test_data_analyst_sponsorable_at_36000_not_at_32000(self):
        # 3544 is on the TSL with a published rate of 34,900.
        self.assertTrue(sponsorable("3544", 36000))
        self.assertFalse(sponsorable("3544", 32000))

    def test_assistant_project_manager_prorated_at_37_hours(self):
        # 2440 floor 39,550 pro-rates to 39,023 at 37h; 39,481 clears it.
        self.assertEqual(required_salary("2440", 37), 39023)
        self.assertTrue(sponsorable("2440", 39481, 37))
        # A figure just under the pro-rated floor fails.
        self.assertFalse(sponsorable("2440", 39000, 37))

    def test_trainee_accountant_3533_not_sponsorable_at_28000(self):
        # 3533 is a finance TSL code with a 48,700 rate, so 28,000 is well short.
        self.assertFalse(sponsorable("3533", 28000))

    def test_unknown_salary_passes(self):
        # Unpublished salary must not silently drop an otherwise-eligible role.
        self.assertTrue(sponsorable("2141", None))
        self.assertTrue(sponsorable(infer_soc("Process Safety Engineer"), None))

    def test_dead_and_unmapped_codes_fail(self):
        self.assertFalse(sponsorable("3319", None))   # DEAD
        self.assertFalse(sponsorable(None, 90000))     # no code inferred
        self.assertFalse(sponsorable("9999", 90000))   # not on any list


class RequiredSalary(unittest.TestCase):
    def test_tsl_uses_published_rate(self):
        self.assertEqual(required_salary("3544"), 34900)
        self.assertEqual(required_salary("3533"), 48700)

    def test_higher_without_bespoke_floor_uses_general_floor(self):
        self.assertEqual(required_salary("2125"), 33400)

    def test_higher_with_bespoke_floor_full_hours(self):
        self.assertEqual(required_salary("2440"), 39550)

    def test_prorating_never_below_33400(self):
        # A short week on a modest higher-skilled floor clamps up to 33,400.
        self.assertEqual(required_salary("2482", 20), 33400)

    def test_unmapped_code_returns_none(self):
        self.assertIsNone(required_salary("3582"))     # DEAD, on no list
        self.assertIsNone(required_salary("0000"))


class GateSemantics(unittest.TestCase):
    """soc_fields is the policy layer over soc.sponsorable. Only a code we can
    affirmatively call closed (or below its floor) greys a row; an unmapped
    title defers to the advert on every tab (jobs/hs failed closed until
    2026-09-12; the NHS tab always deferred)."""

    def setUp(self):
        import monitor
        self.monitor = monitor
        self.soc_fields = monitor.soc_fields

    def test_closed_code_still_greys(self):
        code, ok, reason = self.soc_fields("Health and Safety Advisor", 60000)
        self.assertEqual(code, "3582")
        self.assertFalse(ok)
        self.assertEqual(reason, "code closed: 3582")

    def test_unmapped_title_defers_to_advert(self):
        for title in ("Trainee Accountant",                 # broad-sweep jobs row
                      "Quality and Safety Lead",            # hs-shaped, no rule
                      "Widget Calibration Lead"):           # genuinely off-map
            code, ok, reason = self.soc_fields(title, 40000)
            self.assertEqual(code, "", title)
            self.assertTrue(ok, title)                # not rejected on the code alone
            self.assertEqual(reason, self.monitor.SOC_DEFER, title)

    def test_below_floor_still_greys_a_mapped_code(self):
        # Deferral is for missing codes only; a real code is judged as before.
        self.assertEqual(self.soc_fields("Data Analyst", 32000),
                         ("3544", False, "below floor £34900"))
        self.assertEqual(self.soc_fields("Data Analyst", 36000),
                         ("3544", True, "shortage list"))

    def test_code_unknown_is_never_written_any_more(self):
        # The legacy fail-closed string must not reappear, or the backfill's
        # stale-row migration would loop on it.
        for title in ("Trainee Accountant", "", "Zzz Nonsense Role"):
            self.assertNotEqual(self.soc_fields(title, None)[2], "code unknown")


class ClinicalGate(unittest.TestCase):
    """NHS clinical occupations: eligible professions map to their SOC code and
    are sponsorable on the code alone (pay deferred to the advert, since NHS pay
    is on a national scale soc.py does not model); RQF 3-5 support roles map to a
    closed code and grey. gov.uk verified 2026-09-16."""

    def setUp(self):
        import monitor, soc
        self.soc_fields = monitor.soc_fields
        self.soc, self.monitor = soc, monitor

    def test_eligible_professions_map_and_defer_pay(self):
        cases = {
            "Staff Nurse": "2237", "Registered Mental Health Nurse": "2235",
            "Community Midwife": "2231", "Specialty Doctor - Haematology": "2212",
            "Locum Consultant Psychiatrist": "2212", "Salaried GP": "2211",
            "Band 6 Physiotherapist": "2221", "Occupational Therapist": "2222",
            "Highly Specialist Pharmacist": "2251", "MRI Radiographer": "2254",
            "Paramedic": "2255", "Biomedical Scientist - Immunology": "2113",
            "Clinical Psychologist": "2225", "Operating Department Practitioner": "2259",
        }
        for title, code in cases.items():
            soc_code, ok, reason = self.soc_fields(title, None)
            self.assertEqual(soc_code, code, title)
            self.assertTrue(ok, title)                       # eligible on the code
            self.assertEqual(reason, "clinical role, pay set by the advert", title)

    def test_clinical_stays_eligible_even_below_general_floor(self):
        # A band-3/4 clinical wage must NOT grey the row: pay is deferred, not
        # floor-checked, so a low advertised figure cannot rule it out.
        code, ok, reason = self.soc_fields("Staff Nurse", 26000)
        self.assertEqual((code, ok), ("2237", True))
        self.assertEqual(reason, "clinical role, pay set by the advert")

    def test_support_roles_are_closed(self):
        for title, code in {"Healthcare Assistant": "6131",
                            "Nursing Auxiliary": "6131",
                            "Clinical Support Worker": "6131",
                            "Phlebotomist": "6131",
                            "Pharmacy Technician": "3212",
                            "Dental Nurse": "3213",
                            "Care Worker": "6135",
                            "Senior Care Worker": "6135"}.items():
            soc_code, ok, reason = self.soc_fields(title, 40000)
            self.assertEqual(soc_code, code, title)
            self.assertFalse(ok, title)                      # RQF 3-5, not on a list
            self.assertIn("code closed", reason)

    def test_british_speciality_spelling_maps(self):
        # NHS adverts overwhelmingly write "Speciality Doctor" (extra i).
        self.assertEqual(self.soc_fields("Speciality Doctor", None)[0], "2212")
        self.assertEqual(self.soc_fields("Speciality Registrar", None)[0], "2212")

    def test_nursing_associate_defers_not_closed(self):
        # Ambiguous registered band-4 role: left to the advert, not asserted closed.
        code, ok, reason = self.soc_fields("Nursing Associate", 40000)
        self.assertEqual(code, "")
        self.assertTrue(ok)
        self.assertEqual(reason, self.monitor.SOC_DEFER)

    def test_support_beats_profession_in_ordering(self):
        # The closed support pattern must win over the eligible one it contains.
        self.assertFalse(self.soc_fields("Nursing Assistant", 40000)[1])   # not a "nurse"
        self.assertFalse(self.soc_fields("Pharmacy Technician", 40000)[1]) # not a "pharmacist"
        self.assertFalse(self.soc_fields("Dental Nurse", 40000)[1])        # not a "dentist"/"nurse"

    def test_every_clinical_code_is_placed_in_exactly_one_set(self):
        sets = (self.soc.HIGHER, self.soc.TSL, self.soc.DEAD,
                self.soc.CLINICAL, self.soc.MEDIUM_CLOSED)
        for _, code in self.soc.TITLE_MAP:
            hits = sum(code in s for s in sets)
            self.assertEqual(hits, 1, "%s in %d sets" % (code, hits))

    def test_non_clinical_titles_are_untouched(self):
        # The clinical block must not capture engineering/design/data titles.
        self.assertEqual(self.soc_fields("Process Engineer", 50000)[0], "2125")
        self.assertEqual(self.soc_fields("UX Designer", 50000)[0], "2141")
        self.assertEqual(self.soc_fields("Data Analyst", 40000)[0], "3544")
        self.assertEqual(self.soc_fields("Management Consultant", 60000)[0], "2431")


class BackfillMigration(unittest.TestCase):
    """backfill_soc must fill untagged rows and migrate stale 'code unknown'
    rows, while never touching a code-based verdict from write time (those were
    judged on the band top, which the archive does not keep)."""

    def setUp(self):
        import monitor, tempfile, json, os
        self.monitor, self.json, self.os = monitor, json, os
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_dir = monitor.DATA_DIR
        monitor.DATA_DIR = self.tmp.name
        self.path = os.path.join(self.tmp.name, "2026-08-01.json")
        self.day = {
            "date": "2026-08-01",
            "jobs": [
                # untagged, predates the gate
                {"title": "Process Engineer", "salary": 40000},
                {"title": "Trainee Accountant", "salary": 28000},
                # stale fail-closed verdicts (both spellings the archive holds)
                {"title": "Trainee Accountant", "salary": 28000,
                 "soc": "", "sponsorable": False, "soc_reason": "code unknown"},
                {"title": "Warehouse Operative", "salary": None,
                 "soc": None, "sponsorable": False, "soc_reason": "code unknown"},
                # code-based verdicts from write time: judged on a band top the
                # archive lacks, so they MUST survive untouched even where a
                # recompute from the stored minimum would disagree.
                {"title": "Data Analyst", "salary": 30000,
                 "soc": "3544", "sponsorable": True, "soc_reason": "shortage list"},
                {"title": "Business Analyst", "salary": 34000,
                 "soc": "2431", "sponsorable": False, "soc_reason": "below floor £35140"},
            ],
            "hs": [{"title": "Health and Safety Manager", "salary": 50000}],
            "nhs": [
                # deferred SOC ? whose title now maps to a clinical code: the
                # new TITLE_MAP rule must reach it on the next backfill.
                {"title": "Biomedical Scientist", "salary": 35000,
                 "soc": "", "sponsorable": True,
                 "soc_reason": "code not inferred — check the advert"},
                # deferred SOC ? that still maps to nothing: stays deferred.
                {"title": "Ward Clerk", "salary": 23000,
                 "soc": "", "sponsorable": True,
                 "soc_reason": "code not inferred — check the advert"},
            ],
            "phd": [{"title": "PhD in Catalysis", "stipend": 19000}],
        }
        with open(self.path, "w") as f:
            json.dump(self.day, f)

    def tearDown(self):
        self.monitor.DATA_DIR = self.orig_dir
        self.tmp.cleanup()

    def run_backfill(self):
        self.monitor.backfill_soc()
        with open(self.path) as f:
            return self.json.load(f)

    def test_fills_migrates_and_preserves(self):
        out = self.run_backfill()
        jobs = out["jobs"]
        defer = self.monitor.SOC_DEFER
        # untagged rows are tagged by the current rules
        self.assertEqual((jobs[0]["soc"], jobs[0]["sponsorable"], jobs[0]["soc_reason"]),
                         ("2125", True, "higher skilled"))
        self.assertEqual((jobs[1]["soc"], jobs[1]["sponsorable"], jobs[1]["soc_reason"]),
                         ("", True, defer))
        # stale fail-closed rows are migrated to deferral, None soc normalised
        for r in (jobs[2], jobs[3]):
            self.assertEqual((r["soc"], r["sponsorable"], r["soc_reason"]), ("", True, defer))
        # code-based verdicts are preserved exactly, even the "wrong-looking" ones
        self.assertEqual((jobs[4]["soc"], jobs[4]["sponsorable"], jobs[4]["soc_reason"]),
                         ("3544", True, "shortage list"))
        self.assertEqual((jobs[5]["soc"], jobs[5]["sponsorable"], jobs[5]["soc_reason"]),
                         ("2431", False, "below floor £35140"))
        # hs filled; a deferred clinical NHS row is now mapped by the new rule;
        # a still-unmapped NHS row stays deferred; phd untouched
        self.assertEqual(out["hs"][0]["soc_reason"], "code closed: 3582")
        self.assertEqual((out["nhs"][0]["soc"], out["nhs"][0]["sponsorable"],
                          out["nhs"][0]["soc_reason"]),
                         ("2113", True, "clinical role, pay set by the advert"))
        self.assertEqual(out["nhs"][1], self.day["nhs"][1])
        self.assertEqual(out["phd"][0], self.day["phd"][0])
        # nothing else on a row was disturbed
        self.assertEqual(jobs[5]["salary"], 34000)

    def test_second_pass_is_a_noop(self):
        first = self.run_backfill()
        second = self.run_backfill()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main(verbosity=2)
