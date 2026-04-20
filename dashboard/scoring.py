from __future__ import annotations

import math

import pandas as pd


DEFAULT_WEIGHTS = {
    "contest_count": 1.0,
    "named_count": 1.2,
    "inside_vacancies_count": 1.2,
    "best_rank_percentile": 1.5,
    "top_10_count": 1.1,
    "top_50_count": 0.8,
    "other_results_total": 0.6,
    "contest_family_count": 0.5,
    "nomination_link_count": 0.3,
    "named_history_penalty": 0.0,
}

DEFAULT_PROXIMITY_WEIGHTS = {
    "rank_percentile": 1.6,
    "delta_to_last_named": 1.8,
    "delta_to_last_inside": 1.0,
    "history_elsewhere": 0.9,
    "contest_count": 0.5,
    "nomination_link": 0.2,
    "recent_activity": 0.8,
    "recent_competitiveness": 1.2,
    "recent_named_penalty": 2.8,
    "stale_peak_penalty": 1.2,
    "already_named_penalty": 3.0,
}


def _safe_max(series: pd.Series) -> float:
    value = float(series.max()) if not series.empty else 0.0
    return value if value > 0 else 1.0


def _log_norm(series: pd.Series) -> pd.Series:
    max_value = _safe_max(series)
    return series.fillna(0).map(lambda value: math.log1p(value) / math.log1p(max_value))


def _series_or_default(frame: pd.DataFrame, column: str, default: float | int | bool = 0) -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series([default] * len(frame), index=frame.index)


def compute_student_scores(students: pd.DataFrame, weights: dict[str, float]) -> pd.DataFrame:
    scored = students.copy()
    scored["metric_contest_count"] = _log_norm(scored["contest_count"])
    scored["metric_named_count"] = _log_norm(scored["named_count"])
    scored["metric_inside_count"] = _log_norm(scored["inside_vacancies_count"])
    scored["metric_top_10_count"] = _log_norm(scored["top_10_count"])
    scored["metric_top_50_count"] = _log_norm(scored["top_50_count"])
    scored["metric_other_results_total"] = _log_norm(scored["other_results_total"])
    scored["metric_contest_family_count"] = _log_norm(scored["contest_family_count"])
    scored["metric_nomination_link_count"] = _log_norm(scored["nomination_link_count"])
    scored["metric_best_rank_percentile"] = 1 - scored["best_rank_percentile"].clip(lower=0, upper=1).fillna(1)
    scored["metric_named_history_penalty"] = _log_norm(scored["named_count"])

    scored["score"] = (
        scored["metric_contest_count"] * weights["contest_count"]
        + scored["metric_named_count"] * weights["named_count"]
        + scored["metric_inside_count"] * weights["inside_vacancies_count"]
        + scored["metric_best_rank_percentile"] * weights["best_rank_percentile"]
        + scored["metric_top_10_count"] * weights["top_10_count"]
        + scored["metric_top_50_count"] * weights["top_50_count"]
        + scored["metric_other_results_total"] * weights["other_results_total"]
        + scored["metric_contest_family_count"] * weights["contest_family_count"]
        + scored["metric_nomination_link_count"] * weights["nomination_link_count"]
        - scored["metric_named_history_penalty"] * weights["named_history_penalty"]
    )

    scored["score_breakdown"] = (
        "contest_count=" + scored["metric_contest_count"].round(2).astype(str)
        + " | named=" + scored["metric_named_count"].round(2).astype(str)
        + " | inside=" + scored["metric_inside_count"].round(2).astype(str)
        + " | best_pct=" + scored["metric_best_rank_percentile"].round(2).astype(str)
        + " | top10=" + scored["metric_top_10_count"].round(2).astype(str)
        + " | top50=" + scored["metric_top_50_count"].round(2).astype(str)
        + " | other=" + scored["metric_other_results_total"].round(2).astype(str)
        + " | families=" + scored["metric_contest_family_count"].round(2).astype(str)
        + " | nom_link=" + scored["metric_nomination_link_count"].round(2).astype(str)
    )
    return scored.sort_values(["score", "contest_count", "best_rank"], ascending=[False, False, True]).reset_index(drop=True)


def compute_opportunity_scores(opportunities: pd.DataFrame, weights: dict[str, float]) -> pd.DataFrame:
    scored = opportunities.copy()
    rank_percentile = _series_or_default(scored, "rank_percentile", 1.0)
    delta_to_last_named = _series_or_default(scored, "delta_to_last_named", pd.NA)
    delta_to_last_inside = _series_or_default(scored, "delta_to_last_inside", pd.NA)
    student_named_elsewhere = _series_or_default(scored, "student_named_elsewhere", 0)
    student_inside_elsewhere = _series_or_default(scored, "student_inside_elsewhere", 0)
    contest_count = _series_or_default(scored, "contest_count", 0)
    has_nomination_link = _series_or_default(scored, "has_nomination_link", False)
    recent_2y_contest_count = _series_or_default(scored, "recent_2y_contest_count", 0)
    recent_2y_top_50_count = _series_or_default(scored, "recent_2y_top_50_count", 0)
    recent_2y_best_rank_percentile = _series_or_default(scored, "recent_2y_best_rank_percentile", 1.0)
    recent_named_override = _series_or_default(scored, "recent_named_override", False)
    stale_peak_flag = _series_or_default(scored, "stale_peak_flag", False)
    named = _series_or_default(scored, "named", False)

    scored["metric_rank_percentile"] = 1 - rank_percentile.clip(lower=0, upper=1).fillna(1)

    positive_named_gap = delta_to_last_named.where(delta_to_last_named > 0)
    scored["metric_delta_to_last_named"] = 1 - (positive_named_gap.fillna(9999).clip(upper=100) / 100.0)
    scored.loc[delta_to_last_named.le(0).fillna(False), "metric_delta_to_last_named"] = 1.0
    scored["metric_delta_to_last_named"] = scored["metric_delta_to_last_named"].clip(lower=0, upper=1)

    positive_inside_gap = delta_to_last_inside.where(delta_to_last_inside > 0)
    scored["metric_delta_to_last_inside"] = 1 - (positive_inside_gap.fillna(9999).clip(upper=50) / 50.0)
    scored.loc[delta_to_last_inside.le(0).fillna(False), "metric_delta_to_last_inside"] = 1.0
    scored["metric_delta_to_last_inside"] = scored["metric_delta_to_last_inside"].clip(lower=0, upper=1)

    scored["metric_history_elsewhere"] = _log_norm(
        student_named_elsewhere.fillna(0) + student_inside_elsewhere.fillna(0)
    )
    scored["metric_contest_count"] = _log_norm(contest_count.fillna(0))
    scored["metric_nomination_link"] = has_nomination_link.astype(int)
    scored["metric_recent_activity"] = _log_norm(recent_2y_contest_count.fillna(0))
    scored["metric_recent_competitiveness"] = (
        _log_norm(recent_2y_top_50_count.fillna(0)) * 0.6
        + (1 - recent_2y_best_rank_percentile.clip(lower=0, upper=1).fillna(1)) * 0.4
    )
    scored["metric_recent_named_penalty"] = recent_named_override.astype(int)
    scored["metric_stale_peak_penalty"] = stale_peak_flag.astype(int)
    scored["metric_already_named_penalty"] = named.astype(int)

    scored["proximity_score"] = (
        scored["metric_rank_percentile"] * weights["rank_percentile"]
        + scored["metric_delta_to_last_named"] * weights["delta_to_last_named"]
        + scored["metric_delta_to_last_inside"] * weights["delta_to_last_inside"]
        + scored["metric_history_elsewhere"] * weights["history_elsewhere"]
        + scored["metric_contest_count"] * weights["contest_count"]
        + scored["metric_nomination_link"] * weights["nomination_link"]
        + scored["metric_recent_activity"] * weights["recent_activity"]
        + scored["metric_recent_competitiveness"] * weights["recent_competitiveness"]
        - scored["metric_recent_named_penalty"] * weights["recent_named_penalty"]
        - scored["metric_stale_peak_penalty"] * weights["stale_peak_penalty"]
        - scored["metric_already_named_penalty"] * weights["already_named_penalty"]
    )

    scored["proximity_breakdown"] = (
        "rank_pct=" + scored["metric_rank_percentile"].round(2).astype(str)
        + " | gap_named=" + scored["metric_delta_to_last_named"].round(2).astype(str)
        + " | gap_inside=" + scored["metric_delta_to_last_inside"].round(2).astype(str)
        + " | hist=" + scored["metric_history_elsewhere"].round(2).astype(str)
        + " | contests=" + scored["metric_contest_count"].round(2).astype(str)
        + " | recent_act=" + scored["metric_recent_activity"].round(2).astype(str)
        + " | recent_comp=" + scored["metric_recent_competitiveness"].round(2).astype(str)
        + " | recent_named_pen=" + scored["metric_recent_named_penalty"].round(2).astype(str)
        + " | stale_peak_pen=" + scored["metric_stale_peak_penalty"].round(2).astype(str)
    )

    return scored.sort_values(
        ["proximity_score", "delta_to_last_named", "rank_percentile"],
        ascending=[False, True, True],
        na_position="last",
    ).reset_index(drop=True)
