from __future__ import annotations

import argparse
import csv
import html
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse


GREEN_STATUS = "#22c55e"
BLUE_STATUS = "#3b82f6"
OPTION_RE = re.compile(
    r'<option\s+value="([^"]*)"(?:\s+data-fulltext="([^"]*)")?([^>]*)>(.*?)</option>',
    re.IGNORECASE | re.DOTALL,
)
TABLE_RE = re.compile(r'<table class="table">(.*?)</table>', re.IGNORECASE | re.DOTALL)
TBODY_RE = re.compile(r"<tbody>(.*?)</tbody>", re.IGNORECASE | re.DOTALL)
TD_RE = re.compile(r"<td\b[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
SPAN_RE = re.compile(r"<span>(.*?)</span>", re.IGNORECASE | re.DOTALL)
HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
PLACEMENT_RE = re.compile(r"(\d+)\s*º")


@dataclass
class ContestOption:
    value: str
    full_text: str
    display_text: str
    selected: bool


@dataclass
class CrossContestResult:
    contest_label: str
    contest_value: str | None
    ranking_text: str
    ranking_position: int | None
    named: bool
    inside_vacancies: bool
    href: str | None


@dataclass
class CandidateResult:
    name: str
    objective_score: float | None
    discursive_score: float | None
    title_score: float | None
    final_score: float | None
    ranking_text: str
    ranking_position: int | None
    named: bool
    inside_vacancies: bool
    other_results: list[CrossContestResult]


@dataclass
class ContestPage:
    source_file: str
    selected_contest: ContestOption | None
    contests: list[ContestOption]
    candidates: list[CandidateResult]


@dataclass
class NearNominationCandidate:
    name: str
    ranking_position: int
    delta_to_last_named: int
    final_score: float | None
    named_in_other_contests: int
    inside_vacancies_in_other_contests: int
    other_results_count: int


def clean_text(fragment: str) -> str:
    fragment = re.sub(r"<img\b[^>]*>", "", fragment, flags=re.IGNORECASE)
    fragment = BR_RE.sub("\n", fragment)
    fragment = TAG_RE.sub(" ", fragment)
    fragment = html.unescape(fragment)
    fragment = fragment.replace("\xa0", " ")
    fragment = re.sub(r"\s+", " ", fragment)
    return fragment.strip()


def parse_number(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    normalized = value.replace(".", "").replace(",", ".")
    if value.count(".") == 1 and "," not in value:
        normalized = value
    try:
        return float(normalized)
    except ValueError:
        return None


def parse_ranking_position(text: str) -> int | None:
    match = PLACEMENT_RE.search(text)
    if match:
        return int(match.group(1))
    return None


def parse_contests(page_html: str) -> list[ContestOption]:
    contests: list[ContestOption] = []
    for value, full_text, attrs, body in OPTION_RE.findall(page_html):
        if not value:
            continue
        display_text = clean_text(body)
        full_text = clean_text(full_text or display_text)
        contests.append(
            ContestOption(
                value=value,
                full_text=full_text,
                display_text=display_text or full_text,
                selected="selected" in attrs.lower(),
            )
        )
    return contests


def parse_cross_contest_results(cell_html: str) -> list[CrossContestResult]:
    results: list[CrossContestResult] = []
    for part in BR_RE.split(cell_html):
        text = clean_text(part)
        if not text:
            continue
        href_match = HREF_RE.search(part)
        href = html.unescape(href_match.group(1)) if href_match else None
        contest_value = None
        if href:
            parsed = urlparse(urljoin("https://www.rankingdosconcursos.com.br/", href))
            contest_value = parse_qs(parsed.query).get("sCa", [None])[0]
        ranking_position = parse_ranking_position(text)
        contest_label = re.sub(r"^\d+\s*º\s*", "", text).strip()
        results.append(
            CrossContestResult(
                contest_label=contest_label,
                contest_value=contest_value,
                ranking_text=text,
                ranking_position=ranking_position,
                named=BLUE_STATUS in part,
                inside_vacancies=GREEN_STATUS in part,
                href=href,
            )
        )
    return results


def parse_candidates(page_html: str) -> list[CandidateResult]:
    table_match = TABLE_RE.search(page_html)
    if not table_match:
        raise ValueError("Tabela principal não encontrada no arquivo.")

    candidates: list[CandidateResult] = []
    for body in TBODY_RE.findall(table_match.group(1)):
        cells = TD_RE.findall(body)
        if len(cells) < 8:
            continue
        status_cell, name_cell, objective, discursive, title, final, ranking, other_results = cells[:8]
        name_match = SPAN_RE.search(name_cell)
        name = clean_text(name_match.group(1) if name_match else name_cell)
        ranking_text = clean_text(ranking)
        candidates.append(
            CandidateResult(
                name=name,
                objective_score=parse_number(clean_text(objective)),
                discursive_score=parse_number(clean_text(discursive)),
                title_score=parse_number(clean_text(title)),
                final_score=parse_number(clean_text(final)),
                ranking_text=ranking_text,
                ranking_position=parse_ranking_position(ranking_text),
                named=BLUE_STATUS in status_cell,
                inside_vacancies=GREEN_STATUS in status_cell,
                other_results=parse_cross_contest_results(other_results),
            )
        )
    return candidates


def parse_page(path: Path) -> ContestPage:
    page_html = path.read_text(encoding="utf-8-sig")
    contests = parse_contests(page_html)
    selected_contest = next((contest for contest in contests if contest.selected), None)
    candidates = parse_candidates(page_html)
    return ContestPage(
        source_file=str(path),
        selected_contest=selected_contest,
        contests=contests,
        candidates=candidates,
    )


def build_near_nomination_list(
    candidates: Iterable[CandidateResult],
    window: int,
) -> tuple[int | None, list[NearNominationCandidate]]:
    candidates = [candidate for candidate in candidates if candidate.ranking_position is not None]
    named_positions = [candidate.ranking_position for candidate in candidates if candidate.named]
    if not named_positions:
        return None, []

    last_named = max(named_positions)
    near_candidates: list[NearNominationCandidate] = []
    for candidate in sorted(candidates, key=lambda item: item.ranking_position):
        if candidate.named or candidate.ranking_position <= last_named:
            continue
        near_candidates.append(
            NearNominationCandidate(
                name=candidate.name,
                ranking_position=candidate.ranking_position,
                delta_to_last_named=candidate.ranking_position - last_named,
                final_score=candidate.final_score,
                named_in_other_contests=sum(1 for item in candidate.other_results if item.named),
                inside_vacancies_in_other_contests=sum(
                    1 for item in candidate.other_results if item.inside_vacancies
                ),
                other_results_count=len(candidate.other_results),
            )
        )
        if len(near_candidates) >= window:
            break
    return last_named, near_candidates


def slugify(value: str) -> str:
    value = value.lower()
    replacements = {
        "á": "a",
        "à": "a",
        "â": "a",
        "ã": "a",
        "é": "e",
        "ê": "e",
        "í": "i",
        "ó": "o",
        "ô": "o",
        "õ": "o",
        "ú": "u",
        "ç": "c",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "concurso"


def export_contests_csv(path: Path, contests: list[ContestOption]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["value", "full_text", "display_text", "selected"])
        writer.writeheader()
        for contest in contests:
            writer.writerow(asdict(contest))


def export_candidates_csv(path: Path, candidates: list[CandidateResult], last_named: int | None) -> None:
    fieldnames = [
        "name",
        "objective_score",
        "discursive_score",
        "title_score",
        "final_score",
        "ranking_text",
        "ranking_position",
        "named",
        "inside_vacancies",
        "delta_to_last_named",
        "other_results_count",
        "named_in_other_contests",
        "inside_vacancies_in_other_contests",
        "other_results_summary",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for candidate in candidates:
            delta = None
            if last_named is not None and candidate.ranking_position is not None:
                delta = candidate.ranking_position - last_named
            writer.writerow(
                {
                    "name": candidate.name,
                    "objective_score": candidate.objective_score,
                    "discursive_score": candidate.discursive_score,
                    "title_score": candidate.title_score,
                    "final_score": candidate.final_score,
                    "ranking_text": candidate.ranking_text,
                    "ranking_position": candidate.ranking_position,
                    "named": candidate.named,
                    "inside_vacancies": candidate.inside_vacancies,
                    "delta_to_last_named": delta,
                    "other_results_count": len(candidate.other_results),
                    "named_in_other_contests": sum(1 for item in candidate.other_results if item.named),
                    "inside_vacancies_in_other_contests": sum(
                        1 for item in candidate.other_results if item.inside_vacancies
                    ),
                    "other_results_summary": " | ".join(item.ranking_text for item in candidate.other_results),
                }
            )


def export_near_nomination_csv(path: Path, candidates: list[NearNominationCandidate]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "name",
                "ranking_position",
                "delta_to_last_named",
                "final_score",
                "named_in_other_contests",
                "inside_vacancies_in_other_contests",
                "other_results_count",
            ],
        )
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(asdict(candidate))


def export_json(path: Path, page: ContestPage, last_named: int | None) -> None:
    payload = asdict(page)
    payload["analysis"] = {
        "last_named_position": last_named,
        "named_count": sum(1 for candidate in page.candidates if candidate.named),
        "inside_vacancies_count": sum(1 for candidate in page.candidates if candidate.inside_vacancies),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def print_summary(page: ContestPage, last_named: int | None, near_candidates: list[NearNominationCandidate]) -> None:
    selected_label = page.selected_contest.full_text if page.selected_contest else "Concurso não identificado"
    named_count = sum(1 for candidate in page.candidates if candidate.named)
    inside_vacancies_count = sum(1 for candidate in page.candidates if candidate.inside_vacancies)

    print(f"Concurso selecionado: {selected_label}")
    print(f"Concursos mapeados: {len(page.contests)}")
    print(f"Candidatos na tabela: {len(page.candidates)}")
    print(f"Nomeados no concurso atual: {named_count}")
    print(f"Dentro das vagas no concurso atual: {inside_vacancies_count}")
    print(f"Última colocação nomeada: {last_named if last_named is not None else 'não encontrada'}")
    print()
    print("Primeiros candidatos após a última nomeação:")
    for candidate in near_candidates[:10]:
        print(
            f"{candidate.ranking_position:>4} | +{candidate.delta_to_last_named:>3} | "
            f"{candidate.final_score or '-':>5} | {candidate.name}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extrai concursos, candidatos e uma fila inicial de 'próximos da nomeação' de uma página salva do Ranking dos Concursos."
    )
    parser.add_argument("input_file", type=Path, help="Arquivo HTML salvo em .txt")
    parser.add_argument(
        "--window",
        type=int,
        default=30,
        help="Quantidade de candidatos a exportar na fila de próximos da nomeação.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Pasta de saída. Se omitido, será criada em output/<slug-do-concurso>.",
    )
    args = parser.parse_args()

    page = parse_page(args.input_file)
    selected_label = page.selected_contest.full_text if page.selected_contest else args.input_file.stem
    output_dir = args.output_dir or Path("output") / slugify(selected_label)
    output_dir.mkdir(parents=True, exist_ok=True)

    last_named, near_candidates = build_near_nomination_list(page.candidates, args.window)

    export_contests_csv(output_dir / "contests.csv", page.contests)
    export_candidates_csv(output_dir / "candidates.csv", page.candidates, last_named)
    export_near_nomination_csv(output_dir / "near_nomination.csv", near_candidates)
    export_json(output_dir / "page_data.json", page, last_named)
    print_summary(page, last_named, near_candidates)
    print()
    print(f"Arquivos gerados em: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
