from __future__ import annotations

from html import escape
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.adjustments_store import (
    apply_manual_adjustments,
    load_manual_years,
    load_nomination_overrides,
    upsert_manual_year,
    upsert_nomination_override,
)
from dashboard.calibration import (
    blend_entity_with_student_backtest,
    calibrate_student_score_weights,
)
from dashboard.data_loader import discover_snapshots, load_all_snapshots_history, load_snapshot_frames
from dashboard.scoring import (
    DEFAULT_PROXIMITY_WEIGHTS,
    DEFAULT_WEIGHTS,
    compute_opportunity_scores,
    compute_student_scores,
)
from dashboard.shortlist_store import load_shortlist, upsert_shortlist
from dashboard.transform import (
    build_entity_proximity_table,
    build_opportunity_table,
    build_quality_tables,
    build_student_table,
    prepare_history_frames,
    prepare_snapshot_data,
)


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
SHORTLIST_PATH = ROOT / "dashboard_state" / "shortlist.csv"
MANUAL_YEAR_PATH = ROOT / "dashboard_state" / "manual_contest_years.csv"
NOMINATION_OVERRIDE_PATH = ROOT / "dashboard_state" / "nomination_overrides.csv"
SEED_MANUAL_YEAR_PATH = ROOT / "dashboard" / "manual_contest_years_seed.csv"
APP_PASSWORD = "flamengo"
APP_BUILD = "build 2026-04-20 / calibrated-score-radar-polish"

PROXIMITY_PRESETS = {
    "Quem esta mais perto": DEFAULT_PROXIMITY_WEIGHTS,
    "Mais pronto para abordagem": {
        **DEFAULT_PROXIMITY_WEIGHTS,
        "rank_percentile": 1.3,
        "delta_to_last_named": 1.4,
        "delta_to_last_inside": 0.8,
        "history_elsewhere": 1.2,
        "contest_count": 0.8,
        "nomination_link": 0.3,
        "recent_activity": 0.9,
        "recent_competitiveness": 1.5,
        "recent_named_penalty": 3.2,
        "stale_peak_penalty": 1.5,
        "already_named_penalty": 3.0,
    },
}

SHORTLIST_STATUS = ["novo", "revisar", "prioridade", "contatar", "em conversa", "convertido", "descartado"]
SHORTLIST_PRIORITY = ["alta", "media", "baixa"]
TIME_HORIZONS = {
    "Todo o historico": None,
    "Ultimos 2 anos": 2,
    "Ultimos 3 anos": 3,
    "Ultimos 5 anos": 5,
}

RADAR_COLUMN_OPTIONS = {
    "Faixa": "best_band",
    "Estado recente": "entity_status",
    "Concurso principal": "best_contest_name",
    "Ano do concurso": "best_contest_year",
    "Colocacao": "best_ranking_text",
    "Dist. corte": "best_delta_current",
    "Rank %": "best_rank_percentile_current",
    "Score calibrado": "calibrated_radar_score",
    "Radar atual": "entity_proximity_score",
    "Historico calibrado": "score",
    "Concursos": "contest_count",
    "Sinais fortes": "strong_signal_count",
    "Sinais muito fortes": "very_strong_signal_count",
    "Aliases": "alias_count",
    "Concursos 2 anos": "recent_2y_contest_count",
    "Nomeacoes 2 anos": "recent_2y_named_count",
    "Perfil temporal": "recency_profile",
}

BAND_COLOR_MAP = {
    "Acima do corte": "#c94f2d",
    "Muito perto": "#e07a24",
    "Perto": "#d7a229",
    "Monitorar": "#4c7ea8",
    "Forte sinal": "#5d8fb8",
    "Ja nomeado": "#7f8d98",
    "Sem faixa": "#c9d3dc",
}


st.set_page_config(
    page_title="Scout dos proximos aprovados pela Base do Aprovado",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] {
            display: none;
        }
        [data-testid="collapsedControl"] {
            display: none;
        }
        .block-container {
            padding-top: 1rem;
            padding-bottom: 1.5rem;
        }
        .stTabs [data-baseweb="tab-list"] {
            gap: 0.4rem;
        }
        .stTabs [data-baseweb="tab"] {
            height: 2.6rem;
            border-radius: 999px;
            padding: 0 0.95rem;
            background: #f3f6f9;
        }
        .acr-note {
            padding: 0.8rem 0.95rem;
            border: 1px solid #dbe4f0;
            border-radius: 14px;
            background: #f8fbff;
            margin-bottom: 0.75rem;
        }
        .acr-soft {
            padding: 0.9rem 1rem;
            border-radius: 14px;
            background: #f5f7fb;
            border: 1px solid #e7ecf3;
        }
        .acr-toolbar {
            padding: 0.9rem 1rem 0.55rem 1rem;
            border: 1px solid #dce6ef;
            border-radius: 18px;
            background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
            margin: 0.2rem 0 1rem 0;
        }
        .acr-matrix-wrap {
            overflow-x: auto;
            border: 1px solid #dce6ef;
            border-radius: 18px;
            background: #0f1722;
            padding: 0.35rem;
            margin-top: 0.5rem;
        }
        .acr-matrix-toolbar {
            padding: 0.75rem 0.85rem 0.45rem 0.85rem;
            border: 1px solid #dce6ef;
            border-radius: 16px;
            background: #fbfdff;
            margin: 0.5rem 0 0.65rem 0;
        }
        .acr-radar-wrap {
            overflow-x: auto;
            border: 1px solid #e2eaf2;
            border-radius: 18px;
            background: linear-gradient(180deg, #ffffff 0%, #fbfdff 100%);
            margin-top: 0.4rem;
            box-shadow: 0 10px 26px rgba(15, 23, 34, 0.04);
        }
        table.acr-radar {
            width: 100%;
            border-collapse: collapse;
            min-width: 920px;
        }
        .acr-radar th, .acr-radar td {
            padding: 0.7rem 0.75rem;
            border-bottom: 1px solid #edf1f5;
            white-space: nowrap;
            vertical-align: middle;
            font-size: 0.83rem;
        }
        .acr-radar th {
            background: #f7fafc;
            color: #698095;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-size: 0.72rem;
            text-align: left;
            position: sticky;
            top: 0;
            z-index: 1;
        }
        .acr-radar tr:hover td {
            background: #f9fbfe;
        }
        .acr-radar tr:nth-child(even) td {
            background: rgba(247, 250, 252, 0.55);
        }
        .acr-radar-row-acima-do-corte td {
            border-left: 3px solid #c94f2d;
        }
        .acr-radar-row-muito-perto td {
            border-left: 3px solid #e07a24;
        }
        .acr-rank-pill {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 28px;
            height: 28px;
            padding: 0 0.45rem;
            border-radius: 999px;
            background: #eef3f8;
            color: #25445f;
            font-weight: 700;
        }
        .acr-link {
            color: #19324b !important;
            text-decoration: none;
            font-weight: 700;
        }
        .acr-link:hover {
            text-decoration: underline;
        }
        .acr-mini-badge {
            display: inline-block;
            padding: 0.2rem 0.5rem;
            border-radius: 999px;
            font-size: 0.76rem;
            font-weight: 700;
        }
        .acr-matrix-legend {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin-top: 0.35rem;
        }
        table.acr-matrix {
            border-collapse: separate;
            border-spacing: 0;
            min-width: 980px;
            width: 100%;
            color: #eef4fb;
            font-size: 0.82rem;
        }
        .acr-matrix th,
        .acr-matrix td {
            padding: 0.55rem 0.6rem;
            border-right: 1px solid rgba(255,255,255,0.06);
            border-bottom: 1px solid rgba(255,255,255,0.05);
            vertical-align: middle;
            white-space: nowrap;
        }
        .acr-matrix tbody tr:hover td {
            background-color: rgba(255,255,255,0.035);
        }
        .acr-matrix thead th {
            position: sticky;
            top: 0;
            z-index: 2;
            background: #111a27;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-size: 0.72rem;
            color: #9fb3c9;
        }
        .acr-matrix .group-head {
            background: #0d1521;
            color: #7fa1c2;
            font-weight: 700;
            text-align: left;
            border-bottom: 1px solid rgba(255,255,255,0.08);
        }
        .acr-matrix .sticky-col {
            position: sticky;
            left: 0;
            z-index: 3;
            background: #111a27;
        }
        .acr-matrix .sticky-col-2 {
            position: sticky;
            left: 52px;
            z-index: 3;
            background: #111a27;
        }
        .acr-rank-col {
            width: 52px;
            text-align: center;
            color: #8fa4ba;
            font-weight: 700;
        }
        .acr-student-link,
        .acr-contest-link {
            color: #f4f8fd !important;
            text-decoration: none;
            font-weight: 700;
        }
        .acr-student-link:hover,
        .acr-contest-link:hover {
            text-decoration: underline;
        }
        .acr-contest-cell {
            min-width: 98px;
            border-radius: 12px;
            padding: 0.38rem 0.45rem;
            line-height: 1.15;
            text-align: left;
            color: #0f1722;
            font-weight: 600;
        }
        .acr-contest-sub {
            display: block;
            font-size: 0.74rem;
            opacity: 0.95;
            margin-top: 0.12rem;
        }
        .acr-contest-compact {
            min-width: 74px;
            text-align: center;
        }
        .acr-nav {
            padding: 0.4rem 0 0.6rem 0;
        }
        .acr-hero {
            padding: 0.95rem 1rem;
            border-radius: 18px;
            background: linear-gradient(135deg, #f7fbff 0%, #eef6ff 100%);
            border: 1px solid #dbe8f4;
            margin-bottom: 0.7rem;
        }
        .acr-section-title {
            margin-top: 0.1rem;
            margin-bottom: 0.35rem;
            font-weight: 700;
            font-size: 1.02rem;
        }
        .acr-chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin: 0.3rem 0 0.95rem 0;
        }
        .acr-chip {
            display: inline-block;
            padding: 0.28rem 0.62rem;
            border-radius: 999px;
            background: #eef4fb;
            color: #24415c;
            border: 1px solid #d8e3ef;
            font-size: 0.84rem;
        }
        .acr-kpi {
            padding: 0.9rem 0.95rem;
            border-radius: 16px;
            border: 1px solid #e5ebf2;
            background: #ffffff;
            min-height: 116px;
            margin-bottom: 0.55rem;
        }
        .acr-kpi-label {
            color: #5c6f82;
            font-size: 0.88rem;
            margin-bottom: 0.4rem;
        }
        .acr-kpi-value {
            font-size: 2rem;
            line-height: 1.1;
            font-weight: 700;
            margin-bottom: 0.35rem;
            color: #162739;
        }
        .acr-kpi-help {
            color: #60758a;
            font-size: 0.84rem;
        }
        .acr-list-card {
            padding: 0.75rem 0.9rem;
            border-radius: 14px;
            border: 1px solid #e4ebf3;
            background: #fff;
            margin-bottom: 0.45rem;
            box-shadow: 0 8px 24px rgba(15, 23, 34, 0.03);
        }
        .acr-list-card-hot {
            border-color: #f0b19a;
            background: linear-gradient(180deg, #fffaf7 0%, #fff2ec 100%);
            box-shadow: 0 6px 16px rgba(201, 79, 45, 0.08);
        }
        .acr-list-card-very-hot {
            border-color: #ef9b72;
            background: linear-gradient(180deg, #fff8f2 0%, #ffeade 100%);
            box-shadow: 0 8px 18px rgba(224, 122, 36, 0.12);
        }
        .acr-list-title {
            font-weight: 700;
            color: #162739;
            margin-bottom: 0.18rem;
        }
        .acr-list-subtitle {
            color: #61768a;
            font-size: 0.88rem;
            margin-bottom: 0.45rem;
        }
        .acr-badge {
            display: inline-block;
            padding: 0.2rem 0.52rem;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 600;
            margin-right: 0.35rem;
            margin-bottom: 0.28rem;
            border: 1px solid transparent;
        }
        .acr-badge-hot { background: #fff0ea; color: #9b3d1c; border-color: #efb79d; }
        .acr-badge-very-hot { background: #ffe7db; color: #a14b11; border-color: #eea877; }
        .acr-badge-warm { background: #fff6df; color: #8a5d00; border-color: #eed89d; }
        .acr-badge-cool { background: #edf6ff; color: #24517a; border-color: #cfe1f4; }
        .acr-badge-muted { background: #f3f5f7; color: #536473; border-color: #dce3e8; }
        .acr-detail-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.7rem;
            margin: 0.7rem 0 0.9rem 0;
        }
        .acr-detail-card {
            padding: 0.85rem 0.95rem;
            border-radius: 14px;
            background: #fff;
            border: 1px solid #e4ebf3;
        }
        .acr-detail-label {
            font-size: 0.82rem;
            color: #60758a;
            margin-bottom: 0.2rem;
        }
        .acr-detail-value {
            font-size: 1rem;
            color: #162739;
            font-weight: 600;
        }
        .acr-login {
            max-width: 460px;
            padding: 1.1rem 1.1rem 0.95rem 1.1rem;
            border-radius: 18px;
            border: 1px solid #dce6ef;
            background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
            margin: 2rem auto 0 auto;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner=False)
def list_snapshots() -> list[str]:
    return [snapshot.snapshot_id for snapshot in discover_snapshots(OUTPUT_DIR)]


@st.cache_data(show_spinner=True)
def load_prepared_snapshot(snapshot_id: str) -> dict[str, pd.DataFrame]:
    snapshots = {snapshot.snapshot_id: snapshot for snapshot in discover_snapshots(OUTPUT_DIR)}
    snapshot = snapshots[snapshot_id]
    frames = load_snapshot_frames(snapshot)
    prepared = prepare_snapshot_data(frames)
    seed_manual_years = pd.read_csv(SEED_MANUAL_YEAR_PATH) if SEED_MANUAL_YEAR_PATH.exists() else pd.DataFrame()
    manual_years = pd.concat([seed_manual_years, load_manual_years(MANUAL_YEAR_PATH)], ignore_index=True, sort=False)
    prepared = apply_manual_adjustments(
        prepared,
        manual_years,
        load_nomination_overrides(NOMINATION_OVERRIDE_PATH),
    )
    prepared["students"] = build_student_table(prepared["candidates"])
    prepared["opportunities"] = build_opportunity_table(prepared["candidates"], prepared["students"])
    prepared["quality"] = build_quality_tables(prepared["candidates"], prepared["contest_pages"])
    return prepared


@st.cache_data(show_spinner=False)
def load_history() -> dict[str, pd.DataFrame]:
    history = load_all_snapshots_history(OUTPUT_DIR)
    return prepare_history_frames(history)


def get_reference_year(prepared: dict[str, pd.DataFrame]) -> int:
    years = prepared["candidates"]["contest_year"].dropna()
    current_year = datetime.now().year
    if years.empty:
        return current_year
    return int(years.max())


@st.cache_data(show_spinner=True)
def load_score_calibration(snapshot_id: str, candidates: pd.DataFrame) -> dict[str, object]:
    del snapshot_id
    return calibrate_student_score_weights(candidates)


def apply_time_horizon(
    opportunities: pd.DataFrame,
    horizon_years: int | None,
    include_unknown_years: bool,
    reference_year: int,
) -> pd.DataFrame:
    if horizon_years is None:
        if include_unknown_years:
            return opportunities
        return opportunities[opportunities["contest_year"].notna()]

    min_year = reference_year - horizon_years + 1
    year_ok = opportunities["contest_year"].fillna(-1).ge(min_year)
    if include_unknown_years:
        year_ok = year_ok | opportunities["contest_year"].isna()
    return opportunities[year_ok]


def format_number(value: int | float | None) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{int(value):,}".replace(",", ".")


def band_count(entity_table: pd.DataFrame, band_name: str) -> int:
    if entity_table.empty or "best_band" not in entity_table.columns:
        return 0
    return int(entity_table["best_band"].fillna("").eq(band_name).sum())


def band_bg_color(label: str) -> str:
    mapping = {
        "Acima do corte": "#ffddcf",
        "Muito perto": "#ffe6cf",
        "Perto": "#fff0c9",
        "Monitorar": "#dcecff",
        "Forte sinal": "#d5e7fb",
        "Ja nomeado": "#d9dfe5",
    }
    return mapping.get(label, "#edf2f6")


def read_query_value(name: str) -> str | None:
    value = st.query_params.get(name)
    if value is None:
        return None
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value)


def sync_state_from_query_params() -> None:
    view = read_query_value("view")
    student = read_query_value("student")
    contest = read_query_value("contest")
    if view:
        st.session_state["current_view"] = view
    if student:
        st.session_state["selected_entity_name"] = student
    if contest:
        st.session_state["selected_contest_value"] = contest


def render_filter_chips(items: list[str]) -> None:
    if not items:
        return
    chips = "".join(f'<span class="acr-chip">{item}</span>' for item in items)
    st.markdown(f'<div class="acr-chip-row">{chips}</div>', unsafe_allow_html=True)


def badge_class(label: str) -> str:
    if label == "Acima do corte":
        return "acr-badge-very-hot"
    if label in {"Muito perto", "Ativo e competitivo"}:
        return "acr-badge-hot"
    if label in {"Perto", "Acompanhar"}:
        return "acr-badge-warm"
    if label in {"Pico antigo", "Ativo, mas sem sinal forte recente"}:
        return "acr-badge-cool"
    return "acr-badge-muted"


def open_entity_view(display_name: str) -> None:
    st.session_state["selected_entity_name"] = display_name
    st.session_state["current_view"] = "Aluno"
    st.query_params.clear()
    st.query_params["view"] = "Aluno"
    st.query_params["student"] = display_name
    st.rerun()


def open_contest_view(contest_value: str, contest_name: str) -> None:
    st.session_state["selected_contest_value"] = str(contest_value)
    st.session_state["selected_contest_name"] = contest_name
    st.session_state["current_view"] = "Concurso"
    st.query_params.clear()
    st.query_params["view"] = "Concurso"
    st.query_params["contest"] = str(contest_value)
    st.rerun()


def render_top_entity_cards(entity_table: pd.DataFrame, limit: int = 6) -> None:
    if entity_table.empty:
        return
    for idx, row in entity_table.head(limit).iterrows():
        badges = [
            row.get("best_band", ""),
            row.get("entity_status", ""),
        ]
        badge_html = "".join(
            f'<span class="acr-badge {badge_class(label)}">{label}</span>'
            for label in badges
            if label
        )
        delta = format_number(row.get("best_delta_current"))
        card_class = "acr-list-card"
        if row.get("best_band") == "Acima do corte":
            card_class += " acr-list-card-very-hot"
        elif row.get("best_band") == "Muito perto":
            card_class += " acr-list-card-hot"
        st.markdown(
            f"""
            <div class="{card_class}">
                <div class="acr-list-title">{row.get("display_name", "")}</div>
                <div class="acr-list-subtitle">{row.get("best_contest_name", "")}</div>
                <div>{badge_html}</div>
                <div class="acr-list-subtitle">Distancia do corte: {delta} | Score calibrado: {row.get("calibrated_radar_score", row.get("entity_proximity_score", 0)):.2f}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        action_cols = st.columns([1, 1], gap="small")
        if action_cols[0].button(str(row.get("display_name", "")), key=f"open_entity_card_{idx}", use_container_width=True):
            open_entity_view(str(row.get("display_name", "")))
        if action_cols[1].button(str(row.get("best_contest_name", "")), key=f"open_contest_card_{idx}", use_container_width=True):
            open_contest_view(str(row.get("best_contest_value", "")), str(row.get("best_contest_name", "")))


def detail_card(label: str, value: str) -> str:
    return (
        '<div class="acr-detail-card">'
        f'<div class="acr-detail-label">{label}</div>'
        f'<div class="acr-detail-value">{value}</div>'
        "</div>"
    )


def compact_contest_label(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    tokens = text.split()
    if len(tokens) <= 3 and len(text) <= 18:
        return text
    short = " ".join(tokens[:3])
    return short[:18].strip()


def column_display_label(column_name: str) -> str:
    inverse = {value: key for key, value in RADAR_COLUMN_OPTIONS.items()}
    return inverse.get(column_name, column_name.replace("_", " ").title())


def top_controls(
    snapshot_ids: list[str],
    selected_snapshot: str,
    prepared: dict[str, pd.DataFrame],
) -> tuple[str, pd.DataFrame, pd.DataFrame, str, str, list[str], list[str]]:
    opportunities = prepared["opportunities"]
    students = prepared["students"]
    reference_year = get_reference_year(prepared)

    st.markdown('<div class="acr-toolbar">', unsafe_allow_html=True)
    row = st.columns([1.6, 1.05, 1.05, 0.95, 0.95, 0.9])
    selected_snapshot = row[0].selectbox("Snapshot", snapshot_ids, index=snapshot_ids.index(selected_snapshot))
    proximity_preset = row[1].selectbox("Preset", list(PROXIMITY_PRESETS.keys()), index=0)
    horizon_label = row[2].selectbox("Horizonte", list(TIME_HORIZONS.keys()), index=0)
    ui_mode = row[3].segmented_control("Modo", ["Simples", "Avancado"], default="Simples")
    exclude_current_named = row[4].toggle("Excluir nomeados", value=True, help="Ligado foca em quem ainda pode passar.")
    show_compact_help = row[5].toggle("Ajuda curta", value=False, help="Mostra lembretes breves sobre o efeito dos filtros.")

    horizon_years = TIME_HORIZONS[horizon_label]
    include_unknown_default = horizon_years is None
    extra_cols = st.columns([1.5, 1, 1, 1.1, 0.9])
    selected_families = extra_cols[0].multiselect(
        "Familias",
        sorted(opportunities["contest_family"].dropna().unique().tolist()),
        default=[],
        placeholder="Todas",
    )
    max_rank_percentile = extra_cols[1].slider("Rank % max", 0.01, 1.0, 0.20, 0.01)
    max_delta_named = extra_cols[2].slider("Dist. corte", 1, 500, 100, 1)
    min_other_results = extra_cols[3].slider("Fez tb min", 0, 250, 0)
    include_unknown_years = extra_cols[4].toggle(
        "Sem ano",
        value=include_unknown_default,
        help="Quando ligado com horizonte ativo, concursos antigos sem ano detectado podem voltar para o radar.",
    )
    if show_compact_help:
        st.caption(
            f"Horizonte usa o ano inferido do concurso. Referencia atual: {reference_year}. "
            "Diminuir rank % e dist. corte deixa o radar mais quente."
        )
    if horizon_years is not None and include_unknown_years:
        st.warning("Concursos sem ano identificado estao entrando no filtro. Isso pode trazer itens antigos.")
    selected_radar_columns = []
    if ui_mode == "Avancado":
        selected_radar_labels = st.multiselect(
            "Colunas extras do radar",
            [label for label in RADAR_COLUMN_OPTIONS.keys() if label not in {"Faixa", "Estado recente", "Concurso principal", "Dist. corte", "Score", "Concursos"}],
            default=["Ano do concurso", "Colocacao", "Sinais fortes", "Perfil temporal"],
            help="Escolha quais colunas adicionais quer ver na tabela principal.",
        )
        selected_radar_columns = [RADAR_COLUMN_OPTIONS[label] for label in selected_radar_labels]
    st.markdown("</div>", unsafe_allow_html=True)

    filtered_opportunities = opportunities.copy()
    filtered_opportunities = apply_time_horizon(
        filtered_opportunities,
        horizon_years,
        include_unknown_years,
        reference_year,
    )
    if selected_families:
        filtered_opportunities = filtered_opportunities[filtered_opportunities["contest_family"].isin(selected_families)]
    filtered_opportunities = filtered_opportunities[filtered_opportunities["rank_percentile"].fillna(1).le(max_rank_percentile)]
    filtered_opportunities = filtered_opportunities[filtered_opportunities["other_results_count"].fillna(0).ge(min_other_results)]
    eligible_gap = filtered_opportunities["delta_to_last_named"].isna() | filtered_opportunities["delta_to_last_named"].le(max_delta_named)
    filtered_opportunities = filtered_opportunities[eligible_gap]
    if exclude_current_named:
        filtered_opportunities = filtered_opportunities[~filtered_opportunities["named"]]

    filtered_students = students[students["identity_key"].isin(filtered_opportunities["identity_key"].unique().tolist())].copy()

    filter_summary = [
        horizon_label,
        proximity_preset,
        f"Rank % ate {max_rank_percentile:.0%}",
        f"Corte ate {max_delta_named} pos.",
    ]
    filter_summary.append("Sem ano fora" if not include_unknown_years else "Sem ano dentro")
    if selected_families:
        filter_summary.append(f"{len(selected_families)} familias")
    if min_other_results > 0:
        filter_summary.append(f"Fez tb >= {min_other_results}")
    if exclude_current_named:
        filter_summary.append("Exclui nomeados")

    return selected_snapshot, filtered_opportunities, filtered_students, proximity_preset, ui_mode, filter_summary, selected_radar_columns


def require_password() -> bool:
    if st.session_state.get("authenticated", False):
        return True

    st.markdown(
        """
        <div class="acr-login">
            <div class="acr-section-title">Acesso restrito</div>
            Digite a senha para entrar no scout.
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.form("login_form", clear_on_submit=False):
        password = st.text_input("Senha", type="password")
        submitted = st.form_submit_button("Entrar", use_container_width=True)
        if submitted:
            if password == APP_PASSWORD:
                st.session_state["authenticated"] = True
                st.rerun()
            st.error("Senha incorreta.")
    return False


def compute_views(
    prepared: dict[str, pd.DataFrame],
    filtered_opportunities: pd.DataFrame,
    filtered_students: pd.DataFrame,
    proximity_preset: str,
    score_calibration: dict[str, object],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    scored_opportunities = compute_opportunity_scores(filtered_opportunities, PROXIMITY_PRESETS[proximity_preset])
    entity_table = build_entity_proximity_table(scored_opportunities)
    calibrated_weights = score_calibration.get("weights", DEFAULT_WEIGHTS) if score_calibration else DEFAULT_WEIGHTS
    scored_students = compute_student_scores(filtered_students, calibrated_weights)
    entity_table = blend_entity_with_student_backtest(entity_table, scored_students)
    return entity_table, scored_opportunities


def metric_card_columns(prepared: dict[str, pd.DataFrame], entity_table: pd.DataFrame) -> None:
    candidates = prepared["candidates"]
    contests = prepared["contest_pages"]
    students = prepared["students"]
    cols = st.columns(6)
    cols[0].metric("Concursos", format_number(len(contests)))
    cols[1].metric("Linhas de candidatos", format_number(len(candidates)))
    cols[2].metric("Alunos consolidados", format_number(len(students)))
    cols[3].metric("Entidades no radar", format_number(len(entity_table)))
    cols[4].metric("Sinais muito fortes", format_number(int(entity_table["very_strong_signal_count"].fillna(0).sum())))
    cols[5].metric("Sinais fortes", format_number(int(entity_table["strong_signal_count"].fillna(0).sum())))


def render_primary_metrics(prepared: dict[str, pd.DataFrame], entity_table: pd.DataFrame) -> None:
    cols = st.columns(3)
    metrics = [
        (
            "Alunos no radar",
            format_number(len(entity_table)),
            "Entidades que atendem aos filtros e aparecem como oportunidades reais agora.",
        ),
        (
            "Muito perto",
            format_number(band_count(entity_table, "Muito perto")),
            "Perfis mais quentes do recorte atual, ja muito proximos do corte observado.",
        ),
        (
            "Monitorar",
            format_number(
                band_count(entity_table, "Perto")
                + band_count(entity_table, "Monitorar")
                + band_count(entity_table, "Acima do corte")
            ),
            "Perfis que ainda merecem acompanhamento, mesmo que nem todos sejam abordagem imediata.",
        ),
    ]
    for col, (label, value, help_text) in zip(cols, metrics):
        col.markdown(
            f"""
            <div class="acr-kpi">
                <div class="acr-kpi-label">{label}</div>
                <div class="acr-kpi-value">{value}</div>
                <div class="acr-kpi-help">{help_text}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    if st.session_state.get("ui_mode_current") == "Avancado":
        with st.expander("Ver contexto tecnico da base"):
            metric_card_columns(prepared, entity_table)


def render_calibration_panel(calibration: dict[str, object], ui_mode: str) -> None:
    st.markdown("### Como o score aprende")
    st.markdown(
        """
        <div class="acr-note">
            O modelo olha um ano do passado, monta o ranking teorico dos alunos naquele momento e mede
            quem realmente virou nomeado nos anos seguintes. Esse ciclo repete ano a ano para calibrar o peso
            dos sinais que mais antecipam nomeacao futura.
        </div>
        """,
        unsafe_allow_html=True,
    )
    yearly = calibration.get("yearly", pd.DataFrame())
    metric_lift = calibration.get("metric_lift", pd.DataFrame())
    if yearly.empty:
        st.info("Ainda nao ha anos suficientes para calibrar o score historicamente.")
        return

    usable = yearly[yearly["usable_for_calibration"].fillna(False)].copy()
    source = usable if not usable.empty else yearly.copy()
    avg_base = float(source.get("base_lift_at_50", pd.Series(dtype=float)).fillna(0).mean()) if "base_lift_at_50" in source.columns else 0.0
    avg_cal = float(source.get("calibrated_lift_at_50", pd.Series(dtype=float)).fillna(0).mean()) if "calibrated_lift_at_50" in source.columns else 0.0
    avg_delta = float(source.get("delta_precision_at_50", pd.Series(dtype=float)).fillna(0).mean()) if "delta_precision_at_50" in source.columns else 0.0
    top_driver = (
        metric_lift[metric_lift["raw_signal"].fillna(0).gt(0)].head(1)["weight_key"].iloc[0]
        if not metric_lift.empty and metric_lift["raw_signal"].fillna(0).gt(0).any()
        else "Sem driver dominante"
    )

    card_cols = st.columns(3)
    card_cols[0].metric("Anos usados", format_number(len(usable)), help="Anos com amostra suficiente de nomeacoes futuras para treinar.")
    card_cols[1].metric("Lift medio top 50", f"{avg_cal:.1f}x", help="Quanto o top 50 calibrado supera a taxa base de nomeacao futura.")
    card_cols[2].metric("Ganho medio", f"{avg_delta:.1%}", help="Melhora media de precisao do top 50 em relacao ao score base.")

    if top_driver != "Sem driver dominante":
        st.caption(f"Driver historico mais forte: `{top_driver}`")

    if ui_mode == "Avancado":
        with st.expander("Ver backtest por ano"):
            year_view = yearly.copy()
            visible_columns = [
                "anchor_year",
                "future_cutoff_year",
                "future_named_count",
                "base_rate",
                "base_precision_at_50",
                "calibrated_precision_at_50",
                "delta_precision_at_50",
                "usable_for_calibration",
            ]
            visible_columns = [column for column in visible_columns if column in year_view.columns]
            st.dataframe(
                year_view[visible_columns].sort_values("anchor_year"),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "anchor_year": st.column_config.NumberColumn("Ano observado", format="%d"),
                    "future_cutoff_year": st.column_config.NumberColumn("Olha ate", format="%d"),
                    "future_named_count": st.column_config.NumberColumn("Nomeados futuros"),
                    "base_rate": st.column_config.ProgressColumn("Taxa base", min_value=0.0, max_value=1.0),
                    "base_precision_at_50": st.column_config.ProgressColumn("Base top 50", min_value=0.0, max_value=1.0),
                    "calibrated_precision_at_50": st.column_config.ProgressColumn("Calibrado top 50", min_value=0.0, max_value=1.0),
                    "delta_precision_at_50": st.column_config.NumberColumn("Ganho", format="%.1%%"),
                    "usable_for_calibration": st.column_config.CheckboxColumn("Usado"),
                },
            )
            if not metric_lift.empty:
                lift_cols = [column for column in ["weight_key", "positive_mean", "negative_mean", "raw_signal"] if column in metric_lift.columns]
                st.dataframe(
                    metric_lift[lift_cols].head(8),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "weight_key": st.column_config.TextColumn("Sinal"),
                        "positive_mean": st.column_config.NumberColumn("Media nomeados", format="%.3f"),
                        "negative_mean": st.column_config.NumberColumn("Media nao nomeados", format="%.3f"),
                        "raw_signal": st.column_config.NumberColumn("Separacao", format="%.3f"),
                    },
                )


def render_band_context(entity_table: pd.DataFrame) -> None:
    st.markdown("### Temperatura do recorte")
    if entity_table.empty:
        st.info("Sem entidades para resumir no recorte atual.")
        return

    order = ["Acima do corte", "Muito perto", "Perto", "Monitorar", "Forte sinal", "Ja nomeado"]
    counts = (
        entity_table["best_band"]
        .fillna("Sem faixa")
        .value_counts()
        .rename_axis("Faixa")
        .reset_index(name="Alunos")
    )
    counts["ordem"] = counts["Faixa"].map({label: idx for idx, label in enumerate(order)}).fillna(len(order))
    counts = counts.sort_values(["ordem", "Alunos"], ascending=[True, False])
    fig = px.bar(
        counts,
        x="Alunos",
        y="Faixa",
        orientation="h",
        color="Faixa",
        color_discrete_map=BAND_COLOR_MAP,
        text="Alunos",
    )
    fig.update_layout(
        height=290,
        margin=dict(l=8, r=8, t=10, b=8),
        showlegend=False,
        xaxis_title="Alunos",
        yaxis_title="",
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    st.plotly_chart(fig, use_container_width=True)


def render_top_contests_panel(entity_table: pd.DataFrame, scored_opportunities: pd.DataFrame) -> None:
    st.markdown("### Concursos que concentram os sinais")
    top_contests = (
        scored_opportunities[scored_opportunities["identity_key"].isin(entity_table.head(25)["identity_key"])]
        .groupby(["contest_value", "contest_name"], dropna=False)
        .agg(
            alunos=("identity_key", "nunique"),
            media_score=("proximity_score", "mean"),
            quentes=("near_pass_band", lambda s: s.isin(["Acima do corte", "Muito perto"]).sum()),
        )
        .reset_index()
        .sort_values(["quentes", "alunos", "media_score"], ascending=[False, False, False])
        .head(10)
    )
    if top_contests.empty:
        st.info("Sem concursos com sinal forte no recorte atual.")
        return
    top_contests["abrir"] = top_contests.apply(
        lambda row: f"?view=Concurso&contest={quote_plus(str(row['contest_value']))}",
        axis=1,
    )
    html = ['<div class="acr-radar-wrap"><table class="acr-radar"><thead><tr><th>Concurso</th><th>Quentes</th><th>Alunos</th><th>Score medio</th></tr></thead><tbody>']
    for _, row in top_contests.iterrows():
        html.append(
            "<tr>"
            f'<td><a class="acr-link" href="{row["abrir"]}">{escape(str(row["contest_name"]))}</a></td>'
            f"<td>{int(row['quentes'])}</td>"
            f"<td>{int(row['alunos'])}</td>"
            f"<td>{float(row['media_score']):.2f}</td>"
            "</tr>"
        )
    html.append("</tbody></table></div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def radar_table(entity_table: pd.DataFrame, ui_mode: str, selected_radar_columns: list[str]) -> None:
    if entity_table.empty:
        st.info("Nenhum aluno atende aos filtros atuais.")
        return

    simple_columns = [
        "display_name",
        "best_band",
        "entity_status",
        "best_contest_name",
        "best_delta_current",
        "calibrated_radar_score",
    ]
    default_advanced = [
        "entity_proximity_score",
        "score",
        "contest_count",
        "best_rank_percentile_current",
        "best_contest_year",
        "best_ranking_text",
    ]
    columns = simple_columns if ui_mode == "Simples" else simple_columns + default_advanced + selected_radar_columns
    columns = list(dict.fromkeys([column for column in columns if column in entity_table.columns]))
    display_rows = entity_table[columns].head(18 if ui_mode == "Simples" else 80).copy()
    html = ['<div class="acr-radar-wrap"><table class="acr-radar"><thead><tr>']
    html.append("<th>#</th>")
    for column in columns:
        html.append(f"<th>{escape(column_display_label(column))}</th>")
    html.append("</tr></thead><tbody>")

    for idx, (_, row) in enumerate(display_rows.iterrows(), start=1):
        row_band = str(row.get("best_band", ""))
        html.append(f'<tr class="acr-radar-row-{escape(row_band.lower().replace(" ", "-"))}">')
        html.append(f'<td><span class="acr-rank-pill">{idx}</span></td>')
        for column in columns:
            value = row.get(column, "")
            if column == "display_name":
                label = escape(str(value))
                href = f"?view=Aluno&student={quote_plus(str(value))}"
                cell = f'<a class="acr-link" href="{href}">{label}</a>'
            elif column == "best_contest_name":
                label = escape(str(value))
                href = f"?view=Concurso&contest={quote_plus(str(row.get('best_contest_value', '')))}"
                cell = f'<a class="acr-link" href="{href}">{label}</a>'
            elif column == "best_band":
                cell = (
                    f'<span class="acr-mini-badge" style="background:{band_bg_color(str(value))};color:#19324b;">'
                    f"{escape(str(value))}</span>"
                )
            elif column in {"calibrated_radar_score", "entity_proximity_score", "score"}:
                cell = f"{float(value):.2f}" if pd.notna(value) else "N/A"
            elif column in {"best_rank_percentile_current"}:
                cell = f"{float(value) * 100:.1f}%" if pd.notna(value) else "N/A"
            elif column in {"best_delta_current", "contest_count", "best_contest_year", "strong_signal_count", "very_strong_signal_count", "recent_2y_contest_count", "recent_2y_named_count", "alias_count"}:
                cell = escape(format_number(value))
            else:
                cell = escape(str(value))
            html.append(f"<td>{cell}</td>")
        html.append("</tr>")
    html.append("</tbody></table></div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def render_ranking_matrix(entity_table: pd.DataFrame, scored_opportunities: pd.DataFrame, ui_mode: str) -> None:
    if entity_table.empty or scored_opportunities.empty:
        st.info("Sem dados suficientes para montar a matriz do ranking.")
        return

    st.markdown("### Ranking matrix")
    st.markdown(
        """
        <div class="acr-note">
            Use a matrix para comparar rapidamente os top alunos contra um conjunto pequeno de concursos relevantes.
            Clique no nome do aluno para abrir o perfil e no bloco do concurso para abrir o detalhe daquele ranking.
        </div>
        """,
        unsafe_allow_html=True,
    )

    default_top_n = 10 if ui_mode == "Simples" else 16
    default_contest_n = 5 if ui_mode == "Simples" else 7
    toolbar_row = st.columns([0.9, 1.1, 1.2, 1.1, 1.4], gap="small")
    top_n = toolbar_row[0].slider("Top alunos", 6, 30, default_top_n, 1, key="matrix_top_n")
    sort_mode = toolbar_row[1].selectbox(
        "Ordenar por",
        ["Score", "Mais perto do corte", "Mais quentes"],
        index=0,
        key="matrix_sort",
    )
    cell_mode = toolbar_row[2].segmented_control(
        "Visual",
        ["Compacta", "Detalhada"],
        default="Compacta" if ui_mode == "Simples" else "Detalhada",
        key="matrix_cell_mode",
    )
    preset_mode = toolbar_row[3].selectbox(
        "Concursos",
        ["Mais relevantes", "Mais quentes", "Escolha manual"],
        index=0,
        key="matrix_preset",
    )
    student_search = toolbar_row[4].text_input(
        "Buscar aluno na matrix",
        value="",
        key="matrix_student_search",
        placeholder="Digite parte do nome",
    )

    working_entities = entity_table.copy()
    if student_search.strip():
        query = student_search.strip().lower()
        working_entities = working_entities[working_entities["display_name"].str.lower().str.contains(query, na=False)]

    if sort_mode == "Mais perto do corte":
        working_entities = working_entities.sort_values(
            ["best_delta_current", "calibrated_radar_score", "entity_proximity_score"],
            ascending=[True, False],
            na_position="last",
        )
    elif sort_mode == "Mais quentes":
        working_entities = working_entities.sort_values(
            ["best_band", "calibrated_radar_score", "entity_proximity_score"],
            ascending=[True, False],
            na_position="last",
        )

    top_entities = working_entities.head(top_n).copy()
    if top_entities.empty:
        st.info("Nenhum aluno encontrado para esse filtro de busca na matrix.")
        return

    selected_keys = top_entities["identity_key"].tolist()
    matrix_scope = scored_opportunities[scored_opportunities["identity_key"].isin(selected_keys)].copy()

    contest_stats = (
        matrix_scope.groupby(["contest_value", "contest_name"], dropna=False)
        .agg(
            appearances=("identity_key", "nunique"),
            avg_score=("proximity_score", "mean"),
            hot_count=("near_pass_band", lambda s: s.isin(["Acima do corte", "Muito perto"]).sum()),
        )
        .reset_index()
        .sort_values(["appearances", "hot_count", "avg_score", "contest_name"], ascending=[False, False, False, True])
    )
    if preset_mode == "Mais quentes":
        contest_stats = contest_stats.sort_values(["hot_count", "avg_score", "appearances"], ascending=[False, False, False])
    default_contests = contest_stats.head(default_contest_n)
    contest_options = contest_stats.apply(lambda row: f"{row['contest_name']} [{row['contest_value']}]", axis=1).tolist()
    default_labels = default_contests.apply(lambda row: f"{row['contest_name']} [{row['contest_value']}]", axis=1).tolist()
    if preset_mode == "Escolha manual":
        selected_labels = st.multiselect(
            "Concursos em coluna",
            contest_options,
            default=default_labels,
            key="matrix_contests",
            help="Escolha os concursos que viram colunas no ranking matrix.",
        )
    else:
        selected_labels = default_labels
    if not selected_labels:
        selected_labels = default_labels
    selected_values = [label.rsplit("[", 1)[-1].rstrip("]") for label in selected_labels]

    matrix_opps = (
        matrix_scope[matrix_scope["contest_value"].astype(str).isin(selected_values)]
        .sort_values(["identity_key", "proximity_score", "delta_to_last_named"], ascending=[True, False, True], na_position="last")
        .drop_duplicates(subset=["identity_key", "contest_value"], keep="first")
    )

    contest_lookup = (
        matrix_opps[["contest_value", "contest_name"]]
        .drop_duplicates()
        .set_index("contest_value")["contest_name"]
        .to_dict()
    )

    legend = "".join(
        f'<span class="acr-chip" style="background:{band_bg_color(label)};color:#1a2a3a;border-color:transparent;">{label}</span>'
        for label in ["Acima do corte", "Muito perto", "Perto", "Monitorar"]
    )
    st.markdown(f'<div class="acr-matrix-legend">{legend}</div>', unsafe_allow_html=True)

    html = [
        '<div class="acr-matrix-wrap">',
        '<table class="acr-matrix">',
        "<thead>",
        "<tr>",
        '<th class="group-head sticky-col" colspan="2">Ranking</th>',
        '<th class="group-head" colspan="4">Radar atual</th>',
        '<th class="group-head" colspan="3">Recente</th>',
        f'<th class="group-head" colspan="{len(selected_values)}">Concursos</th>',
        "</tr>",
        "<tr>",
        '<th class="sticky-col acr-rank-col">#</th>',
        '<th class="sticky-col-2">Aluno</th>',
        "<th>Faixa</th>",
        "<th>Estado</th>",
        "<th>Score</th>",
        "<th>Dist.</th>",
        "<th>2 anos</th>",
        "<th>Nom. 2 anos</th>",
        "<th>Perfil</th>",
    ]
    for contest_value in selected_values:
        contest_name = str(contest_lookup.get(contest_value, contest_value))
        html.append(f'<th title="{escape(contest_name)}">{escape(compact_contest_label(contest_name))}</th>')
    html.extend(["</tr>", "</thead>", "<tbody>"])

    for rank, (_, entity) in enumerate(top_entities.iterrows(), start=1):
        student_name = str(entity.get("display_name", ""))
        student_link = f"?view=Aluno&student={quote_plus(student_name)}"
        html.extend(
            [
                "<tr>",
                f'<td class="sticky-col acr-rank-col">{rank}</td>',
                f'<td class="sticky-col-2"><a class="acr-student-link" href="{student_link}">{escape(student_name)}</a></td>',
                f'<td style="background:{band_bg_color(str(entity.get("best_band", "")))};color:#182635;font-weight:700;">{escape(str(entity.get("best_band", "")))}</td>',
                f'<td>{escape(str(entity.get("entity_status", "")))}</td>',
                f'<td>{float(entity.get("calibrated_radar_score", entity.get("entity_proximity_score", 0))):.2f}</td>',
                f'<td>{escape(format_number(entity.get("best_delta_current")))}</td>',
                f'<td>{escape(format_number(entity.get("recent_2y_contest_count")))}</td>',
                f'<td>{escape(format_number(entity.get("recent_2y_named_count")))}</td>',
                f'<td>{escape(str(entity.get("recency_profile", "")))}</td>',
            ]
        )

        entity_rows = matrix_opps[matrix_opps["identity_key"] == entity["identity_key"]]
        entity_by_contest = {str(row["contest_value"]): row for _, row in entity_rows.iterrows()}
        for contest_value in selected_values:
            row = entity_by_contest.get(str(contest_value))
            if row is None:
                html.append('<td style="background:#162231;color:#6f8296;">-</td>')
                continue
            cell_label = str(row.get("near_pass_band", ""))
            cell_color = band_bg_color(cell_label)
            contest_link = f"?view=Concurso&contest={quote_plus(str(contest_value))}"
            ranking = escape(str(row.get("ranking_text", "")))
            delta = escape(format_number(row.get("delta_to_last_named")))
            cell_class = "acr-contest-cell acr-contest-compact" if cell_mode == "Compacta" else "acr-contest-cell"
            cell_body = (
                f"{escape(cell_label)}"
                f'<span class="acr-contest-sub">{ranking}</span>'
                f'<span class="acr-contest-sub">Delta {delta}</span>'
            )
            if cell_mode == "Compacta":
                cell_body = (
                    f"{ranking}"
                    f'<span class="acr-contest-sub">{escape(cell_label)}</span>'
                    f'<span class="acr-contest-sub">D {delta}</span>'
                )
            html.append(
                "<td>"
                f'<a class="acr-contest-link" href="{contest_link}">'
                f'<div class="{cell_class}" style="background:{cell_color};">'
                f"{cell_body}"
                "</div></a></td>"
            )
        html.append("</tr>")

    html.extend(["</tbody>", "</table>", "</div>"])
    st.markdown("".join(html), unsafe_allow_html=True)


def main_entity_tab(
    prepared: dict[str, pd.DataFrame],
    entity_table: pd.DataFrame,
    scored_opportunities: pd.DataFrame,
    score_calibration: dict[str, object],
    ui_mode: str,
    filter_summary: list[str],
    selected_radar_columns: list[str],
) -> None:
    st.subheader("Quem esta proximo de passar?")
    render_filter_chips(filter_summary)
    render_primary_metrics(prepared, entity_table)
    upper_left, upper_right = st.columns([1.15, 1], gap="large")
    with upper_left:
        st.markdown("### Destaques imediatos")
        render_top_entity_cards(entity_table, limit=5 if ui_mode == "Avancado" else 4)
    with upper_right:
        render_calibration_panel(score_calibration, ui_mode)

    render_ranking_matrix(entity_table, scored_opportunities, ui_mode)

    st.markdown("### Radar detalhado")
    st.markdown(
        """
        <div class="acr-note">
            O ranking abaixo ja mistura a proximidade atual com o score calibrado pelo historico. Assim, um bom
            resultado antigo pesa menos quando o aluno nao sustenta competitividade recente ou ja aparece nomeado depois.
        </div>
        """,
        unsafe_allow_html=True,
    )
    radar_table(entity_table, ui_mode, selected_radar_columns)

    lower_left, lower_right = st.columns([1, 1], gap="large")
    with lower_left:
        render_band_context(entity_table)
    with lower_right:
        render_top_contests_panel(entity_table, scored_opportunities)

    if ui_mode == "Avancado":
        with st.expander("Ver grafico avancado de proximidade ao corte"):
            scatter = entity_table.head(250).copy()
            if not scatter.empty:
                fig_scatter = px.scatter(
                    scatter,
                    x="best_delta_current",
                    y="best_rank_percentile_current",
                    size="contest_count",
                    color="best_band",
                    hover_name="display_name",
                    hover_data=["best_contest_name", "strong_signal_count", "very_strong_signal_count"],
                    color_discrete_map=BAND_COLOR_MAP,
                )
                fig_scatter.update_yaxes(autorange="reversed")
                fig_scatter.update_layout(
                    height=430,
                    margin=dict(l=8, r=8, t=12, b=8),
                    xaxis_title="Distancia do corte",
                    yaxis_title="Rank percentual",
                )
                st.plotly_chart(fig_scatter, use_container_width=True)


def entity_detail_tab(entity_table: pd.DataFrame, scored_opportunities: pd.DataFrame, ui_mode: str) -> None:
    st.subheader("Aluno")

    if entity_table.empty:
        st.info("Nenhum aluno atende aos filtros atuais.")
        return

    selector_left, selector_right = st.columns([1.6, 1])
    options = entity_table["display_name"].tolist()
    default_name = st.session_state.get("selected_entity_name", options[0])
    if default_name not in options:
        default_name = options[0]
    selected_name = selector_left.selectbox("Selecione um aluno", options, index=options.index(default_name))
    st.session_state["selected_entity_name"] = selected_name
    selected_entity = entity_table[entity_table["display_name"] == selected_name].iloc[0]
    opp_rows = scored_opportunities[scored_opportunities["identity_key"] == selected_entity["identity_key"]].copy()
    contest_count = int(selected_entity.get("contest_count", 0) or 0)
    selector_right.metric("Concursos no radar", format_number(contest_count))

    st.markdown(
        f"""
        <div class="acr-soft">
            <strong>Leitura curta:</strong> {selected_entity.get("display_name", "")} aparece melhor em
            <strong>{selected_entity.get("best_contest_name", "")}</strong>, na faixa
            <strong>{selected_entity.get("best_band", "")}</strong>, com ranking
            <strong>{selected_entity.get("best_ranking_text", "")}</strong>. Leitura temporal:
            <strong>{selected_entity.get("recency_profile", "")}</strong>.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="acr-detail-grid">'
        + detail_card("Faixa principal", str(selected_entity.get("best_band", "")))
        + detail_card("Estado recente", str(selected_entity.get("entity_status", "")))
        + detail_card("Concurso principal", str(selected_entity.get("best_contest_name", "")))
        + detail_card("Distancia do corte", format_number(selected_entity.get("best_delta_current")))
        + detail_card("Perfil temporal", str(selected_entity.get("recency_profile", "")))
        + detail_card("Ultimo ano nomeado", format_number(selected_entity.get("latest_named_year")))
        + detail_card("Concursos nos ultimos 2 anos", format_number(selected_entity.get("recent_2y_contest_count")))
        + detail_card("Nomeacoes nos ultimos 2 anos", format_number(selected_entity.get("recent_2y_named_count")))
        + "</div>",
        unsafe_allow_html=True,
    )

    summary_left, summary_right = st.columns([1.15, 1], gap="large")
    with summary_left:
        st.markdown("### Visao consolidada")
        st.dataframe(
            pd.DataFrame(
                [
                    ["Familias", selected_entity.get("families", "")],
                    ["Aliases", selected_entity.get("alias_names", "")],
                    ["Sinais fortes", format_number(selected_entity.get("strong_signal_count"))],
                    ["Sinais muito fortes", format_number(selected_entity.get("very_strong_signal_count"))],
                    ["Melhor rank % recente", f"{selected_entity.get('recent_2y_best_rank_percentile', 1):.2f}"],
                    ["Anos desde melhor resultado", format_number(selected_entity.get("years_since_best_result"))],
                ],
                columns=["Indicador", "Valor"],
            ),
            use_container_width=True,
            hide_index=True,
        )
    with summary_right:
        top_contests = (
            opp_rows.sort_values(["proximity_score", "delta_to_last_named"], ascending=[False, True], na_position="last")
            .head(8)
            .copy()
        )
        if not top_contests.empty:
            fig = px.bar(
                top_contests.sort_values("proximity_score", ascending=True),
                x="proximity_score",
                y="contest_name",
                color="near_pass_band",
                orientation="h",
                text="delta_to_last_named",
                color_discrete_map=BAND_COLOR_MAP,
            )
            fig.update_layout(
                height=290,
                margin=dict(l=8, r=8, t=12, b=8),
                xaxis_title="Score de proximidade",
                yaxis_title="",
                legend_title="Faixa",
            )
            fig.update_traces(texttemplate="Δ %{text}", textposition="outside", cliponaxis=False)
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("### Concursos do aluno")
    table_columns = [
        "contest_name",
        "contest_family",
        "contest_year",
        "near_pass_band",
        "ranking_text",
        "delta_to_last_named",
        "rank_percentile",
        "proximity_score",
        "student_named_elsewhere",
        "student_inside_elsewhere",
    ]
    if ui_mode == "Simples":
        table_columns = [
            "contest_name",
            "contest_family",
            "contest_year",
            "near_pass_band",
            "ranking_text",
            "delta_to_last_named",
            "proximity_score",
        ]

    pretty_rows = opp_rows.sort_values(
        ["proximity_score", "delta_to_last_named", "contest_year"],
        ascending=[False, True, False],
        na_position="last",
    )
    st.dataframe(
        pretty_rows[table_columns],
        use_container_width=True,
        hide_index=True,
        column_config={
            "contest_name": st.column_config.TextColumn("Concurso", width="large"),
            "contest_family": st.column_config.TextColumn("Familia", width="small"),
            "contest_year": st.column_config.NumberColumn("Ano", format="%d"),
            "near_pass_band": st.column_config.TextColumn("Faixa", width="small"),
            "ranking_text": st.column_config.TextColumn("Colocacao", width="medium"),
            "delta_to_last_named": st.column_config.NumberColumn("Dist. corte"),
            "rank_percentile": st.column_config.ProgressColumn("Rank %", min_value=0.0, max_value=1.0),
            "proximity_score": st.column_config.NumberColumn("Score", format="%.2f"),
            "student_named_elsewhere": st.column_config.NumberColumn("Nomeado fora"),
            "student_inside_elsewhere": st.column_config.NumberColumn("Dentro fora"),
        },
    )

    with st.expander("Ver leitura tecnica"):
        if pd.notna(selected_entity.get("score")):
            st.caption(selected_entity.get("score_breakdown", ""))
        st.caption(selected_entity.get("best_proximity_breakdown", ""))
        st.dataframe(
            opp_rows[
                [
                    "contest_name",
                    "proximity_breakdown",
                    "detected_columns",
                    "raw_row_text",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )


def contest_detail_tab(prepared: dict[str, pd.DataFrame]) -> None:
    st.subheader("Concurso")
    contests = prepared["contest_pages"].copy()
    candidates = prepared["candidates"].copy()
    if contests.empty:
        st.info("Nenhum concurso disponivel neste snapshot.")
        return

    selected_contest_value = st.session_state.get("selected_contest_value")
    default_index = 0
    if selected_contest_value is not None:
        matches = contests.index[contests["contest_value"].astype(str).eq(str(selected_contest_value))].tolist()
        if matches:
            default_index = matches[0]

    option_labels = contests.apply(lambda row: f"{row['contest_name']} [{row['contest_value']}]", axis=1).tolist()
    selected_label = st.selectbox("Selecione um concurso", option_labels, index=default_index)
    selected_row = contests.loc[
        contests.apply(lambda row: f"{row['contest_name']} [{row['contest_value']}]", axis=1).eq(selected_label)
    ].iloc[0]
    contest_value = str(selected_row["contest_value"])
    st.session_state["selected_contest_value"] = contest_value
    st.session_state["selected_contest_name"] = str(selected_row["contest_name"])

    contest_candidates = candidates[candidates["contest_value"].astype(str).eq(contest_value)].copy()
    contest_candidates = contest_candidates.sort_values("ranking_position", na_position="last")

    metrics = st.columns(4)
    metrics[0].metric("Ano", format_number(selected_row.get("contest_year")))
    metrics[1].metric("Candidatos", format_number(selected_row.get("candidates_count")))
    metrics[2].metric("Nomeados", format_number(selected_row.get("named_count")))
    metrics[3].metric("Dentro das vagas", format_number(selected_row.get("inside_vacancies_count")))

    left, right = st.columns([1.3, 1], gap="large")
    with left:
        st.dataframe(
            contest_candidates[
                [
                    "ranking_position",
                    "name",
                    "ranking_text",
                    "named",
                    "inside_vacancies",
                    "other_results_count",
                    "final_score",
                ]
            ],
            use_container_width=True,
            hide_index=True,
            column_config={
                "ranking_position": st.column_config.NumberColumn("Posicao"),
                "name": st.column_config.TextColumn("Aluno", width="medium"),
                "ranking_text": st.column_config.TextColumn("Colocacao", width="medium"),
                "named": st.column_config.CheckboxColumn("Nomeado"),
                "inside_vacancies": st.column_config.CheckboxColumn("Dentro"),
                "other_results_count": st.column_config.NumberColumn("Fez tb"),
                "final_score": st.column_config.NumberColumn("Nota", format="%.2f"),
            },
        )
    with right:
        top_slice = contest_candidates.head(20).copy()
        if not top_slice.empty:
            fig = px.bar(
                top_slice.sort_values("ranking_position", ascending=False),
                x="ranking_position",
                y="name",
                color="named",
                orientation="h",
            )
            fig.update_layout(
                height=520,
                margin=dict(l=8, r=8, t=12, b=8),
                xaxis_title="Posicao",
                yaxis_title="",
                legend_title="Nomeado",
            )
            st.plotly_chart(fig, use_container_width=True)


def timeline_tab(history: dict[str, pd.DataFrame], entity_table: pd.DataFrame) -> None:
    st.subheader("Evolucao")
    candidates_history = history["candidates_history"]
    if candidates_history.empty or candidates_history["snapshot_id"].nunique() < 2:
        st.info(
            "Ainda nao ha snapshots suficientes para uma timeline real. Assim que novas coletas entrarem, "
            "esta aba vai mostrar evolucao de posicao, reaparecimento em concursos e sinais concretos de continuidade de estudo."
        )
        return

    selected_name = st.selectbox("Aluno para timeline", entity_table["display_name"].head(500))
    selected_key = entity_table.loc[entity_table["display_name"] == selected_name, "identity_key"].iloc[0]
    rows = candidates_history[candidates_history["identity_key"] == selected_key].copy()
    if rows.empty:
        st.warning("Sem historico para este aluno.")
        return

    fig = px.line(
        rows.sort_values("snapshot_id"),
        x="snapshot_id",
        y="ranking_position",
        color="contest_name",
        markers=True,
        title="Evolucao de posicao por snapshot",
    )
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(height=420, margin=dict(l=8, r=8, t=18, b=8), xaxis_title="", yaxis_title="Posicao")
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(
        rows[
            [
                "snapshot_id",
                "contest_name",
                "ranking_text",
                "ranking_position",
                "final_score",
                "named",
                "inside_vacancies",
            ]
        ].sort_values(["contest_name", "snapshot_id"]),
        use_container_width=True,
        hide_index=True,
    )


def shortlist_tab(entity_table: pd.DataFrame) -> None:
    st.subheader("Operacao")
    shortlist = load_shortlist(SHORTLIST_PATH)
    if entity_table.empty:
        st.info("Sem entidades para shortlist com os filtros atuais.")
        return

    st.markdown(
        """
        <div class="acr-note">
            Aqui o ranking vira fila de acao. Voce escolhe a entidade, define status, prioridade, responsavel e notas.
        </div>
        """,
        unsafe_allow_html=True,
    )

    selected_name = st.selectbox("Aluno para shortlist", entity_table["display_name"].tolist(), key="shortlist_entity")
    selected_entity = entity_table[entity_table["display_name"] == selected_name].iloc[0]
    shortlist_row = shortlist[
        (shortlist["identity_key"].astype(str) == str(selected_entity["identity_key"]))
        & (shortlist["contest_value"].astype(str) == str(selected_entity["best_contest_value"]))
    ]

    default_status = SHORTLIST_STATUS[0]
    default_priority = SHORTLIST_PRIORITY[0]
    default_owner = ""
    default_notes = ""
    if not shortlist_row.empty:
        row = shortlist_row.iloc[0]
        default_status = row.get("status", default_status)
        default_priority = row.get("priority", default_priority)
        default_owner = row.get("owner", "")
        default_notes = row.get("notes", "")

    with st.form("shortlist_form"):
        status = st.selectbox("Status", SHORTLIST_STATUS, index=SHORTLIST_STATUS.index(default_status))
        priority = st.selectbox("Prioridade", SHORTLIST_PRIORITY, index=SHORTLIST_PRIORITY.index(default_priority))
        owner = st.text_input("Responsavel", value=default_owner)
        notes = st.text_area("Notas", value=default_notes)
        submitted = st.form_submit_button("Salvar shortlist")
        if submitted:
            upsert_shortlist(
                SHORTLIST_PATH,
                {
                    "identity_key": selected_entity["identity_key"],
                    "display_name": selected_entity["display_name"],
                    "contest_name": selected_entity["best_contest_name"],
                    "contest_value": selected_entity["best_contest_value"],
                    "status": status,
                    "priority": priority,
                    "owner": owner,
                    "notes": notes,
                },
            )
            st.success("Shortlist salva.")

    shortlist = load_shortlist(SHORTLIST_PATH)
    if shortlist.empty:
        st.info("A shortlist ainda esta vazia.")
        return
    st.dataframe(shortlist.sort_values("updated_at", ascending=False), use_container_width=True, hide_index=True)


def quality_tab(prepared: dict[str, pd.DataFrame]) -> None:
    st.subheader("Qualidade de dados")
    quality = prepared["quality"]
    subtab1, subtab2, subtab3, subtab4, subtab5 = st.tabs(
        ["Texto suspeito", "Nomes repetidos", "Aliases", "Layouts", "Concursos esparsos"]
    )
    with subtab1:
        st.dataframe(quality["suspicious_text"].head(500), use_container_width=True, hide_index=True)
    with subtab2:
        st.dataframe(quality["repeated_names"].head(500), use_container_width=True, hide_index=True)
    with subtab3:
        st.dataframe(quality["aliases"].head(500), use_container_width=True, hide_index=True)
    with subtab4:
        st.dataframe(quality["layouts"], use_container_width=True, hide_index=True)
    with subtab5:
        st.dataframe(
            quality["sparse_contests"][
                ["contest_name", "contest_family", "contest_year", "candidates_count", "named_count", "inside_vacancies_count"]
            ].head(500),
            use_container_width=True,
            hide_index=True,
        )


def adjustments_tab(prepared: dict[str, pd.DataFrame]) -> None:
    st.subheader("Ajustes")
    st.caption("Aqui ficam correcoes operacionais que alimentam o calculo do radar sem precisar editar os CSVs na mao.")

    contest_pages = prepared["contest_pages"].copy()
    manual_years = load_manual_years(MANUAL_YEAR_PATH)
    nomination_overrides = load_nomination_overrides(NOMINATION_OVERRIDE_PATH)

    year_tab, named_tab = st.tabs(["Ano manual dos concursos", "Indicador manual de nomeacoes"])

    with year_tab:
        st.markdown(
            """
            <div class="acr-note">
                Liste os concursos sem ano detectado e salve o ano correto. Assim o horizonte temporal passa a filtrar melhor.
            </div>
            """,
            unsafe_allow_html=True,
        )
        missing_year = contest_pages[contest_pages["contest_year"].isna()][["contest_value", "contest_name"]].drop_duplicates()
        st.write(f"**Concursos sem ano detectado:** {format_number(len(missing_year))}")
        st.dataframe(missing_year.head(500), use_container_width=True, hide_index=True)

        if not missing_year.empty:
            options = missing_year.sort_values("contest_name").apply(
                lambda row: f"{row['contest_name']} [{row['contest_value']}]", axis=1
            ).tolist()
            selected_label = st.selectbox("Concurso para informar ano", options, key="manual_year_contest")
            selected_row = missing_year.loc[
                missing_year.apply(lambda row: f"{row['contest_name']} [{row['contest_value']}]", axis=1).eq(selected_label)
            ].iloc[0]
            with st.form("manual_year_form"):
                manual_year = st.number_input("Ano do concurso", min_value=2000, max_value=2100, value=2025, step=1)
                submitted_year = st.form_submit_button("Salvar ano manual")
                if submitted_year:
                    upsert_manual_year(
                        MANUAL_YEAR_PATH,
                        str(selected_row["contest_value"]),
                        str(selected_row["contest_name"]),
                        int(manual_year),
                    )
                    st.cache_data.clear()
                    st.success("Ano manual salvo. Recarregue a pagina se quiser ver tudo imediatamente atualizado.")

        st.markdown("#### Anos manuais ja cadastrados")
        st.dataframe(manual_years.sort_values("updated_at", ascending=False), use_container_width=True, hide_index=True)

    with named_tab:
        st.markdown(
            """
            <div class="acr-note">
                Defina manualmente o ultimo nomeado. Se voce informar 50, o sistema entende que as posicoes 1 a 50 estao nomeadas naquele concurso.
            </div>
            """,
            unsafe_allow_html=True,
        )
        options_df = contest_pages[["contest_value", "contest_name", "named_count", "contest_year"]].drop_duplicates()
        option_labels = options_df.sort_values("contest_name").apply(
            lambda row: f"{row['contest_name']} [{row['contest_value']}]", axis=1
        ).tolist()
        selected_label = st.selectbox("Concurso para ajustar nomeacoes", option_labels, key="manual_named_contest")
        selected_row = options_df.loc[
            options_df.apply(lambda row: f"{row['contest_name']} [{row['contest_value']}]", axis=1).eq(selected_label)
        ].iloc[0]

        st.write(
            f"**Concurso atual:** {selected_row['contest_name']} | "
            f"**Ano:** {format_number(selected_row['contest_year'])} | "
            f"**Nomeados marcados hoje:** {format_number(selected_row['named_count'])}"
        )

        contest_candidates = prepared["candidates"][prepared["candidates"]["contest_value"].astype(str).eq(str(selected_row["contest_value"]))].copy()
        preview = contest_candidates[
            ["ranking_position", "name", "ranking_text", "named"]
        ].sort_values("ranking_position", na_position="last").head(80)
        st.dataframe(preview, use_container_width=True, hide_index=True)

        with st.form("nomination_override_form"):
            last_named_position = st.number_input("Ultimo nomeado manual", min_value=1, max_value=10000, value=50, step=1)
            submitted_named = st.form_submit_button("Salvar indicador de nomeacao")
            if submitted_named:
                upsert_nomination_override(
                    NOMINATION_OVERRIDE_PATH,
                    str(selected_row["contest_value"]),
                    str(selected_row["contest_name"]),
                    int(last_named_position),
                )
                st.cache_data.clear()
                st.success("Indicador manual salvo. Recarregue a pagina se quiser ver o radar imediatamente atualizado.")

        st.markdown("#### Indicadores manuais ja cadastrados")
        st.dataframe(nomination_overrides.sort_values("updated_at", ascending=False), use_container_width=True, hide_index=True)


def roadmap_tab() -> None:
    st.subheader("Proximo nivel")
    improvements = [
        (
            "1. Timeline real de estudo e progressao",
            "Com snapshots recorrentes, a pergunta deixa de ser so posicao atual e passa a ser evolucao real: subiu, reapareceu, manteve consistencia?",
        ),
        (
            "2. Resolucao de identidade assistida",
            "Aprimorar aliases e possiveis homonimos antes de scorear forte comercialmente.",
        ),
        (
            "3. Score supervisionado com retorno do time",
            "Usar feedback de abordagem e conversao para calibrar o que realmente significa proximidade util para o negocio.",
        ),
        (
            "4. Enriquecimento externo dos concursos",
            "Adicionar vagas oficiais, cadastro reserva, datas e banca para dar contexto objetivo de proximidade.",
        ),
        (
            "5. Integracao real com CRM",
            "Sincronizar shortlist, status e notas com o CRM da equipe.",
        ),
    ]
    for title, body in improvements:
        with st.container(border=True):
            st.markdown(f"**{title}**")
            st.write(body)


def main() -> None:
    inject_styles()
    sync_state_from_query_params()
    st.title("Scout dos proximos aprovados pela Base do Aprovado")
    st.caption("Radar para enxergar quem esta realmente chegando perto da aprovacao.")
    st.caption(APP_BUILD)
    if not require_password():
        return

    snapshot_ids = list_snapshots()
    if not snapshot_ids:
        st.error(f"Nenhum snapshot compativel foi encontrado em {OUTPUT_DIR}.")
        return

    selected_snapshot = st.session_state.get("selected_snapshot", snapshot_ids[0])
    if selected_snapshot not in snapshot_ids:
        selected_snapshot = snapshot_ids[0]
    prepared = load_prepared_snapshot(selected_snapshot)
    selected_snapshot_new, filtered_opportunities, filtered_students, proximity_preset, ui_mode, filter_summary, selected_radar_columns = top_controls(
        snapshot_ids,
        selected_snapshot,
        prepared,
    )
    st.session_state["ui_mode_current"] = ui_mode
    if selected_snapshot_new != selected_snapshot:
        st.session_state["selected_snapshot"] = selected_snapshot_new
        st.rerun()
    score_calibration = load_score_calibration(selected_snapshot_new, prepared["candidates"])
    entity_table, scored_opportunities = compute_views(
        prepared,
        filtered_opportunities,
        filtered_students,
        proximity_preset,
        score_calibration,
    )

    st.markdown('<div class="acr-nav">', unsafe_allow_html=True)
    current_view = st.segmented_control(
        "Area",
        ["Radar", "Aluno", "Concurso", "Ajustes", "Qualidade"],
        default=st.session_state.get("current_view", "Radar"),
        key="current_view",
    )
    current_query_view = read_query_value("view")
    if current_query_view != current_view:
        st.query_params.clear()
        st.query_params["view"] = current_view
        if current_view == "Aluno" and st.session_state.get("selected_entity_name"):
            st.query_params["student"] = st.session_state["selected_entity_name"]
        if current_view == "Concurso" and st.session_state.get("selected_contest_value"):
            st.query_params["contest"] = st.session_state["selected_contest_value"]
    st.markdown("</div>", unsafe_allow_html=True)

    if current_view == "Radar":
        main_entity_tab(
            prepared,
            entity_table,
            scored_opportunities,
            score_calibration,
            ui_mode,
            filter_summary,
            selected_radar_columns,
        )
    elif current_view == "Aluno":
        entity_detail_tab(entity_table, scored_opportunities, ui_mode)
    elif current_view == "Concurso":
        contest_detail_tab(prepared)
    elif current_view == "Ajustes":
        adjustments_tab(prepared)
    else:
        quality_tab(prepared)


if __name__ == "__main__":
    main()
