"""Tests for the ATS adapters (ats.py) and the companyBoards.json map. No network:
the adapters are exercised only for their dispatch and failure behaviour; live
fetching is covered by the pipeline run, not here."""
import json
import os
import unittest

import ats

DATA = os.path.join(os.path.dirname(__file__), "data")
KNOWN_ATS = set(ats._ADAPTERS) | {"workday"}


class Dispatch(unittest.TestCase):
    def test_unknown_ats_returns_empty(self):
        self.assertEqual(ats.fetch_board({"ats": "nonesuch", "slug": "x"}), [])

    def test_workday_without_coordinates_returns_empty(self):
        self.assertEqual(ats.fetch_board({"ats": "workday", "slug": "x"}), [])
        self.assertEqual(ats.workday({"host": "h", "tenant": "t"}), [])   # missing site

    def test_html_is_stripped(self):
        self.assertEqual(ats._text("<p>Hi <b>there</b></p>"), "Hi there")


class CompanyBoards(unittest.TestCase):
    """The ATS map: every entry must name a company that exists, use a known ATS,
    and carry the coordinates its adapter needs. A malformed board would silently
    yield nothing, so pin the shape."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(DATA, "companyBoards.json")) as f:
            cls.boards = json.load(f)
        with open(os.path.join(DATA, "companies.json")) as f:
            cls.company_names = {c["name"] for c in json.load(f)}

    def test_keys_are_real_companies(self):
        for name in self.boards:
            if name.startswith("_"):
                continue
            self.assertIn(name, self.company_names, name)

    def test_every_board_is_well_formed(self):
        for name, blist in self.boards.items():
            if name.startswith("_"):
                continue
            self.assertIsInstance(blist, list, name)
            self.assertTrue(blist, name)
            for b in blist:
                self.assertIn(b.get("ats"), KNOWN_ATS, "%s: %s" % (name, b.get("ats")))
                if b["ats"] == "workday":
                    for k in ("host", "tenant", "site"):
                        self.assertTrue(b.get(k), "%s workday missing %s" % (name, k))
                else:
                    self.assertTrue(b.get("slug"), "%s missing slug" % name)

    def test_at_least_some_companies_mapped(self):
        real = [k for k in self.boards if not k.startswith("_")]
        self.assertGreaterEqual(len(real), 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
