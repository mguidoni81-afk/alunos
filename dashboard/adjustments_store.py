from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


MANUAL_YEAR_COLUMNS = [
    "contest_value",
    "contest_name",
    "manual_year",
    "updated_at",
]

NOMINATION_OVERRIDE_COLUMNS = [
    "contest_value",
    "contest_name",
    "last_named_position",
    "updated_at",
]


def _load_table(path: Path, columns: list[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=columns)
    df = pd.read_csv(path)
    for column in columns:
        if column not in df.columns:
            df[column] = ""
    return df[columns]


def load_manual_years(path: Path) -> pd.DataFrame:
    df = _load_table(path, MANUAL_YEAR_COLUMNS)
    if "manual_year" in df.columns:
        df["manual_year"] = pd.to_numeric(df["manual_year"], errors="coerce")
    return df


def upsert_manual_year(path: Path, contest_value: str, contest_name: str, manual_year: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = load_manual_years(path)
    now = datetime.now(UTC).isoformat()
    mask = df["contest_value"].astype(str).eq(str(contest_value))
    if mask.any():
        df.loc[mask, "contest_name"] = contest_name
        df.loc[mask, "manual_year"] = int(manual_year)
        df.loc[mask, "updated_at"] = now
    else:
        df = pd.concat(
            [
                df,
                pd.DataFrame(
                    [
                        {
                            "contest_value": contest_value,
                            "contest_name": contest_name,
                            "manual_year": int(manual_year),
                            "updated_at": now,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
    df.to_csv(path, index=False)


def load_nomination_overrides(path: Path) -> pd.DataFrame:
    df = _load_table(path, NOMINATION_OVERRIDE_COLUMNS)
    if "last_named_position" in df.columns:
        df["last_named_position"] = pd.to_numeric(df["last_named_position"], errors="coerce")
    return df


def upsert_nomination_override(path: Path, contest_value: str, contest_name: str, last_named_position: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = load_nomination_overrides(path)
    now = datetime.now(UTC).isoformat()
    mask = df["contest_value"].astype(str).eq(str(contest_value))
    if mask.any():
        df.loc[mask, "contest_name"] = contest_name
        df.loc[mask, "last_named_position"] = int(last_named_position)
        df.loc[mask, "updated_at"] = now
    else:
        df = pd.concat(
            [
                df,
                pd.DataFrame(
                    [
                        {
                            "contest_value": contest_value,
                            "contest_name": contest_name,
                            "last_named_position": int(last_named_position),
                            "updated_at": now,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
    df.to_csv(path, index=False)


def apply_manual_adjustments(
    prepared: dict[str, pd.DataFrame],
    manual_years: pd.DataFrame,
    nomination_overrides: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    contest_pages = prepared["contest_pages"].copy()
    candidates = prepared["candidates"].copy()

    if not manual_years.empty:
        by_value = (
            manual_years.dropna(subset=["contest_value", "manual_year"])
            .drop_duplicates(subset=["contest_value"], keep="last")
            .set_index("contest_value")["manual_year"]
        )
        by_name = (
            manual_years.dropna(subset=["contest_name", "manual_year"])
            .drop_duplicates(subset=["contest_name"], keep="last")
            .set_index("contest_name")["manual_year"]
        )
        contest_pages["contest_year"] = (
            contest_pages["contest_value"].map(by_value)
            .combine_first(contest_pages["contest_name"].map(by_name))
            .combine_first(contest_pages["contest_year"])
        )
        candidates["contest_year"] = (
            candidates["contest_value"].map(by_value)
            .combine_first(candidates["contest_name"].map(by_name))
            .combine_first(candidates["contest_year"])
        )

    if not nomination_overrides.empty:
        latest_overrides = nomination_overrides.dropna(subset=["contest_value", "last_named_position"]).drop_duplicates(
            subset=["contest_value"], keep="last"
        )
        for row in latest_overrides.itertuples(index=False):
            contest_value = str(row.contest_value)
            cutoff = int(row.last_named_position)
            contest_mask = candidates["contest_value"].astype(str).eq(contest_value)
            candidates.loc[contest_mask, "named"] = (
                contest_mask
                & candidates["ranking_position"].notna()
                & candidates["ranking_position"].le(cutoff)
            )

        named_count_by_contest = (
            candidates.groupby("contest_value", dropna=False)["named"]
            .sum()
            .reset_index(name="named_count")
        )
        contest_pages = contest_pages.drop(columns=["named_count"], errors="ignore").merge(
            named_count_by_contest,
            on="contest_value",
            how="left",
        )
        contest_pages["named_count"] = contest_pages["named_count"].fillna(0).astype(int)

    adjusted = prepared.copy()
    adjusted["contest_pages"] = contest_pages
    adjusted["candidates"] = candidates
    return adjusted
