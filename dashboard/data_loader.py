from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import pandas as pd


SNAPSHOT_FILE_RE = re.compile(
    r"^(?P<prefix>.+)_(?P<kind>selector_contests|contest_pages|candidates|other_results|data)\.(?P<ext>csv|json)$"
)
REQUIRED_KINDS = {"selector_contests", "contest_pages", "candidates", "other_results"}


@dataclass(frozen=True)
class SnapshotPaths:
    snapshot_id: str
    base_path: Path
    selector_contests: Path
    contest_pages: Path
    candidates: Path
    other_results: Path
    data_json: Path | None


def discover_snapshots(output_dir: Path) -> list[SnapshotPaths]:
    grouped: dict[str, dict[str, Path]] = {}
    for path in output_dir.iterdir():
        if not path.is_file():
            continue
        match = SNAPSHOT_FILE_RE.match(path.name)
        if not match:
            continue
        prefix = match.group("prefix")
        kind = match.group("kind")
        grouped.setdefault(prefix, {})[kind] = path

    snapshots: list[SnapshotPaths] = []
    for snapshot_id, files in grouped.items():
        if not REQUIRED_KINDS.issubset(files.keys()):
            continue
        snapshots.append(
            SnapshotPaths(
                snapshot_id=snapshot_id,
                base_path=output_dir,
                selector_contests=files["selector_contests"],
                contest_pages=files["contest_pages"],
                candidates=files["candidates"],
                other_results=files["other_results"],
                data_json=files.get("data"),
            )
        )

    snapshots.sort(key=lambda item: item.snapshot_id, reverse=True)
    return snapshots


def load_snapshot_frames(snapshot: SnapshotPaths) -> dict[str, pd.DataFrame]:
    return {
        "selector_contests": pd.read_csv(snapshot.selector_contests),
        "contest_pages": pd.read_csv(snapshot.contest_pages),
        "candidates": pd.read_csv(snapshot.candidates),
        "other_results": pd.read_csv(snapshot.other_results),
    }


def load_all_snapshots_history(output_dir: Path) -> dict[str, pd.DataFrame]:
    snapshots = discover_snapshots(output_dir)
    candidate_frames: list[pd.DataFrame] = []
    contest_frames: list[pd.DataFrame] = []

    for snapshot in snapshots:
        candidates = pd.read_csv(snapshot.candidates)
        candidates["snapshot_id"] = snapshot.snapshot_id
        contests = pd.read_csv(snapshot.contest_pages)
        contests["snapshot_id"] = snapshot.snapshot_id
        candidate_frames.append(candidates)
        contest_frames.append(contests)

    candidates_history = pd.concat(candidate_frames, ignore_index=True) if candidate_frames else pd.DataFrame()
    contests_history = pd.concat(contest_frames, ignore_index=True) if contest_frames else pd.DataFrame()
    return {
        "candidates_history": candidates_history,
        "contest_pages_history": contests_history,
    }
