from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime
from typing import Iterable

import pandas as pd


MOJIBAKE_MARKERS = ("Ã", "Â", "�")
NAME_STOPWORDS = {"de", "da", "do", "das", "dos", "e"}


def fix_mojibake(value: object) -> object:
    if not isinstance(value, str):
        return value
    if not any(marker in value for marker in MOJIBAKE_MARKERS):
        return value
    try:
        fixed = value.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return fixed


def clean_text(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    value = fix_mojibake(str(value))
    return re.sub(r"\s+", " ", value).strip()


def normalize_name(value: str) -> str:
    value = clean_text(value).lower()
    value = unicodedata.normalize("NFD", value)
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def identity_key(value: str) -> str:
    normalized = normalize_name(value)
    tokens = [token for token in normalized.split() if token not in NAME_STOPWORDS]
    return " ".join(tokens)


def contest_family(value: str) -> str:
    text = clean_text(value)
    if not text:
        return "Sem família"
    return text.split()[0].upper()


def infer_contest_year(value: str) -> float:
    text = clean_text(value)
    if not text:
        return float("nan")

    explicit = re.findall(r"\b(20\d{2})\b", text)
    if explicit:
        return float(max(int(item) for item in explicit))

    short_years = re.findall(r"\b(\d{2})\b", text)
    candidate_years = []
    for token in short_years:
        year = int(token)
        if 10 <= year <= 35:
            candidate_years.append(2000 + year)
    if candidate_years:
        return float(max(candidate_years))
    return float("nan")


def quota_category(value: str) -> str:
    text = clean_text(value)
    match = re.search(r"\(([^)]+)\)", text)
    if match:
        return clean_text(match.group(1))
    return "Sem categoria"


def _reference_year(series: pd.Series) -> int:
    known = pd.to_numeric(series, errors="coerce").dropna()
    current_year = datetime.now().year
    if known.empty:
        return current_year
    return int(known.max())


def _apply_text_cleanup(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    cleaned = df.copy()
    for column in columns:
        if column in cleaned.columns:
            cleaned[column] = cleaned[column].map(clean_text)
    return cleaned


def _series_or_default(frame: pd.DataFrame, column: str, default: object) -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series([default] * len(frame), index=frame.index)


def prepare_snapshot_data(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    selector_contests = _apply_text_cleanup(
        frames["selector_contests"],
        ["value", "full_text", "display_text"],
    )
    contest_pages = _apply_text_cleanup(
        frames["contest_pages"],
        ["contest_name", "contest_value", "source_url", "page_title"],
    )
    candidates = _apply_text_cleanup(
        frames["candidates"],
        [
            "contest_name",
            "contest_value",
            "source_url",
            "page_title",
            "name",
            "ranking_text",
            "other_results_summary",
            "detected_columns",
            "raw_row_text",
            "nomination_link_href",
            "nomination_candidate_name_param",
            "nomination_contest_name_param",
            "nomination_cargo_name_param",
        ],
    )
    other_results = _apply_text_cleanup(
        frames["other_results"],
        [
            "source_contest_name",
            "source_contest_value",
            "source_url",
            "page_title",
            "candidate_name",
            "candidate_ranking_text",
            "target_contest_label",
            "target_contest_value",
            "target_ranking_text",
            "target_href",
        ],
    )

    for column in ["candidates_count", "named_count", "inside_vacancies_count"]:
        contest_pages[column] = pd.to_numeric(contest_pages[column], errors="coerce").fillna(0).astype(int)
    contest_pages["contest_family"] = contest_pages["contest_name"].map(contest_family)
    contest_pages["contest_year"] = contest_pages["contest_name"].map(infer_contest_year)

    numeric_columns = [
        "objective_score",
        "discursive_score",
        "title_score",
        "final_score",
        "ranking_position",
        "other_results_count",
        "named_in_other_contests",
        "inside_vacancies_in_other_contests",
    ]
    for column in numeric_columns:
        if column in candidates.columns:
            candidates[column] = pd.to_numeric(candidates[column], errors="coerce")

    for column in ["named", "inside_vacancies"]:
        if column in candidates.columns:
            candidates[column] = candidates[column].astype(str).str.lower().eq("true")

    candidates["contest_family"] = candidates["contest_name"].map(contest_family)
    candidates["contest_year"] = candidates["contest_name"].map(infer_contest_year)
    candidates["quota_category"] = candidates["ranking_text"].map(quota_category)
    candidates["normalized_name"] = candidates["name"].map(normalize_name)
    candidates["identity_key"] = candidates["name"].map(identity_key)
    candidates["has_nomination_link"] = candidates["nomination_link_href"].astype(str).str.len().gt(0)
    candidates["has_other_results"] = candidates["other_results_count"].fillna(0).gt(0)

    candidates = candidates.merge(
        contest_pages[["contest_value", "candidates_count"]],
        how="left",
        on="contest_value",
        suffixes=("", "_contest"),
    )
    candidates["rank_percentile"] = candidates["ranking_position"] / candidates["candidates_count"].replace({0: pd.NA})
    candidates["rank_percentile"] = candidates["rank_percentile"].astype("float64")
    candidates["top_10"] = candidates["ranking_position"].fillna(10**9).le(10)
    candidates["top_50"] = candidates["ranking_position"].fillna(10**9).le(50)
    candidates["top_100"] = candidates["ranking_position"].fillna(10**9).le(100)
    candidates["top_200"] = candidates["ranking_position"].fillna(10**9).le(200)

    for column in ["target_ranking_position"]:
        if column in other_results.columns:
            other_results[column] = pd.to_numeric(other_results[column], errors="coerce")
    for column in ["target_named", "target_inside_vacancies"]:
        if column in other_results.columns:
            other_results[column] = other_results[column].astype(str).str.lower().eq("true")

    return {
        "selector_contests": selector_contests,
        "contest_pages": contest_pages,
        "candidates": candidates,
        "other_results": other_results,
    }


def build_student_table(
    candidates: pd.DataFrame,
    reference_year_override: int | None = None,
    lightweight: bool = False,
) -> pd.DataFrame:
    reference_year = reference_year_override if reference_year_override is not None else _reference_year(candidates["contest_year"])
    recent_2y_min = reference_year - 2 + 1
    recent_3y_min = reference_year - 3 + 1
    grouped = candidates.groupby("identity_key", dropna=False)

    summary = grouped.agg(
        display_name=("name", lambda s: s.value_counts().index[0] if not s.empty else ""),
        alias_count=("normalized_name", "nunique"),
        contest_count=("contest_value", "count"),
        unique_contest_count=("contest_value", "nunique"),
        contest_family_count=("contest_family", "nunique"),
        named_count=("named", "sum"),
        inside_vacancies_count=("inside_vacancies", "sum"),
        nomination_link_count=("has_nomination_link", "sum"),
        has_nomination_link_any=("has_nomination_link", "max"),
        other_results_total=("other_results_count", "sum"),
        named_in_other_contests_total=("named_in_other_contests", "sum"),
        inside_in_other_contests_total=("inside_vacancies_in_other_contests", "sum"),
        best_rank=("ranking_position", "min"),
        median_rank=("ranking_position", "median"),
        best_rank_percentile=("rank_percentile", "min"),
        median_rank_percentile=("rank_percentile", "median"),
        best_final_score=("final_score", "max"),
        average_final_score=("final_score", "mean"),
        top_10_count=("top_10", "sum"),
        top_50_count=("top_50", "sum"),
        top_100_count=("top_100", "sum"),
        top_200_count=("top_200", "sum"),
        quota_category_count=("quota_category", "nunique"),
    ).reset_index()

    latest_years = (
        candidates.groupby("identity_key")["contest_year"]
        .max()
        .reset_index(name="latest_seen_year")
    )
    latest_named_years = (
        candidates[candidates["named"]]
        .groupby("identity_key")["contest_year"]
        .max()
        .reset_index(name="latest_named_year")
    )
    recent_2y = candidates[candidates["contest_year"].fillna(-1).ge(recent_2y_min)]
    recent_3y = candidates[candidates["contest_year"].fillna(-1).ge(recent_3y_min)]
    recent_2y_summary = (
        recent_2y.groupby("identity_key")
        .agg(
            recent_2y_contest_count=("contest_value", "count"),
            recent_2y_unique_contest_count=("contest_value", "nunique"),
            recent_2y_named_count=("named", "sum"),
            recent_2y_inside_count=("inside_vacancies", "sum"),
            recent_2y_top_50_count=("top_50", "sum"),
            recent_2y_best_rank_percentile=("rank_percentile", "min"),
        )
        .reset_index()
    )
    recent_3y_summary = (
        recent_3y.groupby("identity_key")
        .agg(
            recent_3y_contest_count=("contest_value", "count"),
            recent_3y_top_100_count=("top_100", "sum"),
        )
        .reset_index()
    )
    best_rows = (
        candidates.sort_values(
            ["identity_key", "rank_percentile", "ranking_position", "contest_year"],
            ascending=[True, True, True, False],
            na_position="last",
        )
        .groupby("identity_key", as_index=False)
        .first()[["identity_key", "contest_year", "contest_name"]]
        .rename(columns={"contest_year": "best_result_year", "contest_name": "best_result_contest"})
    )

    students = (
        summary.merge(latest_years, on="identity_key", how="left")
        .merge(latest_named_years, on="identity_key", how="left")
        .merge(recent_2y_summary, on="identity_key", how="left")
        .merge(recent_3y_summary, on="identity_key", how="left")
        .merge(best_rows, on="identity_key", how="left")
    )
    if lightweight:
        students["sample_contests"] = ""
        students["families"] = ""
        students["quota_mix"] = ""
        students["alias_names"] = students["display_name"]
    else:
        sample_contests = (
            candidates.sort_values(["identity_key", "ranking_position"], na_position="last")
            .groupby("identity_key")["contest_name"]
            .apply(lambda series: " | ".join(series.dropna().astype(str).head(5)))
            .reset_index(name="sample_contests")
        )
        sample_families = (
            candidates.groupby("identity_key")["contest_family"]
            .apply(lambda series: " | ".join(series.dropna().astype(str).drop_duplicates().head(5)))
            .reset_index(name="families")
        )
        quota_mix = (
            candidates.groupby("identity_key")["quota_category"]
            .apply(lambda series: " | ".join(series.dropna().astype(str).drop_duplicates().head(5)))
            .reset_index(name="quota_mix")
        )
        alias_names = (
            candidates.groupby("identity_key")["name"]
            .apply(lambda series: " | ".join(series.dropna().astype(str).drop_duplicates().head(5)))
            .reset_index(name="alias_names")
        )
        students = (
            students.merge(sample_contests, on="identity_key", how="left")
            .merge(sample_families, on="identity_key", how="left")
            .merge(quota_mix, on="identity_key", how="left")
            .merge(alias_names, on="identity_key", how="left")
        )

    students["best_rank"] = students["best_rank"].fillna(999999)
    students["median_rank"] = students["median_rank"].fillna(999999)
    students["best_rank_percentile"] = students["best_rank_percentile"].fillna(1.0)
    students["median_rank_percentile"] = students["median_rank_percentile"].fillna(1.0)
    students["best_final_score"] = students["best_final_score"].fillna(0.0)
    students["average_final_score"] = students["average_final_score"].fillna(0.0)
    for column in [
        "recent_2y_contest_count",
        "recent_2y_unique_contest_count",
        "recent_2y_named_count",
        "recent_2y_inside_count",
        "recent_2y_top_50_count",
        "recent_3y_contest_count",
        "recent_3y_top_100_count",
    ]:
        students[column] = students.get(column, 0).fillna(0)
    students["recent_2y_best_rank_percentile"] = students.get("recent_2y_best_rank_percentile", 1.0).fillna(1.0)
    students["has_named_history"] = students["named_count"].gt(0)
    students["has_inside_history"] = students["inside_vacancies_count"].gt(0)
    students["consistency_index"] = students["top_50_count"] * 2 + students["top_100_count"] + students["inside_vacancies_count"] * 3
    students["market_signal_index"] = (
        students["contest_count"] + students["other_results_total"] / 10.0 + students["named_in_other_contests_total"] * 2
    )
    students["latest_seen_year"] = students["latest_seen_year"].fillna(0)
    students["latest_named_year"] = students["latest_named_year"].fillna(0)
    students["best_result_year"] = students["best_result_year"].fillna(0)
    students["years_since_latest_seen"] = students["latest_seen_year"].map(
        lambda value: reference_year - int(value) if value and value > 0 else 999
    )
    students["years_since_best_result"] = students["best_result_year"].map(
        lambda value: reference_year - int(value) if value and value > 0 else 999
    )
    students["recent_named_override"] = students["recent_2y_named_count"].gt(0)
    students["recent_activity_signal"] = students["recent_2y_contest_count"].ge(2)
    students["recent_competitive_signal"] = (
        students["recent_2y_top_50_count"].gt(0)
        | students["recent_2y_best_rank_percentile"].fillna(1.0).le(0.1)
    )
    students["stale_peak_flag"] = (
        students["years_since_best_result"].ge(4)
        & students["recent_2y_contest_count"].gt(0)
        & students["recent_2y_best_rank_percentile"].fillna(1.0).gt(0.2)
    )
    students["recency_profile"] = "Historico sem leitura recente"
    students.loc[students["recent_named_override"], "recency_profile"] = "Nomeado recentemente"
    students.loc[
        ~students["recent_named_override"]
        & students["recent_activity_signal"]
        & students["recent_competitive_signal"],
        "recency_profile",
    ] = "Ativo e competitivo"
    students.loc[
        ~students["recent_named_override"] & students["stale_peak_flag"],
        "recency_profile",
    ] = "Pico antigo"
    students.loc[
        ~students["recent_named_override"]
        & students["recent_activity_signal"]
        & ~students["recent_competitive_signal"],
        "recency_profile",
    ] = "Ativo, mas sem sinal forte recente"
    return students.sort_values(
        ["contest_count", "named_count", "inside_vacancies_count", "best_rank"],
        ascending=[False, False, False, True],
    )


def build_opportunity_table(candidates: pd.DataFrame, students: pd.DataFrame) -> pd.DataFrame:
    contest_cutoffs = (
        candidates.groupby(["contest_value", "contest_name", "contest_family", "candidates_count"], dropna=False)
        .apply(
            lambda frame: pd.Series(
                {
                    "last_named_rank": frame.loc[frame["named"], "ranking_position"].max(),
                    "last_inside_rank": frame.loc[frame["inside_vacancies"], "ranking_position"].max(),
                    "named_count_current": frame["named"].sum(),
                    "inside_count_current": frame["inside_vacancies"].sum(),
                }
            ),
            include_groups=False,
        )
        .reset_index()
    )

    opportunities = candidates.merge(
        contest_cutoffs,
        on=["contest_value", "contest_name", "contest_family", "candidates_count"],
        how="left",
    ).merge(
        students[
            [
                "identity_key",
                "display_name",
                "contest_count",
                "alias_count",
                "families",
                "named_count",
                "inside_vacancies_count",
                "other_results_total",
                "alias_names",
                "latest_seen_year",
                "latest_named_year",
                "recent_2y_contest_count",
                "recent_2y_named_count",
                "recent_2y_inside_count",
                "recent_2y_top_50_count",
                "recent_2y_best_rank_percentile",
                "years_since_latest_seen",
                "years_since_best_result",
                "recent_named_override",
                "recent_activity_signal",
                "recent_competitive_signal",
                "stale_peak_flag",
                "recency_profile",
            ]
        ].rename(
            columns={
                "named_count": "student_named_total",
                "inside_vacancies_count": "student_inside_total",
                "other_results_total": "student_other_results_total",
            }
        ),
        on="identity_key",
        how="left",
    )

    opportunities["delta_to_last_named"] = opportunities["ranking_position"] - opportunities["last_named_rank"]
    opportunities["delta_to_last_inside"] = opportunities["ranking_position"] - opportunities["last_inside_rank"]
    opportunities["delta_to_last_named"] = opportunities["delta_to_last_named"].where(
        opportunities["ranking_position"].notna() & opportunities["last_named_rank"].notna()
    )
    opportunities["delta_to_last_inside"] = opportunities["delta_to_last_inside"].where(
        opportunities["ranking_position"].notna() & opportunities["last_inside_rank"].notna()
    )
    opportunities["is_open_opportunity"] = ~opportunities["named"]
    opportunities["is_near_named_cutoff"] = opportunities["delta_to_last_named"].between(1, 30, inclusive="both")
    opportunities["is_near_inside_cutoff"] = opportunities["delta_to_last_inside"].between(1, 20, inclusive="both")
    opportunities["near_pass_band"] = "Monitorar"
    opportunities.loc[opportunities["named"], "near_pass_band"] = "Já nomeado"
    opportunities.loc[
        opportunities["is_open_opportunity"] & opportunities["delta_to_last_named"].le(0),
        "near_pass_band",
    ] = "Acima do corte"
    opportunities.loc[
        opportunities["is_open_opportunity"] & opportunities["delta_to_last_named"].between(1, 30, inclusive="both"),
        "near_pass_band",
    ] = "Perto"
    opportunities.loc[
        opportunities["is_open_opportunity"] & opportunities["delta_to_last_named"].between(1, 10, inclusive="both"),
        "near_pass_band",
    ] = "Muito perto"
    opportunities.loc[
        opportunities["is_open_opportunity"]
        & opportunities["delta_to_last_named"].isna()
        & opportunities["rank_percentile"].fillna(1).le(0.05),
        "near_pass_band",
    ] = "Forte sinal"
    opportunities["student_named_elsewhere"] = opportunities["student_named_total"].fillna(0) - opportunities["named"].astype(int)
    opportunities["student_inside_elsewhere"] = (
        opportunities["student_inside_total"].fillna(0) - opportunities["inside_vacancies"].astype(int)
    )
    opportunities["recent_named_override"] = opportunities["recent_named_override"].fillna(False)
    opportunities["recent_activity_signal"] = opportunities["recent_activity_signal"].fillna(False)
    opportunities["recent_competitive_signal"] = opportunities["recent_competitive_signal"].fillna(False)
    opportunities["stale_peak_flag"] = opportunities["stale_peak_flag"].fillna(False)
    return opportunities


def build_entity_proximity_table(opportunities: pd.DataFrame) -> pd.DataFrame:
    working = opportunities.copy()
    defaults = {
        "display_name": "",
        "alias_names": "",
        "families": "",
        "alias_count": 1,
        "contest_value": "",
        "rank_percentile": 1.0,
        "delta_to_last_named": pd.NA,
        "student_named_elsewhere": 0,
        "student_inside_elsewhere": 0,
        "latest_seen_year": 0,
        "latest_named_year": 0,
        "recent_2y_contest_count": 0,
        "recent_2y_named_count": 0,
        "recent_2y_top_50_count": 0,
        "recent_2y_best_rank_percentile": 1.0,
        "years_since_latest_seen": 999,
        "years_since_best_result": 999,
        "recent_named_override": False,
        "recent_activity_signal": False,
        "recent_competitive_signal": False,
        "stale_peak_flag": False,
        "recency_profile": "",
        "contest_name": "",
        "contest_family": "",
        "contest_year": 0,
        "near_pass_band": "",
        "ranking_text": "",
        "ranking_position": pd.NA,
        "delta_to_last_inside": pd.NA,
        "proximity_breakdown": "",
    }
    for column, default in defaults.items():
        if column not in working.columns:
            working[column] = _series_or_default(working, column, default)

    working["strong_signal"] = working["near_pass_band"].isin(["Acima do corte", "Muito perto", "Perto"])
    working["very_strong_signal"] = working["near_pass_band"].isin(["Acima do corte", "Muito perto"])
    working["open_signal"] = working["is_open_opportunity"].fillna(False)

    if "proximity_score" not in working.columns:
        working["proximity_score"] = 0.0

    best_rows = (
        working.sort_values(
            ["identity_key", "proximity_score", "delta_to_last_named", "rank_percentile"],
            ascending=[True, False, True, True],
            na_position="last",
        )
        .groupby("identity_key", as_index=False)
        .first()
    )

    summary = (
        working.groupby("identity_key", dropna=False)
        .agg(
            display_name=("display_name", lambda s: s.value_counts().index[0] if not s.empty else ""),
            alias_names=("alias_names", lambda s: s.dropna().astype(str).head(1).iloc[0] if not s.dropna().empty else ""),
            families=("families", lambda s: s.dropna().astype(str).head(1).iloc[0] if not s.dropna().empty else ""),
            alias_count=("alias_count", "max"),
            contest_count=("contest_value", "nunique"),
            recent_contest_count=("contest_value", "count"),
            open_opportunity_count=("open_signal", "sum"),
            strong_signal_count=("strong_signal", "sum"),
            very_strong_signal_count=("very_strong_signal", "sum"),
            avg_proximity_score=("proximity_score", "mean"),
            max_proximity_score=("proximity_score", "max"),
            best_rank_percentile_any=("rank_percentile", "min"),
            best_delta_to_last_named=("delta_to_last_named", "min"),
            student_named_elsewhere_max=("student_named_elsewhere", "max"),
            student_inside_elsewhere_max=("student_inside_elsewhere", "max"),
            latest_seen_year=("latest_seen_year", "max"),
            latest_named_year=("latest_named_year", "max"),
            recent_2y_contest_count=("recent_2y_contest_count", "max"),
            recent_2y_named_count=("recent_2y_named_count", "max"),
            recent_2y_top_50_count=("recent_2y_top_50_count", "max"),
            recent_2y_best_rank_percentile=("recent_2y_best_rank_percentile", "min"),
            years_since_latest_seen=("years_since_latest_seen", "min"),
            years_since_best_result=("years_since_best_result", "min"),
            recent_named_override=("recent_named_override", "max"),
            recent_activity_signal=("recent_activity_signal", "max"),
            recent_competitive_signal=("recent_competitive_signal", "max"),
            stale_peak_flag=("stale_peak_flag", "max"),
            recency_profile=("recency_profile", lambda s: s.dropna().astype(str).head(1).iloc[0] if not s.dropna().empty else ""),
        )
        .reset_index()
    )

    entity = summary.merge(
        best_rows[
            [
                "identity_key",
                "contest_value",
                "contest_name",
                "contest_family",
                "contest_year",
                "near_pass_band",
                "ranking_text",
                "ranking_position",
                "rank_percentile",
                "delta_to_last_named",
                "delta_to_last_inside",
                "proximity_breakdown",
                "proximity_score",
            ]
        ].rename(
            columns={
                "contest_value": "best_contest_value",
                "contest_name": "best_contest_name",
                "contest_family": "best_contest_family",
                "contest_year": "best_contest_year",
                "near_pass_band": "best_band",
                "ranking_text": "best_ranking_text",
                "ranking_position": "best_ranking_position",
                "rank_percentile": "best_rank_percentile_current",
                "delta_to_last_named": "best_delta_current",
                "delta_to_last_inside": "best_inside_delta_current",
                "proximity_breakdown": "best_proximity_breakdown",
                "proximity_score": "best_proximity_score",
            }
        ),
        on="identity_key",
        how="left",
    )

    entity["entity_proximity_score"] = (
        entity["best_proximity_score"].fillna(0)
        + entity["very_strong_signal_count"].fillna(0) * 0.35
        + entity["strong_signal_count"].fillna(0) * 0.15
        + entity["recent_contest_count"].clip(upper=12).fillna(0) * 0.04
        + entity["recent_2y_contest_count"].clip(upper=8).fillna(0) * 0.06
        + entity["recent_2y_top_50_count"].clip(upper=5).fillna(0) * 0.08
        - entity["recent_2y_named_count"].clip(upper=3).fillna(0) * 0.55
        - entity["stale_peak_flag"].astype(int) * 0.45
    )
    entity["best_rank_percentile_any"] = entity["best_rank_percentile_any"].fillna(1.0)
    entity["best_delta_to_last_named"] = entity["best_delta_to_last_named"].fillna(999999)
    entity["entity_status"] = "Acompanhar"
    entity.loc[entity["recent_named_override"], "entity_status"] = "Ja nomeado recentemente"
    entity.loc[
        ~entity["recent_named_override"]
        & entity["recent_activity_signal"]
        & entity["recent_competitive_signal"],
        "entity_status",
    ] = "Ativo e competitivo"
    entity.loc[
        ~entity["recent_named_override"] & entity["stale_peak_flag"],
        "entity_status",
    ] = "Pico antigo"
    entity["entity_reading"] = (
        "Faixa=" + entity["best_band"].fillna("")
        + " | status=" + entity["entity_status"].fillna("")
        + " | recency=" + entity["recency_profile"].fillna("")
    )
    return entity.sort_values(
        ["entity_proximity_score", "very_strong_signal_count", "strong_signal_count", "best_delta_to_last_named"],
        ascending=[False, False, False, True],
    )


def prepare_history_frames(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    candidates_history = frames["candidates_history"].copy()
    contest_pages_history = frames["contest_pages_history"].copy()

    if not candidates_history.empty:
        text_columns = [column for column in candidates_history.columns if candidates_history[column].dtype == "object"]
        candidates_history = _apply_text_cleanup(candidates_history, text_columns)
        for column in ["ranking_position", "final_score", "other_results_count"]:
            if column in candidates_history.columns:
                candidates_history[column] = pd.to_numeric(candidates_history[column], errors="coerce")
        for column in ["named", "inside_vacancies"]:
            if column in candidates_history.columns:
                candidates_history[column] = candidates_history[column].astype(str).str.lower().eq("true")
        candidates_history["normalized_name"] = candidates_history["name"].map(normalize_name)
        candidates_history["identity_key"] = candidates_history["name"].map(identity_key)

    if not contest_pages_history.empty:
        text_columns = [column for column in contest_pages_history.columns if contest_pages_history[column].dtype == "object"]
        contest_pages_history = _apply_text_cleanup(contest_pages_history, text_columns)
        for column in ["candidates_count", "named_count", "inside_vacancies_count"]:
            if column in contest_pages_history.columns:
                contest_pages_history[column] = pd.to_numeric(contest_pages_history[column], errors="coerce")

    return {
        "candidates_history": candidates_history,
        "contest_pages_history": contest_pages_history,
    }


def build_quality_tables(candidates: pd.DataFrame, contest_pages: pd.DataFrame) -> dict[str, pd.DataFrame]:
    suspicious_names = candidates[
        candidates["name"].str.contains("Ã|Â|�", regex=True, na=False)
        | candidates["contest_name"].str.contains("Ã|Â|�", regex=True, na=False)
    ][["contest_name", "name", "ranking_text"]].drop_duplicates()

    repeated_exact = (
        candidates.groupby("name")
        .agg(contest_count=("contest_value", "count"))
        .reset_index()
        .query("contest_count > 1")
        .sort_values("contest_count", ascending=False)
    )

    layouts = (
        candidates.groupby("detected_columns")
        .agg(row_count=("identity_key", "count"), contests=("contest_value", "nunique"))
        .reset_index()
        .sort_values("row_count", ascending=False)
    )

    sparse_contests = contest_pages[
        (contest_pages["named_count"] == 0) | (contest_pages["inside_vacancies_count"] == 0)
    ].sort_values(["candidates_count", "named_count"], ascending=[False, True])

    aliases = (
        candidates.groupby("identity_key")
        .agg(
            alias_count=("normalized_name", "nunique"),
            alias_names=("name", lambda s: " | ".join(s.drop_duplicates().head(5))),
            contests=("contest_value", "nunique"),
        )
        .reset_index()
        .query("alias_count > 1")
        .sort_values(["alias_count", "contests"], ascending=[False, False])
    )

    return {
        "suspicious_text": suspicious_names,
        "repeated_names": repeated_exact,
        "layouts": layouts,
        "sparse_contests": sparse_contests,
        "aliases": aliases,
    }
