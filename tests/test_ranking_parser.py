from pathlib import Path
import unittest

from ranking_parser import build_near_nomination_list, parse_page


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_FILE = WORKSPACE_ROOT / "SEFAZ PR.txt"


class RankingParserTest(unittest.TestCase):
    def test_parse_sample_file(self) -> None:
        page = parse_page(SAMPLE_FILE)

        self.assertIsNotNone(page.selected_contest)
        self.assertEqual(page.selected_contest.full_text, "SEFAZ PR Auditor Fiscal")
        self.assertEqual(len(page.contests), 208)
        self.assertEqual(len(page.candidates), 1000)

        named_count = sum(1 for candidate in page.candidates if candidate.named)
        self.assertEqual(named_count, 94)

        last_named, near_candidates = build_near_nomination_list(page.candidates, window=5)
        self.assertEqual(last_named, 347)
        self.assertEqual(len(near_candidates), 5)
        self.assertEqual(near_candidates[0].ranking_position, 348)
        self.assertEqual(near_candidates[0].name, "Edmar Leonardo Nagayama")


if __name__ == "__main__":
    unittest.main()
