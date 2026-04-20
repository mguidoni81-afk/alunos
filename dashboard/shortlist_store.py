from __future__ import annotations

from datetime import datetime, UTC
from pathlib import Path

import pandas as pd


SHORTLIST_COLUMNS = [
    "identity_key",
    "display_name",
    "contest_name",
    "contest_value",
    "status",
    "priority",
    "owner",
    "notes",
    "created_at",
    "updated_at",
]


def load_shortlist(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=SHORTLIST_COLUMNS)
    df = pd.read_csv(path)
    for column in SHORTLIST_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    return df[SHORTLIST_COLUMNS]


def upsert_shortlist(path: Path, entry: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    shortlist = load_shortlist(path)
    now = datetime.now(UTC).isoformat()
    key_mask = (
        shortlist["identity_key"].astype(str).eq(str(entry["identity_key"]))
        & shortlist["contest_value"].astype(str).eq(str(entry["contest_value"]))
    )

    if key_mask.any():
        shortlist.loc[key_mask, "display_name"] = entry["display_name"]
        shortlist.loc[key_mask, "contest_name"] = entry["contest_name"]
        shortlist.loc[key_mask, "status"] = entry["status"]
        shortlist.loc[key_mask, "priority"] = entry["priority"]
        shortlist.loc[key_mask, "owner"] = entry["owner"]
        shortlist.loc[key_mask, "notes"] = entry["notes"]
        shortlist.loc[key_mask, "updated_at"] = now
    else:
        row = {
            "identity_key": entry["identity_key"],
            "display_name": entry["display_name"],
            "contest_name": entry["contest_name"],
            "contest_value": entry["contest_value"],
            "status": entry["status"],
            "priority": entry["priority"],
            "owner": entry["owner"],
            "notes": entry["notes"],
            "created_at": now,
            "updated_at": now,
        }
        shortlist = pd.concat([shortlist, pd.DataFrame([row])], ignore_index=True)

    shortlist.to_csv(path, index=False)
