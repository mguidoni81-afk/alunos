from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

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

STUDENT_SCORE_PRESETS = {
    "Equilibrado": DEFAULT_WEIGHTS,
    "Comercial": {
        **DEFAULT_WEIGHTS,
        "contest_count": 1.4,
        "named_count": 0.6,
        "inside_vacancies_count": 1.0,
        "best_rank_percentile": 1.7,
        "top_10_count": 1.2,
        "top_50_count": 0.8,
        "other_results_total": 1.0,
        "contest_family_count": 0.7,
        "nomination_link_count": 0.6,
        "named_history_penalty": 0.8,
    },
}

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


st.set_page_config(
    page_title="Alunos Consultoria Ranking",
    layout="wide",
    initial_sidebar_state="expanded",
)


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.4rem;
            padding-bottom: 2rem;
        }
        .acr-note {
            padding: 0.9rem 1rem;
            border: 1px solid #dbe4f0;
            border-radius: 14px;
            background: #f8fbff;
            margin-bottom: 0.9rem;
        }
        .acr-soft {
            padding: 0.9rem 1rem;
            border-radius: 14px;
            background: #f5f7fb;
            border: 1px solid #e7ecf3;
        }
        .acr-hero {
            padding: 1.1rem 1.2rem;
            border-radius: 18px;
            background: linear-gradient(135deg, #f7fbff 0%, #eef6ff 100%);
            border: 1px solid #dbe8f4;
            margin-bottom: 1rem;
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
            padding: 1rem;
            border-radius: 16px;
            border: 1px solid #e5ebf2;
            background: #ffffff;
            min-height: 128px;
            margin-bottom: 0.8rem;
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
    prepared["students"] = build_student_table(prepared["candidates"])
    prepared["opportunities"] = build_opportunity_table(prepared["candidates"], prepared["students"])
    prepared["quality"] = build_quality_tables(prepared["candidates"], prepared["contest_pages"])
    return prepared


@st.cache_data(show_spinner=False)
def load_history() -> dict[str, pd.DataFrame]:
    history = load_all_snapshots_history(OUTPUT_DIR)
    return prepare_history_frames(history)


def explain_caption(show: bool, text: str) -> None:
    if show:
        st.sidebar.caption(text)


def get_reference_year(prepared: dict[str, pd.DataFrame]) -> int:
    years = prepared["candidates"]["contest_year"].dropna()
    current_year = datetime.now().year
    if years.empty:
        return current_year
    return max(int(years.max()), current_year)


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


def render_filter_chips(items: list[str]) -> None:
    if not items:
        return
    chips = "".join(f'<span class="acr-chip">{item}</span>' for item in items)
    st.markdown(f'<div class="acr-chip-row">{chips}</div>', unsafe_allow_html=True)


def sidebar_controls(prepared: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame, str, str, list[str]]:
    opportunities = prepared["opportunities"]
    students = prepared["students"]

    explain_controls = st.sidebar.toggle("Explicar ajustes", value=True)
    ui_mode = st.sidebar.radio("Modo de visualizacao", ["Simples", "Avancado"], index=0)
    explain_caption(explain_controls, "Simples destaca leitura executiva. Avancado deixa mais contexto tecnico visivel.")

    st.sidebar.header("Objetivo")
    proximity_preset = st.sidebar.selectbox("Preset principal", list(PROXIMITY_PRESETS.keys()), index=0)
    explain_caption(explain_controls, "Troca a logica base do ranking. Um preset mais agressivo sobe quem parece mais perto do corte.")

    st.sidebar.header("Janela de analise")
    reference_year = get_reference_year(prepared)
    horizon_label = st.sidebar.selectbox("Horizonte temporal", list(TIME_HORIZONS.keys()), index=0)
    include_unknown_years = st.sidebar.checkbox("Incluir concursos sem ano identificavel", value=True)
    explain_caption(
        explain_controls,
        f"O horizonte usa o ano inferido do concurso. Referencia atual: {reference_year}. Diminuir o horizonte foca no desempenho recente.",
    )

    st.sidebar.header("Filtros principais")
    selected_families = st.sidebar.multiselect(
        "Familias de concurso",
        sorted(opportunities["contest_family"].dropna().unique().tolist()),
        default=[],
    )
    explain_caption(explain_controls, "Adicionar familias estreita o radar. Remover familias amplia a base analisada.")

    max_rank_percentile = st.sidebar.slider("Rank percentual maximo", 0.01, 1.0, 0.20, 0.01)
    explain_caption(
        explain_controls,
        "Diminuir deixa so alunos mais bem colocados em cada concurso. Aumentar abre a analise para perfis mais distantes do topo.",
    )

    max_delta_named = st.sidebar.slider("Maxima distancia ate o ultimo nomeado", 1, 500, 100, 1)
    explain_caption(explain_controls, "Diminuir aproxima o radar do corte atual. Aumentar inclui oportunidades menos quentes.")

    min_other_results = st.sidebar.slider("Minimo de resultados cruzados", 0, 250, 0)
    explain_caption(explain_controls, "Aumentar exige historico mais denso em outros rankings. Diminuir deixa entrar perfis com menos rastros.")

    exclude_current_named = st.sidebar.checkbox("Excluir ja nomeados no concurso atual", value=True)
    explain_caption(explain_controls, "Ligado foca em quem ainda pode passar. Desligado inclui tambem quem ja foi nomeado.")

    filtered_opportunities = opportunities.copy()
    filtered_opportunities = apply_time_horizon(
        filtered_opportunities,
        TIME_HORIZONS[horizon_label],
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
    if selected_families:
        filter_summary.append(f"{len(selected_families)} familias")
    if min_other_results > 0:
        filter_summary.append(f"Fez tb >= {min_other_results}")
    if exclude_current_named:
        filter_summary.append("Exclui nomeados")

    return filtered_opportunities, filtered_students, proximity_preset, ui_mode, filter_summary


def compute_views(
    prepared: dict[str, pd.DataFrame],
    filtered_opportunities: pd.DataFrame,
    filtered_students: pd.DataFrame,
    proximity_preset: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    scored_opportunities = compute_opportunity_scores(filtered_opportunities, PROXIMITY_PRESETS[proximity_preset])
    entity_table = build_entity_proximity_table(scored_opportunities)
    scored_students = compute_student_scores(filtered_students, STUDENT_SCORE_PRESETS["Equilibrado"])
    entity_table = entity_table.merge(
        scored_students[["identity_key", "score", "score_breakdown"]],
        on="identity_key",
        how="left",
    )
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

    with st.expander("Ver contexto tecnico da base"):
        metric_card_columns(prepared, entity_table)


def radar_table(entity_table: pd.DataFrame, ui_mode: str) -> None:
    if entity_table.empty:
        st.info("Nenhum aluno atende aos filtros atuais.")
        return

    base_columns = [
        "display_name",
        "best_band",
        "best_contest_name",
        "best_delta_current",
        "best_rank_percentile_current",
        "entity_proximity_score",
        "contest_count",
    ]
    advanced_columns = [
        "best_contest_year",
        "best_ranking_text",
        "strong_signal_count",
        "very_strong_signal_count",
        "alias_count",
    ]
    columns = base_columns if ui_mode == "Simples" else base_columns + advanced_columns

    st.dataframe(
        entity_table[columns].head(25 if ui_mode == "Simples" else 100),
        use_container_width=True,
        hide_index=True,
        column_config={
            "entity_proximity_score": st.column_config.NumberColumn("Score", format="%.2f"),
            "best_delta_current": st.column_config.NumberColumn("Dist. corte"),
            "best_rank_percentile_current": st.column_config.ProgressColumn("Rank %", min_value=0.0, max_value=1.0),
            "contest_count": st.column_config.NumberColumn("Concursos"),
            "best_contest_name": st.column_config.TextColumn("Concurso principal", width="large"),
        },
    )


def main_entity_tab(
    prepared: dict[str, pd.DataFrame],
    entity_table: pd.DataFrame,
    ui_mode: str,
    filter_summary: list[str],
) -> None:
    st.subheader("Quem esta proximo de passar?")
    st.markdown(
        """
        <div class="acr-hero">
            <div class="acr-section-title">Radar principal</div>
            A leitura agora acontece em camadas. Primeiro o recorte ativo, depois os nomes mais quentes,
            e so depois o detalhe tecnico.
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_filter_chips(filter_summary)
    render_primary_metrics(prepared, entity_table)

    left, right = st.columns([1.8, 1.2])
    with left:
        st.markdown("### Top alunos do recorte atual")
        radar_table(entity_table, ui_mode)
    with right:
        band_counts = entity_table["best_band"].fillna("Sem faixa").value_counts().reset_index()
        band_counts.columns = ["faixa", "count"]
        fig_band = px.bar(
            band_counts,
            x="faixa",
            y="count",
            color="faixa",
            title="Distribuicao por faixa",
        )
        fig_band.update_layout(showlegend=False, height=360, margin=dict(l=10, r=10, t=50, b=10))
        st.plotly_chart(fig_band, use_container_width=True)

        st.markdown("#### Leitura rapida")
        quick_read = entity_table.head(10)[["display_name", "best_contest_name", "best_band"]].copy()
        quick_read.columns = ["Aluno", "Concurso principal", "Faixa"]
        st.dataframe(quick_read, use_container_width=True, hide_index=True)

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
                    title="Entidades por proximidade ao corte",
                )
                fig_scatter.update_yaxes(autorange="reversed")
                st.plotly_chart(fig_scatter, use_container_width=True)


def entity_detail_tab(entity_table: pd.DataFrame, scored_opportunities: pd.DataFrame, ui_mode: str) -> None:
    st.subheader("Aluno")
    st.caption("O detalhe individual fica concentrado aqui para o radar principal ficar mais limpo.")

    if entity_table.empty:
        st.info("Nenhum aluno atende aos filtros atuais.")
        return

    selected_name = st.selectbox("Selecione um aluno", entity_table["display_name"].tolist())
    selected_entity = entity_table[entity_table["display_name"] == selected_name].iloc[0]
    opp_rows = scored_opportunities[scored_opportunities["identity_key"] == selected_entity["identity_key"]].copy()

    cols = st.columns(4)
    cols[0].metric("Faixa principal", selected_entity.get("best_band", ""))
    cols[1].metric("Concurso principal", selected_entity.get("best_contest_name", ""))
    cols[2].metric("Dist. do corte", format_number(selected_entity.get("best_delta_current")))
    cols[3].metric("Aliases", format_number(selected_entity.get("alias_count") or 1))

    st.markdown(
        f"""
        <div class="acr-soft">
            <strong>Leitura curta:</strong> {selected_entity.get("display_name", "")} aparece melhor em
            <strong>{selected_entity.get("best_contest_name", "")}</strong>, na faixa
            <strong>{selected_entity.get("best_band", "")}</strong>, com ranking
            <strong>{selected_entity.get("best_ranking_text", "")}</strong>.
        </div>
        """,
        unsafe_allow_html=True,
    )

    tab1, tab2, tab3 = st.tabs(["Resumo", "Concursos do aluno", "Leitura tecnica"])
    with tab1:
        left, right = st.columns([1.3, 1])
        with left:
            st.write(f"**Familias em que aparece:** {selected_entity.get('families', '')}")
            st.write(f"**Aliases:** {selected_entity.get('alias_names', '')}")
            st.write(
                f"**Sinais fortes:** {int(selected_entity.get('strong_signal_count', 0) or 0)} | "
                f"**Sinais muito fortes:** {int(selected_entity.get('very_strong_signal_count', 0) or 0)}"
            )
        with right:
            if pd.notna(selected_entity.get("score")):
                st.caption(selected_entity.get("score_breakdown", ""))
            st.caption(selected_entity.get("best_proximity_breakdown", ""))

    with tab2:
        columns = [
            "contest_name",
            "contest_family",
            "contest_year",
            "near_pass_band",
            "proximity_score",
            "ranking_text",
            "delta_to_last_named",
            "rank_percentile",
            "student_named_elsewhere",
            "student_inside_elsewhere",
        ]
        if ui_mode == "Simples":
            columns = [
                "contest_name",
                "contest_family",
                "contest_year",
                "near_pass_band",
                "ranking_text",
                "delta_to_last_named",
            ]

        st.dataframe(
            opp_rows[columns],
            use_container_width=True,
            hide_index=True,
            column_config={
                "proximity_score": st.column_config.NumberColumn("Score", format="%.2f"),
                "rank_percentile": st.column_config.ProgressColumn("Rank %", min_value=0.0, max_value=1.0),
            },
        )

    with tab3:
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


def timeline_tab(history: dict[str, pd.DataFrame], entity_table: pd.DataFrame) -> None:
    st.subheader("Linha do tempo")
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
    st.title("Alunos Consultoria Ranking")
    st.caption("Pergunta central: quem esta proximo de passar?")

    snapshot_ids = list_snapshots()
    if not snapshot_ids:
        st.error(f"Nenhum snapshot compativel foi encontrado em {OUTPUT_DIR}.")
        return

    selected_snapshot = st.sidebar.selectbox("Snapshot", snapshot_ids, index=0)
    prepared = load_prepared_snapshot(selected_snapshot)
    history = load_history()
    filtered_opportunities, filtered_students, proximity_preset, ui_mode, filter_summary = sidebar_controls(prepared)
    entity_table, scored_opportunities = compute_views(prepared, filtered_opportunities, filtered_students, proximity_preset)

    tabs = st.tabs(["Radar Principal", "Aluno", "Timeline", "Operacao", "Qualidade", "Proximo Nivel"])
    with tabs[0]:
        main_entity_tab(prepared, entity_table, ui_mode, filter_summary)
    with tabs[1]:
        entity_detail_tab(entity_table, scored_opportunities, ui_mode)
    with tabs[2]:
        timeline_tab(history, entity_table)
    with tabs[3]:
        shortlist_tab(entity_table)
    with tabs[4]:
        quality_tab(prepared)
    with tabs[5]:
        roadmap_tab()


if __name__ == "__main__":
    main()
