from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from dashboard.calibration import blend_entity_with_student_backtest
from dashboard.scoring import DEFAULT_PROXIMITY_WEIGHTS, DEFAULT_WEIGHTS, compute_opportunity_scores, compute_student_scores
from dashboard.transform import (
    BAND_FORTE_SINAL,
    BAND_MONITORAR,
    BAND_MUITO_PERTO,
    BAND_NAS_VAGAS,
    BAND_PERTO,
    BAND_QUASE_VARIOS,
    OPEN_SIGNAL_BANDS,
    build_entity_proximity_table,
)


ANALYSIS_QUASE_VARIOS = "Quase em vários"
ANALYSIS_MAIS_PERTO = "Mais perto agora"
ANALYSIS_TODOS = "Todos competitivos"
ANALYSIS_MODES = (ANALYSIS_QUASE_VARIOS, ANALYSIS_MAIS_PERTO, ANALYSIS_TODOS)

STUDENT_STATE_RECURRENT = "Quase recorrente"
STUDENT_STATE_INSIDE_CUTOFF = "Dentro do corte em algum concurso"
STUDENT_STATE_VERY_CLOSE = "Muito perto"
STUDENT_STATE_CLOSE = "Perto"
STUDENT_STATE_STRONG_WITHOUT_CUTOFF = "Competitivo sem corte"
STUDENT_STATE_RECENT_NAMED = "Nomeado recente"
STUDENT_STATE_STALE = "Histórico antigo"
STUDENT_STATE_MONITOR = "Monitorar"

CONTEST_SIGNAL_INSIDE_CUTOFF = "Dentro do corte"
CONTEST_SIGNAL_NEAR_10 = "Quase +1 a +10"
CONTEST_SIGNAL_NEAR_30 = "Quase +11 a +30"
CONTEST_SIGNAL_STRONG_TOP = "Top forte sem corte"
CONTEST_SIGNAL_MONITOR = "Monitorar"

STUDENT_STATE_ORDER = {
    STUDENT_STATE_RECURRENT: 0,
    STUDENT_STATE_INSIDE_CUTOFF: 1,
    STUDENT_STATE_VERY_CLOSE: 2,
    STUDENT_STATE_CLOSE: 3,
    STUDENT_STATE_STRONG_WITHOUT_CUTOFF: 4,
    STUDENT_STATE_RECENT_NAMED: 5,
    STUDENT_STATE_STALE: 6,
    STUDENT_STATE_MONITOR: 7,
}

CONTEST_SIGNAL_ORDER = {
    CONTEST_SIGNAL_INSIDE_CUTOFF: 0,
    CONTEST_SIGNAL_NEAR_10: 1,
    CONTEST_SIGNAL_NEAR_30: 2,
    CONTEST_SIGNAL_STRONG_TOP: 3,
    CONTEST_SIGNAL_MONITOR: 4,
}

PRIORITY_ORDER = STUDENT_STATE_ORDER


@dataclass(frozen=True)
class CockpitFilters:
    analysis_mode: str = ANALYSIS_QUASE_VARIOS
    selected_years: tuple[int, ...] = ()
    selected_families: tuple[str, ...] = ()
    selected_contests: tuple[str, ...] = ()
    search_text: str = ""
    max_gap: int = 100
    max_rank_percentile: float = 0.20
    min_near_contests: int = 2
    exclude_named: bool = True
    show_inside_open: bool = True
    only_recent_active: bool = False


def _tupled(values: tuple | list | set | None) -> tuple:
    if values is None:
        return ()
    return tuple(values)


def _series_or_default(frame: pd.DataFrame, column: str, default: object) -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series([default] * len(frame), index=frame.index)


def _identity_contest_counts(frame: pd.DataFrame, mask: pd.Series, column_name: str) -> pd.DataFrame:
    if frame.empty or not mask.any():
        return pd.DataFrame(columns=["identity_key", column_name])
    return (
        frame.loc[mask]
        .groupby("identity_key")["contest_value"]
        .nunique()
        .reset_index(name=column_name)
    )


def add_contest_signal_fields(opportunities: pd.DataFrame) -> pd.DataFrame:
    working = opportunities.copy()
    if working.empty:
        working["contest_signal"] = pd.Series(dtype="object")
        working["contest_signal_rank"] = pd.Series(dtype="int64")
        return working

    gap = pd.to_numeric(_series_or_default(working, "delta_to_immediate_vacancies", pd.NA), errors="coerce")
    open_mask = _series_or_default(working, "is_open_opportunity", True).fillna(True)
    named_mask = _series_or_default(working, "named", False).fillna(False)
    open_mask = open_mask & ~named_mask
    rank_percentile = pd.to_numeric(_series_or_default(working, "rank_percentile", 1.0), errors="coerce").fillna(1.0)

    working["contest_signal"] = CONTEST_SIGNAL_MONITOR
    working.loc[
        open_mask & gap.isna() & rank_percentile.le(0.05),
        "contest_signal",
    ] = CONTEST_SIGNAL_STRONG_TOP
    working.loc[
        open_mask & gap.between(11, 30, inclusive="both"),
        "contest_signal",
    ] = CONTEST_SIGNAL_NEAR_30
    working.loc[
        open_mask & gap.between(1, 10, inclusive="both"),
        "contest_signal",
    ] = CONTEST_SIGNAL_NEAR_10
    working.loc[
        open_mask
        & (
            gap.le(0).fillna(False)
            | _series_or_default(working, "inside_vacancies", False).fillna(False)
        ),
        "contest_signal",
    ] = CONTEST_SIGNAL_INSIDE_CUTOFF
    working["contest_signal_rank"] = (
        working["contest_signal"]
        .map(CONTEST_SIGNAL_ORDER)
        .fillna(CONTEST_SIGNAL_ORDER[CONTEST_SIGNAL_MONITOR])
        .astype(int)
    )
    return working


def apply_cockpit_filters(opportunities: pd.DataFrame, filters: CockpitFilters) -> pd.DataFrame:
    working = opportunities.copy()
    if working.empty:
        return working

    selected_years = {int(year) for year in _tupled(filters.selected_years)}
    selected_families = {str(family) for family in _tupled(filters.selected_families)}
    selected_contests = {str(contest) for contest in _tupled(filters.selected_contests)}

    if selected_years and "contest_year" in working.columns:
        working = working[working["contest_year"].fillna(-1).astype(int).isin(selected_years)]
    if selected_families and "contest_family" in working.columns:
        working = working[working["contest_family"].astype(str).isin(selected_families)]
    if selected_contests and "contest_value" in working.columns:
        working = working[working["contest_value"].astype(str).isin(selected_contests)]

    rank_percentile = pd.to_numeric(_series_or_default(working, "rank_percentile", 1.0), errors="coerce").fillna(1.0)
    working = working[rank_percentile.le(float(filters.max_rank_percentile))]

    gap = pd.to_numeric(_series_or_default(working, "delta_to_immediate_vacancies", pd.NA), errors="coerce")
    working = working[gap.isna() | gap.le(int(filters.max_gap))]

    if filters.exclude_named and "named" in working.columns:
        recent_named = _series_or_default(working, "recent_named_override", False).fillna(False)
        working = working[~working["named"].fillna(False) & ~recent_named]

    if not filters.show_inside_open:
        gap = pd.to_numeric(_series_or_default(working, "delta_to_immediate_vacancies", pd.NA), errors="coerce")
        working = working[gap.isna() | gap.gt(0)]

    if filters.only_recent_active:
        recent_count = pd.to_numeric(_series_or_default(working, "recent_2y_contest_count", 0), errors="coerce").fillna(0)
        working = working[recent_count.gt(0)]

    query = filters.search_text.strip().lower()
    if query:
        search_blob = (
            _series_or_default(working, "display_name", "").astype(str)
            + " "
            + _series_or_default(working, "name", "").astype(str)
            + " "
            + _series_or_default(working, "contest_name", "").astype(str)
        ).str.lower()
        working = working[search_blob.str.contains(query, regex=False, na=False)]

    return working.copy()


def apply_cockpit_scope_filters(opportunities: pd.DataFrame, filters: CockpitFilters) -> pd.DataFrame:
    """Apply only the filters that define which contests are in scope."""
    working = opportunities.copy()
    if working.empty:
        return working

    selected_years = {int(year) for year in _tupled(filters.selected_years)}
    selected_families = {str(family) for family in _tupled(filters.selected_families)}
    selected_contests = {str(contest) for contest in _tupled(filters.selected_contests)}

    if selected_years and "contest_year" in working.columns:
        working = working[working["contest_year"].fillna(-1).astype(int).isin(selected_years)]
    if selected_families and "contest_family" in working.columns:
        working = working[working["contest_family"].astype(str).isin(selected_families)]
    if selected_contests and "contest_value" in working.columns:
        working = working[working["contest_value"].astype(str).isin(selected_contests)]

    query = filters.search_text.strip().lower()
    if query:
        search_blob = (
            _series_or_default(working, "display_name", "").astype(str)
            + " "
            + _series_or_default(working, "name", "").astype(str)
            + " "
            + _series_or_default(working, "contest_name", "").astype(str)
        ).str.lower()
        working = working[search_blob.str.contains(query, regex=False, na=False)]

    return working.copy()


def add_cockpit_entity_fields(
    entity_table: pd.DataFrame,
    scored_opportunities: pd.DataFrame,
    min_near_contests: int = 2,
) -> pd.DataFrame:
    entity = entity_table.copy()
    if entity.empty:
        for column, default in [
            ("near_contest_count", 0),
            ("inside_open_contest_count", 0),
            ("multi_signal_count", 0),
            ("evidence_count", 0),
            ("best_open_gap", pd.NA),
            ("best_near_gap", pd.NA),
            ("student_state", STUDENT_STATE_MONITOR),
            ("state_rank", STUDENT_STATE_ORDER[STUDENT_STATE_MONITOR]),
            ("priority_lane", STUDENT_STATE_MONITOR),
            ("priority_rank", STUDENT_STATE_ORDER[STUDENT_STATE_MONITOR]),
            ("why_ranked", ""),
            ("how_classified", ""),
        ]:
            entity[column] = default
        return entity

    opportunities = scored_opportunities.copy()
    gap = pd.to_numeric(_series_or_default(opportunities, "delta_to_immediate_vacancies", pd.NA), errors="coerce")
    open_mask = _series_or_default(opportunities, "is_open_opportunity", True).fillna(True)
    named_mask = _series_or_default(opportunities, "named", False).fillna(False)
    open_mask = open_mask & ~named_mask
    near_mask = open_mask & gap.between(1, 30, inclusive="both")
    inside_mask = open_mask & (
        gap.le(0).fillna(False)
        | _series_or_default(opportunities, "inside_vacancies", False).fillna(False)
    )
    useful_mask = open_mask & (
        near_mask
        | inside_mask
        | _series_or_default(opportunities, "near_pass_band", "").isin(OPEN_SIGNAL_BANDS)
    )

    for column_name, mask in [
        ("near_contest_count", near_mask),
        ("inside_open_contest_count", inside_mask),
        ("multi_signal_count", useful_mask),
    ]:
        entity = entity.merge(
            _identity_contest_counts(opportunities, mask, column_name),
            on="identity_key",
            how="left",
        )
        entity[column_name] = entity[column_name].fillna(0).astype(int)

    best_gap = (
        opportunities.loc[open_mask & gap.notna()]
        .assign(_gap=gap[open_mask & gap.notna()])
        .groupby("identity_key")["_gap"]
        .min()
        .reset_index(name="best_open_gap")
    )
    entity = entity.merge(best_gap, on="identity_key", how="left")
    entity["best_open_gap"] = entity["best_open_gap"].combine_first(entity.get("best_delta_current", pd.Series(pd.NA, index=entity.index)))
    best_near_gap = (
        opportunities.loc[near_mask & gap.notna()]
        .assign(_gap=gap[near_mask & gap.notna()])
        .groupby("identity_key")["_gap"]
        .min()
        .reset_index(name="best_near_gap")
    )
    entity = entity.merge(best_near_gap, on="identity_key", how="left")

    entity["evidence_count"] = entity["multi_signal_count"]
    entity["student_state"] = STUDENT_STATE_MONITOR
    entity.loc[entity["stale_peak_flag"].fillna(False), "student_state"] = STUDENT_STATE_STALE
    entity.loc[entity["best_band"].eq(BAND_FORTE_SINAL), "student_state"] = STUDENT_STATE_STRONG_WITHOUT_CUTOFF
    entity.loc[entity["best_open_gap"].between(11, 30, inclusive="both"), "student_state"] = STUDENT_STATE_CLOSE
    entity.loc[entity["best_open_gap"].between(1, 10, inclusive="both"), "student_state"] = STUDENT_STATE_VERY_CLOSE
    entity.loc[entity["inside_open_contest_count"].gt(0), "student_state"] = STUDENT_STATE_INSIDE_CUTOFF
    entity.loc[entity["near_contest_count"].ge(int(min_near_contests)), "student_state"] = STUDENT_STATE_RECURRENT
    entity.loc[entity["recent_named_override"].fillna(False), "student_state"] = STUDENT_STATE_RECENT_NAMED
    entity["state_rank"] = (
        entity["student_state"]
        .map(STUDENT_STATE_ORDER)
        .fillna(STUDENT_STATE_ORDER[STUDENT_STATE_MONITOR])
        .astype(int)
    )
    entity["priority_lane"] = entity["student_state"]
    entity["priority_rank"] = entity["state_rank"]

    def explain(row: pd.Series) -> str:
        near = int(row.get("near_contest_count", 0) or 0)
        inside = int(row.get("inside_open_contest_count", 0) or 0)
        recent = int(row.get("recent_2y_contest_count", 0) or 0)
        gap_value = row.get("best_open_gap")
        near_gap_value = row.get("best_near_gap")
        if near >= int(min_near_contests):
            inside_text = f", {inside} dentro do corte" if inside else ""
            return f"{near} concursos no quase (menor {format_gap(near_gap_value)}){inside_text}, {recent} recentes."
        if inside > 0:
            return f"Dentro do corte em {inside} concurso(s), sem nomeação marcada."
        if pd.notna(gap_value):
            return f"Menor gap {format_gap(gap_value)} em {row.get('best_contest_name', '')}."
        if row.get("student_state") == STUDENT_STATE_STRONG_WITHOUT_CUTOFF:
            return "Top 5%, mas concurso sem corte confiável."
        return f"Sinal competitivo em {row.get('best_contest_name', '')}."

    entity["why_ranked"] = entity.apply(explain, axis=1)
    entity["how_classified"] = entity["why_ranked"]
    return entity.sort_values(
        ["state_rank", "calibrated_radar_score", "evidence_count", "best_open_gap"],
        ascending=[True, False, False, True],
        na_position="last",
    ).reset_index(drop=True)


def apply_entity_mode(entity_table: pd.DataFrame, filters: CockpitFilters) -> pd.DataFrame:
    entity = entity_table.copy()
    if entity.empty:
        return entity

    if filters.analysis_mode == ANALYSIS_QUASE_VARIOS:
        mask = entity["near_contest_count"].ge(int(filters.min_near_contests))
        if filters.show_inside_open:
            mask = mask | entity["inside_open_contest_count"].gt(0)
        entity = entity[mask]
    elif filters.analysis_mode == ANALYSIS_MAIS_PERTO:
        gap = pd.to_numeric(entity["best_open_gap"], errors="coerce")
        entity = entity[gap.notna() & gap.le(int(filters.max_gap))]

    return entity.sort_values(
        ["state_rank", "calibrated_radar_score", "evidence_count", "best_open_gap"],
        ascending=[True, False, False, True],
        na_position="last",
    ).reset_index(drop=True)


def build_contest_signal_summary(scored_opportunities: pd.DataFrame) -> pd.DataFrame:
    if scored_opportunities.empty:
        return pd.DataFrame(
            columns=[
                "contest_value",
                "contest_name",
                "contest_family",
                "contest_year",
                "alunos_no_recorte",
                "linhas_no_recorte",
                "quase",
                "dentro_do_corte",
                "sinais",
                "melhor_delta_vagas",
                "score_medio",
            ]
        )

    working = scored_opportunities.copy()
    keys = ["contest_value", "contest_name", "contest_family", "contest_year"]
    gap = pd.to_numeric(_series_or_default(working, "delta_to_immediate_vacancies", pd.NA), errors="coerce")
    open_mask = _series_or_default(working, "is_open_opportunity", True).fillna(True)
    named_mask = _series_or_default(working, "named", False).fillna(False)
    open_mask = open_mask & ~named_mask
    working["_near_signal"] = open_mask & gap.between(1, 30, inclusive="both")
    working["_inside_signal"] = open_mask & (
        gap.le(0).fillna(False)
        | _series_or_default(working, "inside_vacancies", False).fillna(False)
    )
    working["_useful_signal"] = open_mask & (
        working["_near_signal"]
        | working["_inside_signal"]
        | _series_or_default(working, "near_pass_band", "").isin(OPEN_SIGNAL_BANDS)
    )

    summary = (
        working.groupby(keys, dropna=False)
        .agg(
            alunos_no_recorte=("identity_key", "nunique"),
            linhas_no_recorte=("identity_key", "count"),
            melhor_delta_vagas=("delta_to_immediate_vacancies", "min"),
            score_medio=("proximity_score", "mean"),
        )
        .reset_index()
    )
    for source, column in [
        ("_near_signal", "quase"),
        ("_inside_signal", "dentro_do_corte"),
        ("_useful_signal", "sinais"),
    ]:
        counts = (
            working[working[source]]
            .groupby(keys, dropna=False)["identity_key"]
            .nunique()
            .reset_index(name=column)
        )
        summary = summary.merge(counts, on=keys, how="left")
        summary[column] = summary[column].fillna(0).astype(int)

    return summary.sort_values(
        ["quase", "dentro_do_corte", "sinais", "score_medio", "contest_year"],
        ascending=[False, False, False, False, False],
        na_position="last",
    ).reset_index(drop=True)


def _contest_group_counts(frame: pd.DataFrame, rows_column: str, students_column: str) -> pd.DataFrame:
    keys = ["contest_value", "contest_name", "contest_family", "contest_year"]
    if frame.empty:
        return pd.DataFrame(columns=keys + [rows_column, students_column])

    working = frame.copy()
    for column in keys:
        if column not in working.columns:
            working[column] = pd.NA
    if "identity_key" not in working.columns:
        working["identity_key"] = working.index.astype(str)

    return (
        working.groupby(keys, dropna=False)
        .agg(
            **{
                rows_column: ("identity_key", "count"),
                students_column: ("identity_key", "nunique"),
            }
        )
        .reset_index()
    )


def _rename_contest_summary(summary: pd.DataFrame, suffix: str) -> pd.DataFrame:
    return summary.rename(
        columns={
            "alunos_no_recorte": f"alunos_{suffix}",
            "linhas_no_recorte": f"linhas_{suffix}",
            "quase": f"quase_{suffix}",
            "dentro_do_corte": f"dentro_do_corte_{suffix}",
            "sinais": f"sinais_{suffix}",
            "melhor_delta_vagas": f"menor_gap_{suffix}",
            "score_medio": f"score_medio_{suffix}",
        }
    )


def _coverage_note(row: pd.Series, filters: CockpitFilters) -> str:
    scope_students = int(row.get("alunos_no_escopo", 0) or 0)
    filtered_students = int(row.get("alunos_apos_filtros", 0) or 0)
    ranking_students = int(row.get("alunos_no_ranking", 0) or 0)

    parts = [f"{scope_students} no escopo", f"{filtered_students} apos filtros", f"{ranking_students} no ranking"]
    reasons: list[str] = []
    if filtered_students < scope_students:
        reasons.append(f"Rank <= {filters.max_rank_percentile:.0%} e gap <= {int(filters.max_gap)}")
        if filters.exclude_named:
            reasons.append("nomeados fora")
        if filters.only_recent_active:
            reasons.append("ativos recentes")
    if ranking_students < filtered_students:
        if filters.analysis_mode == ANALYSIS_QUASE_VARIOS:
            reasons.append("modo exige recorrencia consolidada ou dentro do corte")
        elif filters.analysis_mode == ANALYSIS_MAIS_PERTO:
            reasons.append("modo prioriza menor gap atual")
        else:
            reasons.append("linhas sem evidencia suficiente ficam fora da lista visivel")
    if not reasons:
        reasons.append("sem estreitamento relevante")
    return "; ".join(parts) + ". " + "; ".join(reasons) + "."


def build_filter_coverage_summary(
    prepared: dict[str, pd.DataFrame],
    filters: CockpitFilters,
    scope_opportunities: pd.DataFrame,
    filtered_opportunities: pd.DataFrame,
    visible_opportunities: pd.DataFrame,
) -> pd.DataFrame:
    keys = ["contest_value", "contest_name", "contest_family", "contest_year"]
    scope_counts = _contest_group_counts(scope_opportunities, "linhas_no_escopo", "alunos_no_escopo")
    if scope_counts.empty:
        return pd.DataFrame(
            columns=keys
            + [
                "candidates_count",
                "teto_rank_percentual",
                "named_count",
                "inside_vacancies_count",
                "linhas_no_escopo",
                "alunos_no_escopo",
                "linhas_apos_filtros",
                "alunos_apos_filtros",
                "linhas_no_ranking",
                "alunos_no_ranking",
                "quase_apos_filtros",
                "dentro_do_corte_apos_filtros",
                "sinais_apos_filtros",
                "menor_gap_apos_filtros",
                "leitura_do_recorte",
            ]
        )

    for frame in [scope_counts]:
        frame["contest_value"] = frame["contest_value"].astype(str)

    filtered_summary = _rename_contest_summary(build_contest_signal_summary(filtered_opportunities), "apos_filtros")
    visible_summary = _rename_contest_summary(build_contest_signal_summary(visible_opportunities), "no_ranking")
    for frame in [filtered_summary, visible_summary]:
        if not frame.empty and "contest_value" in frame.columns:
            frame["contest_value"] = frame["contest_value"].astype(str)

    filtered_columns = keys + [
        "linhas_apos_filtros",
        "alunos_apos_filtros",
        "quase_apos_filtros",
        "dentro_do_corte_apos_filtros",
        "sinais_apos_filtros",
        "menor_gap_apos_filtros",
        "score_medio_apos_filtros",
    ]
    visible_columns = keys + [
        "linhas_no_ranking",
        "alunos_no_ranking",
        "quase_no_ranking",
        "dentro_do_corte_no_ranking",
        "sinais_no_ranking",
        "menor_gap_no_ranking",
        "score_medio_no_ranking",
    ]
    coverage = scope_counts.merge(
        filtered_summary[[column for column in filtered_columns if column in filtered_summary.columns]],
        on=keys,
        how="left",
    )
    coverage = coverage.merge(
        visible_summary[[column for column in visible_columns if column in visible_summary.columns]],
        on=keys,
        how="left",
    )

    contest_pages = prepared.get("contest_pages", pd.DataFrame()).copy()
    if not contest_pages.empty and "contest_value" in contest_pages.columns:
        contest_pages["contest_value"] = contest_pages["contest_value"].astype(str)
        metadata_columns = [
            "contest_value",
            "candidates_count",
            "named_count",
            "inside_vacancies_count",
        ]
        for column in metadata_columns:
            if column not in contest_pages.columns:
                contest_pages[column] = pd.NA
        metadata = contest_pages[metadata_columns].drop_duplicates("contest_value")
        coverage = coverage.merge(metadata, on="contest_value", how="left")
    else:
        coverage["candidates_count"] = pd.NA
        coverage["named_count"] = pd.NA
        coverage["inside_vacancies_count"] = pd.NA

    integer_columns = [
        "linhas_no_escopo",
        "alunos_no_escopo",
        "linhas_apos_filtros",
        "alunos_apos_filtros",
        "linhas_no_ranking",
        "alunos_no_ranking",
        "quase_apos_filtros",
        "dentro_do_corte_apos_filtros",
        "sinais_apos_filtros",
        "quase_no_ranking",
        "dentro_do_corte_no_ranking",
        "sinais_no_ranking",
        "candidates_count",
        "named_count",
        "inside_vacancies_count",
    ]
    for column in integer_columns:
        if column not in coverage.columns:
            coverage[column] = 0
        coverage[column] = pd.to_numeric(coverage[column], errors="coerce").fillna(0).astype(int)

    for column in ["menor_gap_apos_filtros", "menor_gap_no_ranking", "score_medio_apos_filtros", "score_medio_no_ranking"]:
        if column not in coverage.columns:
            coverage[column] = pd.NA

    base_for_rank_cap = coverage["candidates_count"].where(coverage["candidates_count"].gt(0), coverage["linhas_no_escopo"])
    coverage["teto_rank_percentual"] = (base_for_rank_cap.astype(float) * float(filters.max_rank_percentile)).astype(int)
    coverage.loc[base_for_rank_cap.gt(0) & coverage["teto_rank_percentual"].lt(1), "teto_rank_percentual"] = 1
    coverage["alunos_cortados_pelos_filtros"] = (coverage["alunos_no_escopo"] - coverage["alunos_apos_filtros"]).clip(lower=0)
    coverage["alunos_fora_do_ranking"] = (coverage["alunos_apos_filtros"] - coverage["alunos_no_ranking"]).clip(lower=0)
    coverage["leitura_do_recorte"] = coverage.apply(lambda row: _coverage_note(row, filters), axis=1)

    return coverage.sort_values(
        ["contest_year", "sinais_apos_filtros", "alunos_no_ranking", "alunos_apos_filtros", "contest_name"],
        ascending=[False, False, False, False, True],
        na_position="last",
    ).reset_index(drop=True)


def discover_legacy_sources(output_dir: Path) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if not output_dir.exists():
        return pd.DataFrame(columns=["source", "files", "csv_rows", "status"])

    for path in sorted(output_dir.iterdir()):
        if not path.is_dir():
            continue
        files = [item for item in path.iterdir() if item.is_file()]
        csv_rows = 0
        for csv_path in path.glob("*.csv"):
            try:
                with csv_path.open("rb") as handle:
                    csv_rows += max(sum(1 for _ in handle) - 1, 0)
            except OSError:
                continue
        rows.append(
            {
                "source": path.name,
                "files": len(files),
                "csv_rows": csv_rows,
                "status": "Fonte legada fora do schema canônico",
            }
        )
    return pd.DataFrame(rows, columns=["source", "files", "csv_rows", "status"])


def build_quality_summary(prepared: dict[str, pd.DataFrame], legacy_sources: pd.DataFrame | None = None) -> pd.DataFrame:
    quality = prepared.get("quality", {})
    contests = prepared.get("contest_pages", pd.DataFrame())
    candidates = prepared.get("candidates", pd.DataFrame())
    students = prepared.get("students", pd.DataFrame())
    legacy_count = 0 if legacy_sources is None else len(legacy_sources)
    missing_years = int(contests["contest_year"].isna().sum()) if "contest_year" in contests.columns else 0

    return pd.DataFrame(
        [
            {"Indicador": "Concursos canônicos", "Valor": len(contests), "Leitura": "Entram no cockpit."},
            {"Indicador": "Linhas de candidatos", "Valor": len(candidates), "Leitura": "Base bruta consolidada."},
            {"Indicador": "Alunos consolidados", "Valor": len(students), "Leitura": "Uma linha por identidade."},
            {"Indicador": "Concursos sem ano", "Valor": missing_years, "Leitura": "Deve ficar em zero após ajustes manuais."},
            {"Indicador": "Texto suspeito", "Valor": len(quality.get("suspicious_text", pd.DataFrame())), "Leitura": "Mojibake ainda não corrigido."},
            {"Indicador": "Aliases possíveis", "Valor": len(quality.get("aliases", pd.DataFrame())), "Leitura": "Nomes que merecem revisão."},
            {"Indicador": "Concursos esparsos", "Valor": len(quality.get("sparse_contests", pd.DataFrame())), "Leitura": "Sem nomeados ou sem vagas detectadas."},
            {"Indicador": "Fontes legadas", "Valor": legacy_count, "Leitura": "Existem, mas não entram no cockpit."},
        ]
    )


def format_gap(value: object) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    number = float(value)
    if number <= 0:
        return f"{int(number)} (dentro do corte)"
    return f"+{int(number)}"


def build_cockpit_model(
    prepared: dict[str, pd.DataFrame],
    filters: CockpitFilters,
    score_weights: dict[str, float] | None = None,
    proximity_weights: dict[str, float] | None = None,
    legacy_sources: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    scope_opportunities = apply_cockpit_scope_filters(prepared["opportunities"], filters)
    filtered_opportunities = apply_cockpit_filters(prepared["opportunities"], filters)
    filtered_students = prepared["students"][
        prepared["students"]["identity_key"].isin(filtered_opportunities["identity_key"].unique().tolist())
    ].copy()

    scored_opportunities = compute_opportunity_scores(
        filtered_opportunities,
        proximity_weights or DEFAULT_PROXIMITY_WEIGHTS,
    )
    scored_opportunities = add_contest_signal_fields(scored_opportunities)
    entity_table = build_entity_proximity_table(scored_opportunities)
    student_scores = compute_student_scores(filtered_students, score_weights or DEFAULT_WEIGHTS)
    entity_table = blend_entity_with_student_backtest(entity_table, student_scores)
    entity_table = add_cockpit_entity_fields(entity_table, scored_opportunities, filters.min_near_contests)
    entity_table = apply_entity_mode(entity_table, filters)

    visible_keys = (
        set(entity_table["identity_key"].astype(str).tolist())
        if not entity_table.empty and "identity_key" in entity_table.columns
        else set()
    )
    opportunity_table = scored_opportunities[
        scored_opportunities["identity_key"].astype(str).isin(visible_keys)
    ].copy()
    coverage_summary = build_filter_coverage_summary(
        prepared,
        filters,
        scope_opportunities,
        scored_opportunities,
        opportunity_table,
    )

    return {
        "entity_table": entity_table,
        "opportunity_table": opportunity_table,
        "scope_opportunity_table": scope_opportunities,
        "filtered_opportunity_table": scored_opportunities,
        "contest_signal_summary": build_contest_signal_summary(opportunity_table),
        "coverage_summary": coverage_summary,
        "quality_summary": build_quality_summary(prepared, legacy_sources),
    }
