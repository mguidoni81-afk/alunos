from pathlib import Path
import unittest

import pandas as pd

from dashboard.adjustments_store import apply_manual_adjustments, load_manual_years, load_nomination_overrides
from dashboard.cockpit import (
    CONTEST_SIGNAL_INSIDE_CUTOFF,
    CONTEST_SIGNAL_NEAR_10,
    CockpitFilters,
    STUDENT_STATE_RECURRENT,
    build_cockpit_model,
)
from dashboard.data_loader import discover_snapshots, load_snapshot_frames
from dashboard.transform import (
    BAND_NOMEADO,
    BAND_QUASE_VARIOS,
    build_opportunity_table,
    build_student_table,
    prepare_snapshot_data,
)


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


def _student(identity_key: str, name: str, contest_count: int) -> dict[str, object]:
    return {
        "identity_key": identity_key,
        "display_name": name,
        "contest_count": contest_count,
        "alias_count": 1,
        "families": "SEFAZ",
        "named_count": 0,
        "inside_vacancies_count": 0,
        "other_results_total": 0,
        "alias_names": name,
        "latest_seen_year": 2025,
        "latest_named_year": 0,
        "recent_2y_contest_count": contest_count,
        "recent_2y_named_count": 0,
        "recent_2y_inside_count": 0,
        "recent_2y_top_50_count": contest_count,
        "recent_2y_best_rank_percentile": 0.02,
        "years_since_latest_seen": 0,
        "years_since_best_result": 0,
        "recent_named_override": False,
        "recent_activity_signal": True,
        "recent_competitive_signal": True,
        "stale_peak_flag": False,
        "recency_profile": "Ativo e competitivo",
        "best_rank_percentile": 0.02,
        "top_10_count": 1,
        "top_50_count": contest_count,
        "contest_family_count": 1,
        "nomination_link_count": 0,
        "best_rank": 5,
        "median_rank": 8,
        "best_final_score": 80,
        "average_final_score": 78,
        "top_100_count": contest_count,
        "top_200_count": contest_count,
    }


def _opportunity(identity_key: str, name: str, contest_value: str, gap: int, named: bool = False) -> dict[str, object]:
    return {
        "identity_key": identity_key,
        "display_name": name,
        "name": name,
        "contest_value": contest_value,
        "contest_name": f"SEFAZ {contest_value}",
        "contest_family": "SEFAZ",
        "contest_year": 2025,
        "rank_percentile": 0.02,
        "delta_to_immediate_vacancies": gap,
        "delta_to_last_named": gap,
        "delta_to_last_inside": gap,
        "inside_vacancies": gap <= 0,
        "named": named,
        "is_open_opportunity": not named,
        "near_pass_band": "Perto" if gap > 10 else "Muito perto",
        "ranking_text": "20º",
        "ranking_position": 20,
        "proximity_score": 1.0,
        "has_nomination_link": False,
        "student_named_elsewhere": 0,
        "student_inside_elsewhere": 0,
        "other_results_count": 99,
        "contest_count": 2,
        "alias_count": 1,
        "families": "SEFAZ",
        "alias_names": name,
        "latest_seen_year": 2025,
        "latest_named_year": 0,
        "recent_2y_contest_count": 2,
        "recent_2y_named_count": 0,
        "recent_2y_inside_count": 0,
        "recent_2y_top_50_count": 2,
        "recent_2y_best_rank_percentile": 0.02,
        "years_since_latest_seen": 0,
        "years_since_best_result": 0,
        "recent_named_override": False,
        "recent_activity_signal": True,
        "recent_competitive_signal": True,
        "stale_peak_flag": False,
        "recency_profile": "Ativo e competitivo",
    }


def _prepared(students: list[dict[str, object]], opportunities: list[dict[str, object]]) -> dict[str, pd.DataFrame]:
    opportunities_df = pd.DataFrame(opportunities)
    if opportunities_df.empty:
        contest_pages = pd.DataFrame()
    else:
        contest_pages = (
            opportunities_df.groupby(["contest_value", "contest_name", "contest_family", "contest_year"], dropna=False)
            .agg(
                candidates_count=("identity_key", "count"),
                named_count=("named", "sum"),
                inside_vacancies_count=("inside_vacancies", "sum"),
            )
            .reset_index()
        )
    return {
        "students": pd.DataFrame(students),
        "opportunities": opportunities_df,
        "quality": {},
        "contest_pages": contest_pages,
        "candidates": pd.DataFrame(),
    }


class CockpitModelTest(unittest.TestCase):
    def test_default_filters_exclude_named(self) -> None:
        prepared = _prepared(
            [_student("a", "Aluno Aberto", 2), _student("n", "Aluno Nomeado", 2)],
            [
                _opportunity("a", "Aluno Aberto", "A", 5),
                _opportunity("a", "Aluno Aberto", "B", 20),
                _opportunity("n", "Aluno Nomeado", "C", 5, named=True),
                _opportunity("n", "Aluno Nomeado", "D", 20, named=True),
            ],
        )

        model = build_cockpit_model(prepared, CockpitFilters())

        self.assertFalse(model["opportunity_table"]["named"].any())
        self.assertEqual(model["entity_table"]["display_name"].tolist(), ["Aluno Aberto"])

    def test_multi_near_contests_promote_recurrent_state(self) -> None:
        prepared = _prepared(
            [_student("a", "Aluno A", 2)],
            [
                _opportunity("a", "Aluno A", "A", 5),
                _opportunity("a", "Aluno A", "B", 20),
            ],
        )

        entity = build_cockpit_model(prepared, CockpitFilters())["entity_table"].iloc[0]

        self.assertEqual(entity["student_state"], STUDENT_STATE_RECURRENT)
        self.assertEqual(entity["priority_lane"], STUDENT_STATE_RECURRENT)
        self.assertEqual(entity["near_contest_count"], 2)
        self.assertEqual(entity["multi_signal_count"], 2)

    def test_inside_cutoff_does_not_dominate_recurrent_state(self) -> None:
        prepared = _prepared(
            [_student("a", "Aluno A", 3)],
            [
                _opportunity("a", "Aluno A", "A", -1),
                _opportunity("a", "Aluno A", "B", 5),
                _opportunity("a", "Aluno A", "C", 20),
            ],
        )

        model = build_cockpit_model(prepared, CockpitFilters())
        entity = model["entity_table"].iloc[0]
        signals = set(model["opportunity_table"]["contest_signal"].tolist())

        self.assertEqual(entity["student_state"], STUDENT_STATE_RECURRENT)
        self.assertIn(CONTEST_SIGNAL_INSIDE_CUTOFF, signals)
        self.assertIn(CONTEST_SIGNAL_NEAR_10, signals)

    def test_min_near_filter_uses_consolidated_student_metric(self) -> None:
        prepared = _prepared(
            [_student("one", "Um Quase", 1), _student("two", "Dois Quases", 2)],
            [
                _opportunity("one", "Um Quase", "A", 5),
                _opportunity("two", "Dois Quases", "B", 5),
                _opportunity("two", "Dois Quases", "C", 25),
            ],
        )

        model = build_cockpit_model(prepared, CockpitFilters(min_near_contests=2, show_inside_open=False))

        self.assertEqual(model["entity_table"]["display_name"].tolist(), ["Dois Quases"])
        self.assertEqual(int(model["entity_table"].iloc[0]["near_contest_count"]), 2)

    def test_visible_opportunity_table_keeps_vertical_detail_rows(self) -> None:
        prepared = _prepared(
            [_student("a", "Aluno A", 2)],
            [
                _opportunity("a", "Aluno A", "A", 5),
                _opportunity("a", "Aluno A", "B", 20),
            ],
        )

        model = build_cockpit_model(prepared, CockpitFilters())

        self.assertEqual(len(model["opportunity_table"]), 2)
        self.assertTrue({"contest_signal", "contest_signal_rank"}.issubset(model["opportunity_table"].columns))

    def test_coverage_distinguishes_scope_filters_and_visible_ranking(self) -> None:
        prepared = _prepared(
            [_student("a", "Aluno A", 2), _student("b", "Aluno B", 1), _student("c", "Aluno C", 1)],
            [
                _opportunity("a", "Aluno A", "A", 5),
                _opportunity("a", "Aluno A", "B", 20),
                _opportunity("b", "Aluno B", "A", 60),
                _opportunity("c", "Aluno C", "A", 200),
            ],
        )

        model = build_cockpit_model(
            prepared,
            CockpitFilters(max_gap=30, max_rank_percentile=1.0, show_inside_open=False),
        )
        row_a = model["coverage_summary"][model["coverage_summary"]["contest_value"].eq("A")].iloc[0]

        self.assertEqual(int(row_a["alunos_no_escopo"]), 3)
        self.assertEqual(int(row_a["alunos_apos_filtros"]), 1)
        self.assertEqual(int(row_a["alunos_no_ranking"]), 1)
        self.assertGreater(int(row_a["alunos_cortados_pelos_filtros"]), 0)
        self.assertIn("filtered_opportunity_table", model)

    def test_selected_contest_remains_in_coverage_when_mode_hides_ranking(self) -> None:
        prepared = _prepared(
            [_student("a", "Aluno A", 2)],
            [
                _opportunity("a", "Aluno A", "A", 5),
                _opportunity("a", "Aluno A", "B", 20),
            ],
        )

        model = build_cockpit_model(
            prepared,
            CockpitFilters(
                selected_contests=("A",),
                max_rank_percentile=1.0,
                show_inside_open=False,
                min_near_contests=2,
            ),
        )
        row_a = model["coverage_summary"][model["coverage_summary"]["contest_value"].eq("A")].iloc[0]

        self.assertTrue(model["entity_table"].empty)
        self.assertEqual(int(row_a["alunos_no_escopo"]), 1)
        self.assertEqual(int(row_a["alunos_apos_filtros"]), 1)
        self.assertEqual(int(row_a["alunos_no_ranking"]), 0)
        self.assertIn("modo exige recorrencia", row_a["leitura_do_recorte"])

    def test_band_label_for_named_is_canonical(self) -> None:
        candidates = pd.DataFrame(
            [
                {
                    "contest_name": "SEFAZ Teste",
                    "contest_value": "T",
                    "name": "Aluno Nomeado",
                    "ranking_text": "1º",
                    "ranking_position": 1,
                    "named": True,
                    "inside_vacancies": True,
                    "other_results_count": 0,
                    "named_in_other_contests": 0,
                    "inside_vacancies_in_other_contests": 0,
                    "final_score": 90,
                    "contest_family": "SEFAZ",
                    "contest_year": 2025,
                    "normalized_name": "aluno nomeado",
                    "identity_key": "aluno nomeado",
                    "has_nomination_link": False,
                    "rank_percentile": 0.01,
                    "top_10": True,
                    "top_50": True,
                    "top_100": True,
                    "top_200": True,
                    "quota_category": "Ampla",
                    "candidates_count": 100,
                }
            ]
        )
        students = build_student_table(candidates, reference_year_override=2025, lightweight=True)

        opportunities = build_opportunity_table(candidates, students)

        self.assertEqual(opportunities.loc[0, "near_pass_band"], BAND_NOMEADO)


class ManualAdjustmentCoverageTest(unittest.TestCase):
    def test_manual_year_seed_covers_current_canonical_snapshot(self) -> None:
        snapshots = discover_snapshots(WORKSPACE_ROOT / "output")
        self.assertTrue(snapshots)
        prepared = prepare_snapshot_data(load_snapshot_frames(snapshots[0]))
        seed_manual_years = pd.read_csv(WORKSPACE_ROOT / "dashboard" / "manual_contest_years_seed.csv")
        manual_years = pd.concat(
            [
                seed_manual_years,
                load_manual_years(WORKSPACE_ROOT / "dashboard_state" / "manual_contest_years.csv"),
            ],
            ignore_index=True,
            sort=False,
        )
        adjusted = apply_manual_adjustments(
            prepared,
            manual_years,
            load_nomination_overrides(WORKSPACE_ROOT / "dashboard_state" / "nomination_overrides.csv"),
        )

        self.assertEqual(int(adjusted["contest_pages"]["contest_year"].isna().sum()), 0)
        self.assertEqual(int(adjusted["candidates"]["contest_year"].isna().sum()), 0)


if __name__ == "__main__":
    unittest.main()
