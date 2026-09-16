"""Unit tests for the Company-watch eligibility engine (eligibility.py) and the
seed-data integrity guard. Run: python3 -m unittest test_eligibility."""
import json
import os
import unittest

import eligibility as e

DATA = os.path.join(os.path.dirname(__file__), "data")
OCC, ROLES = e.load(DATA)


class Eligibility(unittest.TestCase):
    """The seven cases the brief pins, each a real decision the gate must get
    right. Salaries are tested at the band MINIMUM."""

    def test_1_band_fails_on_minimum(self):
        # NHS Band 5 £32,073–£39,043 vs 3544: appointment is at the band minimum,
        # which is below the £34,900 shortage-list rate. This cost a real
        # application, so it is the anchor test.
        p = e.parse_salary("£32,073 to £39,043")
        self.assertEqual((p["min"], p["stated"]), (32073, True))
        v = e.evaluate("3544", p, OCC)
        self.assertEqual(v["verdict"], "fail")
        self.assertEqual(v["shortfall"], 2827)
        self.assertIn("£2,827 below the £34,900 rate for code 3544", v["reason"])

    def test_2_flat_passes_with_headroom(self):
        v = e.evaluate("3544", e.parse_salary("£35,000"), OCC)
        self.assertEqual(v["verdict"], "pass")
        self.assertEqual(v["headroom"], 100)

    def test_3_flat_fails_higher_skilled(self):
        v = e.evaluate("2440", e.parse_salary("£35,000"), OCC)
        self.assertEqual(v["verdict"], "fail")
        self.assertEqual(v["shortfall"], 4550)          # 39,550 - 35,000

    def test_4_closed_code_fails_at_any_salary(self):
        for sal in ("£90,000", "£120,000", "competitive"):
            v = e.evaluate("3582", e.parse_salary(sal), OCC)
            self.assertEqual(v["verdict"], "closed", sal)
            self.assertIn("closed occupation code", v["reason"])

    def test_5_part_time_higher_skilled_general_minimum_holds(self):
        # £33,500 at 37h vs a higher-skilled code whose base floor is the general
        # minimum (2111 = £33,400). The floor pro-rates to ~£32,955, but the
        # £33,400 general minimum does NOT pro-rate, so the required figure stays
        # £33,400 and £33,500 passes. Both figures must appear.
        prorated = round(33400 * 37 / 37.5)             # 32,955
        v = e.evaluate("2111", e.parse_salary("£33,500"), OCC, hours=37)
        self.assertEqual(v["verdict"], "pass")
        self.assertEqual(v["requiredFloor"], 33400)
        self.assertEqual(v["proratedFloor"], prorated)
        self.assertEqual(v["generalMinimum"], 33400)
        self.assertIn("£33,400", v["reason"])
        self.assertIn(e._pounds(prorated), v["reason"])
        self.assertEqual(v["headroom"], 100)

    def test_6_absent_salary_is_unverified_never_pass(self):
        for sal in ("competitive", "negotiable", "depending on experience", "", None):
            v = e.evaluate("3544", e.parse_salary(sal or ""), OCC)
            self.assertEqual(v["verdict"], "unverified", repr(sal))
            self.assertNotEqual(v["verdict"], "pass")
            self.assertIn("£34,900", v["reason"])       # shows the floor it would need

    def test_7_future_year_cohort_is_excluded(self):
        self.assertIsNone(e.match_role("2027 Data and AI Full Time Analyst", ROLES, today_year=2026))
        # ... while a plain data analyst in no future cohort still matches.
        m = e.match_role("Data Analyst", ROLES, today_year=2026)
        self.assertEqual(m["soc"], "3544")


class SalaryParsing(unittest.TestCase):
    def test_band_separators(self):
        for txt in ("£32,073–£39,043", "£32,073 - £39,043", "£32,073 to £39,043",
                    "£32,073—£39,043", "between £32,073 and £39,043"):
            p = e.parse_salary(txt)
            self.assertEqual((p["min"], p["max"], p["stated"]), (32073, 39043, True), txt)

    def test_k_suffix_and_single(self):
        self.assertEqual(e.parse_salary("£35k")["min"], 35000)
        p = e.parse_salary("£35,000 per annum")
        self.assertEqual((p["min"], p["max"]), (35000, 35000))

    def test_hourly_annualised(self):
        p = e.parse_salary("£18.00 per hour")
        self.assertEqual(p["period"], "hour")
        self.assertEqual(p["min"], round(18 * 37.5 * 52))   # 35,100
        self.assertIn("annualised", p["note"])

    def test_unverified_words(self):
        for txt in ("Competitive", "Negotiable", "Depending on experience", "DOE"):
            self.assertFalse(e.parse_salary(txt)["stated"], txt)


class RoleMatching(unittest.TestCase):
    def test_risk_analyst_is_3544_not_2431(self):
        self.assertEqual(e.match_role("Risk Analyst", ROLES)["soc"], "3544")

    def test_esg_data_analyst_is_3544(self):
        self.assertEqual(e.match_role("ESG Data Analyst", ROLES)["soc"], "3544")
        self.assertEqual(e.match_role("ESG Analyst", ROLES)["soc"], "2152")

    def test_assistant_project_manager_kept_despite_manager(self):
        m = e.match_role("Assistant Project Manager", ROLES)
        self.assertEqual(m["soc"], "2440")

    def test_seniority_excluded(self):
        for t in ("Senior Data Analyst", "Lead Data Engineer", "Head of Analytics",
                  "Data Analytics Manager", "Principal Designer"):
            self.assertIsNone(e.match_role(t, ROLES), t)

    def test_graduate_and_safety_excluded(self):
        for t in ("Data Analyst Graduate Scheme", "Analyst Internship",
                  "Apprentice Data Analyst", "Health and Safety Analyst",
                  "HSE Data Analyst"):
            self.assertIsNone(e.match_role(t, ROLES), t)

    def test_confidence_flag_present(self):
        self.assertEqual(e.match_role("Data Analyst", ROLES)["confidence"], "established")
        self.assertEqual(e.match_role("Business Analyst", ROLES)["confidence"], "check")


class CompaniesIntegrity(unittest.TestCase):
    """Catches a truncated or half-regenerated companies.json."""

    def setUp(self):
        with open(os.path.join(DATA, "companies.json")) as f:
            self.companies = json.load(f)

    def test_exactly_83_entries(self):
        self.assertEqual(len(self.companies), 83)

    def test_all_verified_against_register(self):
        self.assertTrue(all(c.get("verifiedAgainstRegister") is True for c in self.companies))

    def test_every_company_has_search_aliases(self):
        for c in self.companies:
            self.assertTrue(c.get("searchAliases"), c.get("name"))
            self.assertIsInstance(c["searchAliases"], list)

    def test_cgi_is_absent(self):
        self.assertNotIn("CGI", [c["name"] for c in self.companies])


if __name__ == "__main__":
    unittest.main(verbosity=2)
