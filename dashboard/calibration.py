from __future__ import annotations

import math

import pandas as pd

from dashboard.scoring import DEFAULT_WEIGHTS, build_student_metric_frame, compute_student_scores
from dashboard.transform import build_student_table


METRIC_TO_WEIGHT = {
    "metric_contest_count": "contest_count",
    "metric_named_count": "named_count",
    "metric_inside_count": "inside_vacancies_count",
    "metric_best_rank_percentile": "best_rank_percentile",
    "metric_top_10_count": "top_10_count",
    "metric_top_50_count": "top_50_count",
    "metric_other_results_total": "other_results_total",
    "metric_contest_family_count": "contest_family_count",
    "metric_nomination_link_count": "nomination_link_count",
    "metric_recent_2y_contest_count": "recent_2y_contest_count",
    "metric_recent_2y_best_rank_percentile": "recent_2y_best_rank_percentile",
    "metric_recent_2y_top_50_count": "recent_2y_top_50_count",
    "metric_named_history_penalty": "named_history_penalty",
    "metric_stale_peak_penalty": "stale_peak_penalty",
}

PENALTY_WEIGHTS = {"named_history_penalty", "stale_peak_penalty"}


def _safe_mean(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    return float(series.mean())


def _normalize_weights(raw_weights: dict[str, float], defaults: dict[str, float]) -> dict[str, float]:
    positive_total = sum(value for key, value in raw_weights.items() if key not in PENALTY_WEIGHTS)
    default_positive_total = sum(value for key, value in defaults.items() if key not in PENALTY_WEIGHTS)
    penalty_total = sum(value for key, value in raw_weights.items() if key in PENALTY_WEIGHTS)
    default_penalty_total = sum(value for key, value in defaults.items() if key in PENALTY_WEIGHTS)

    normalized = {}
    for key, value in raw_weights.items():
        if key in PENALTY_WEIGHTS:
            scale = default_penalty_total / penalty_total if penalty_total > 0 else 1.0
        else:
            scale = default_positive_total / positive_total if positive_total > 0 else 1.0
        normalized[key] = value * scale
    return normalized


def calibrate_student_score_weights(
    candidates: pd.DataFrame,
    start_year: int = 2019,
    future_horizon_years: int = 2,
    top_ks: tuple[int, ...] = (25, 50, 100),
    min_future_named: int = 12,
) -> dict[str, object]:
    years = sorted(candidates["contest_year"].dropna().astype(int).unique().tolist())
    if not years:
        return {"weights": DEFAULT_WEIGHTS, "yearly": pd.DataFrame(), "metric_lift": pd.DataFrame(), "training_rows": pd.DataFrame()}

    latest_year = max(years)
    first_named_year = (
        candidates[candidates["named"]]
        .groupby("identity_key")["contest_year"]
        .min()
        .reset_index(name="first_named_year")
    )

    training_frames: list[pd.DataFrame] = []
    yearly_rows: list[dict[str, object]] = []

    for anchor_year in years:
        if anchor_year < start_year or anchor_year >= latest_year:
            continue

        past_candidates = candidates[candidates["contest_year"].fillna(-1).le(anchor_year)].copy()
        if past_candidates.empty:
            continue

        students = build_student_table(
            past_candidates,
            reference_year_override=int(anchor_year),
            lightweight=True,
        )
        students = students.merge(first_named_year, on="identity_key", how="left")
        students = students[
            students["first_named_year"].isna() | students["first_named_year"].gt(anchor_year)
        ].copy()
        if students.empty:
            continue

        future_cutoff = min(anchor_year + future_horizon_years, latest_year)
        students["future_named_label"] = students["first_named_year"].between(anchor_year + 1, future_cutoff, inclusive="both")

        positives = int(students["future_named_label"].sum())
        usable_for_calibration = positives >= min_future_named
        metric_frame = build_student_metric_frame(students)
        metric_frame["anchor_year"] = anchor_year
        metric_frame["future_cutoff_year"] = future_cutoff
        metric_frame["future_named_label"] = students["future_named_label"].astype(int).values
        metric_frame["display_name"] = students["display_name"].values
        if usable_for_calibration:
            training_frames.append(metric_frame)

        base_scores = compute_student_scores(students.assign(future_named_label=students["future_named_label"].values), DEFAULT_WEIGHTS)
        row = {
            "anchor_year": anchor_year,
            "future_cutoff_year": future_cutoff,
            "students_ranked": len(students),
            "future_named_count": positives,
            "base_rate": students["future_named_label"].mean() if len(students) else 0.0,
            "usable_for_calibration": usable_for_calibration,
        }
        for k in top_ks:
            top_slice = base_scores.head(min(k, len(base_scores)))
            precision = top_slice["future_named_label"].mean() if not top_slice.empty else 0.0
            row[f"base_precision_at_{k}"] = precision
            row[f"base_lift_at_{k}"] = precision / row["base_rate"] if row["base_rate"] > 0 else 0.0
        yearly_rows.append(row)

    if not training_frames:
        return {"weights": DEFAULT_WEIGHTS, "yearly": pd.DataFrame(), "metric_lift": pd.DataFrame(), "training_rows": pd.DataFrame()}

    training = pd.concat(training_frames, ignore_index=True)
    positives = training[training["future_named_label"].eq(1)]
    negatives = training[training["future_named_label"].eq(0)]

    metric_rows = []
    raw_weights: dict[str, float] = {}
    for metric_column, weight_key in METRIC_TO_WEIGHT.items():
        pos_mean = _safe_mean(positives[metric_column])
        neg_mean = _safe_mean(negatives[metric_column])
        diff = pos_mean - neg_mean
        raw_signal = max(-diff, 0.0) if weight_key in PENALTY_WEIGHTS else max(diff, 0.0)
        default_weight = DEFAULT_WEIGHTS.get(weight_key, 0.0)
        blended = default_weight * 0.45 + raw_signal * 4.0
        raw_weights[weight_key] = max(blended, 0.0)
        metric_rows.append(
            {
                "weight_key": weight_key,
                "metric_column": metric_column,
                "positive_mean": pos_mean,
                "negative_mean": neg_mean,
                "separation": diff,
                "raw_signal": raw_signal,
            }
        )

    calibrated_weights = _normalize_weights(raw_weights, DEFAULT_WEIGHTS)
    metric_lift = pd.DataFrame(metric_rows).sort_values("raw_signal", ascending=False)

    evaluation_rows = []
    for anchor_year in sorted(training["anchor_year"].unique().tolist()):
        yearly = training[training["anchor_year"] == anchor_year].copy()
        students_subset = yearly[[column for column in yearly.columns if not column.startswith("metric_")]].copy()
        ranked = compute_student_scores(students_subset, calibrated_weights)
        base_row = next((row for row in yearly_rows if row["anchor_year"] == anchor_year), None)
        row = {
            "anchor_year": anchor_year,
            "future_cutoff_year": int(yearly["future_cutoff_year"].max()),
            "students_ranked": len(yearly),
            "future_named_count": int(yearly["future_named_label"].sum()),
            "base_rate": yearly["future_named_label"].mean() if len(yearly) else 0.0,
            "usable_for_calibration": True,
        }
        for k in top_ks:
            top_slice = ranked.head(min(k, len(ranked)))
            precision = top_slice["future_named_label"].mean() if not top_slice.empty else 0.0
            row[f"calibrated_precision_at_{k}"] = precision
            row[f"calibrated_lift_at_{k}"] = precision / row["base_rate"] if row["base_rate"] > 0 else 0.0
            if base_row is not None:
                row[f"base_precision_at_{k}"] = base_row.get(f"base_precision_at_{k}", 0.0)
                row[f"delta_precision_at_{k}"] = precision - row[f"base_precision_at_{k}"]
        evaluation_rows.append(row)

    return {
        "weights": calibrated_weights,
        "yearly": pd.DataFrame(yearly_rows).merge(
            pd.DataFrame(evaluation_rows),
            on=["anchor_year", "future_cutoff_year"],
            how="left",
            suffixes=("", "_eval"),
        ),
        "metric_lift": metric_lift,
        "training_rows": training,
    }


def blend_entity_with_student_backtest(entity_table: pd.DataFrame, student_scores: pd.DataFrame) -> pd.DataFrame:
    blended = entity_table.merge(
        student_scores[["identity_key", "score", "score_breakdown"]],
        on="identity_key",
        how="left",
        suffixes=("", "_student"),
    )
    blended["score"] = blended["score"].fillna(0.0)

    proximity_norm = blended["entity_proximity_score"].rank(pct=True, ascending=True)
    student_norm = blended["score"].rank(pct=True, ascending=True)
    blended["calibrated_radar_score"] = proximity_norm * 0.68 + student_norm * 0.32
    return blended.sort_values(
        ["calibrated_radar_score", "entity_proximity_score", "best_delta_current"],
        ascending=[False, False, True],
        na_position="last",
    ).reset_index(drop=True)
