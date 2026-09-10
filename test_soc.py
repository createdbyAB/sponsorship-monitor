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


if __name__ == "__main__":
    unittest.main(verbosity=2)
