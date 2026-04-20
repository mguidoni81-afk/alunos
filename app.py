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
    "Quem está mais perto": DEFAULT_PROXIMITY_WEIGHTS,
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
    "Todo o histórico": None,
    "Últimos 2 anos": 2,
    "Últimos 3 anos": 3,
    "Últimos 5 anos": 5,
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
        .acr-note {
            padding: 0.9rem 1rem;
            border: 1px solid #dbe4f0;
            border-radius: 12px;
            background: #f8fbff;
            margin-bottom: 0.75rem;
        }
        .acr-soft {
            padding: 0.9rem 1rem;
            border-radius: 12px;
            background: #f5f7fb;
            border: 1px solid #e7ecf3;
        }
        .acr-section-title {
            margin-top: 0.25rem;
            margin-bottom: 0.25rem;
            font-weight: 700;
            font-size: 1.05rem;
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


def apply_time_horizon(opportunities: pd.DataFrame, horizon_years: int | None, include_unknown_years: bool, reference_year: int) -> pd.DataFrame:
    if horizon_years is None:
        if include_unknown_years:
            return opportunities
        return opportunities[opportunities["contest_year"].notna()]

    min_year = reference_year - horizon_years + 1
    year_ok = opportunities["contest_year"].fillna(-1).ge(min_year)
    if include_unknown_years:
        year_ok = year_ok | opportunities["contest_year"].isna()
    return opportunities[year_ok]


def sidebar_controls(prepared: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    opportunities = prepared["opportunities"]
    students = prepared["students"]
    explain_controls = st.sidebar.toggle("Explicar ajustes", value=True)

    st.sidebar.header("Objetivo")
    proximity_preset = st.sidebar.selectbox("Preset principal", list(PROXIMITY_PRESETS.keys()), index=0)
    explain_caption(explain_controls, "Troca a lógica base do ranking principal. Um preset mais agressivo prioriza quem parece mais perto do corte.")

    st.sidebar.header("Janela de análise")
    reference_year = get_reference_year(prepared)
    horizon_label = st.sidebar.selectbox("Horizonte temporal", list(TIME_HORIZONS.keys()), index=0)
    include_unknown_years = st.sidebar.checkbox("Incluir concursos sem ano identificável", value=True)
    explain_caption(
        explain_controls,
        f"O horizonte usa o ano inferido do nome do concurso. Referência atual: {reference_year}. Diminuir o horizonte foca no aluno com desempenho mais recente."
    )

    st.sidebar.header("Filtros principais")
    selected_families = st.sidebar.multiselect(
        "Famílias de concurso",
        sorted(opportunities["contest_family"].dropna().unique().tolist()),
        default=[],
    )
    explain_caption(explain_controls, "Adicionar famílias estreita o universo. Remover famílias amplia a base analisada.")

    max_rank_percentile = st.sidebar.slider("Rank percentual máximo", 0.01, 1.0, 0.20, 0.01)
    explain_caption(explain_controls, "Diminuir este valor deixa só alunos mais bem colocados dentro de cada concurso. Aumentar abre a análise para perfis mais distantes do topo.")

    max_delta_named = st.sidebar.slider("Máxima distância até o último nomeado", 1, 500, 100, 1)
    explain_caption(explain_controls, "Diminuir aproxima o radar do corte atual. Aumentar permite incluir oportunidades mais distantes, porém menos quentes.")

    min_other_results = st.sidebar.slider("Mínimo de resultados cruzados", 0, 250, 0)
    explain_caption(explain_controls, "Aumentar exige histórico mais denso em outros rankings. Diminuir deixa entrar alunos com menos rastros na base.")

    exclude_current_named = st.sidebar.checkbox("Excluir já nomeados no concurso atual", value=True)
    explain_caption(explain_controls, "Ligado: foca em quem ainda pode passar. Desligado: inclui também quem já foi nomeado, útil para entender força histórica.")

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
    return filtered_opportunities, filtered_students, proximity_preset


def compute_views(prepared: dict[str, pd.DataFrame], filtered_opportunities: pd.DataFrame, filtered_students: pd.DataFrame, proximity_preset: str) -> tuple[pd.DataFrame, pd.DataFrame]:
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
    cols[0].metric("Concursos", f"{len(contests):,}".replace(",", "."))
    cols[1].metric("Linhas de candidatos", f"{len(candidates):,}".replace(",", "."))
    cols[2].metric("Alunos consolidados", f"{len(students):,}".replace(",", "."))
    cols[3].metric("Entidades no radar", f"{len(entity_table):,}".replace(",", "."))
    cols[4].metric("Sinais muito fortes", f"{int(entity_table['very_strong_signal_count'].fillna(0).sum()):,}".replace(",", "."))
    cols[5].metric("Sinais fortes", f"{int(entity_table['strong_signal_count'].fillna(0).sum()):,}".replace(",", "."))


def main_entity_tab(prepared: dict[str, pd.DataFrame], entity_table: pd.DataFrame, scored_opportunities: pd.DataFrame) -> None:
    st.subheader("Quem Está Próximo de Passar?")
    st.markdown(
        """
        <div class="acr-note">
            <div class="acr-section-title">Leitura principal</div>
            Agora o dashboard trata <strong>cada aluno como uma entidade única</strong>. O ranking principal resume o melhor contexto competitivo
            de cada aluno e agrega os sinais secundários que reforçam a leitura de proximidade.
        </div>
        """,
        unsafe_allow_html=True,
    )

    metric_card_columns(prepared, entity_table)

    st.markdown("### Ranking principal de entidades")
    st.dataframe(
        entity_table[
            [
                "display_name",
                "entity_proximity_score",
                "best_band",
                "best_contest_name",
                "best_contest_year",
                "best_ranking_text",
                "best_delta_current",
                "best_rank_percentile_current",
                "strong_signal_count",
                "very_strong_signal_count",
                "contest_count",
                "alias_count",
            ]
        ].head(100),
        use_container_width=True,
        hide_index=True,
        column_config={
            "entity_proximity_score": st.column_config.NumberColumn("Score de proximidade", format="%.2f"),
            "best_rank_percentile_current": st.column_config.ProgressColumn("Melhor rank %", min_value=0.0, max_value=1.0),
        },
    )

    left, right = st.columns([2, 1])
    band_counts = entity_table["best_band"].value_counts().reset_index()
    band_counts.columns = ["faixa", "count"]
    fig_band = px.bar(band_counts, x="faixa", y="count", color="faixa", title="Faixa principal por entidade")
    left.plotly_chart(fig_band, use_container_width=True)

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
        right.plotly_chart(fig_scatter, use_container_width=True)

    st.markdown("### Perfil da entidade")
    if entity_table.empty:
        st.info("Nenhum aluno atende aos filtros atuais.")
        return

    selected_name = st.selectbox("Selecione um aluno", entity_table["display_name"].tolist())
    selected_entity = entity_table[entity_table["display_name"] == selected_name].iloc[0]
    opp_rows = scored_opportunities[scored_opportunities["identity_key"] == selected_entity["identity_key"]].copy()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Score entidade", f"{selected_entity['entity_proximity_score']:.2f}")
    c2.metric("Melhor faixa", selected_entity["best_band"])
    c3.metric("Concurso principal", selected_entity["best_contest_name"])
    c4.metric("Δ até último nomeado", int(selected_entity["best_delta_current"]) if pd.notna(selected_entity["best_delta_current"]) else "N/A")
    c5.metric("Aliases detectados", int(selected_entity["alias_count"]) if pd.notna(selected_entity["alias_count"]) else 1)

    tab1, tab2, tab3 = st.tabs(["Resumo", "Concursos do aluno", "Leitura técnica"])
    with tab1:
        st.write(f"**Famílias em que aparece:** {selected_entity.get('families', '')}")
        st.write(f"**Aliases:** {selected_entity.get('alias_names', '')}")
        st.write(
            f"**Melhor contexto atual:** {selected_entity.get('best_contest_name', '')} | {selected_entity.get('best_ranking_text', '')}"
        )
        st.write(
            f"**Sinais fortes:** {int(selected_entity.get('strong_signal_count', 0) or 0)} | "
            f"**Sinais muito fortes:** {int(selected_entity.get('very_strong_signal_count', 0) or 0)}"
        )
        if pd.notna(selected_entity.get("score")):
            st.caption(selected_entity.get("score_breakdown", ""))
        st.caption(selected_entity.get("best_proximity_breakdown", ""))
    with tab2:
        st.dataframe(
            opp_rows[
                [
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
            ],
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
    st.subheader("Linha do Tempo")
    candidates_history = history["candidates_history"]
    if candidates_history.empty or candidates_history["snapshot_id"].nunique() < 2:
        st.info("Ainda não há snapshots suficientes para uma timeline real. Assim que novas coletas entrarem, esta aba vai mostrar evolução de posição, reaparecimento em concursos e sinais concretos de continuidade de estudo.")
        return

    selected_name = st.selectbox("Aluno para timeline", entity_table["display_name"].head(500))
    selected_key = entity_table.loc[entity_table["display_name"] == selected_name, "identity_key"].iloc[0]
    rows = candidates_history[candidates_history["identity_key"] == selected_key].copy()
    if rows.empty:
        st.warning("Sem histórico para este aluno.")
        return

    fig = px.line(
        rows.sort_values("snapshot_id"),
        x="snapshot_id",
        y="ranking_position",
        color="contest_name",
        markers=True,
        title="Evolução de posição por snapshot",
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
    st.subheader("Shortlist Operacional")
    shortlist = load_shortlist(SHORTLIST_PATH)
    if entity_table.empty:
        st.info("Sem entidades para shortlist com os filtros atuais.")
        return

    st.markdown(
        """
        <div class="acr-note">
            A shortlist é o embrião do workflow comercial. Você escolhe a entidade, define status, prioridade, responsável e notas.
            Isso já transforma o dashboard em fila de ação.
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

    with st.form("shortlist_form"):
        status = st.selectbox("Status", SHORTLIST_STATUS, index=0)
        priority = st.selectbox("Prioridade", SHORTLIST_PRIORITY, index=0)
        owner = st.text_input("Responsável")
        notes = st.text_area("Notas")
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
        st.info("A shortlist ainda está vazia.")
        return
    st.dataframe(shortlist.sort_values("updated_at", ascending=False), use_container_width=True, hide_index=True)


def quality_tab(prepared: dict[str, pd.DataFrame]) -> None:
    st.subheader("Qualidade de Dados")
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
    st.subheader("Próximo Nível")
    improvements = [
        (
            "1. Timeline real de estudo e progressão",
            "Com snapshots recorrentes, a pergunta deixa de ser só posição atual e passa a ser evolução real: subiu, reapareceu, manteve consistência?"
        ),
        (
            "2. Resolução de identidade assistida",
            "Aprimorar aliases e possíveis homônimos antes de scorear forte comercialmente."
        ),
        (
            "3. Score supervisionado com retorno do time",
            "Usar feedback de abordagem e conversão para calibrar o que realmente significa proximidade útil para o negócio."
        ),
        (
            "4. Enriquecimento externo dos concursos",
            "Adicionar vagas oficiais, cadastro reserva, datas e banca para dar contexto objetivo de proximidade."
        ),
        (
            "5. Integração real com CRM",
            "Sincronizar shortlist, status e notas com o CRM da equipe."
        ),
    ]
    for title, body in improvements:
        with st.container(border=True):
            st.markdown(f"**{title}**")
            st.write(body)


def main() -> None:
    inject_styles()
    st.title("Alunos Consultoria Ranking")
    st.caption("Pergunta central: quem está próximo de passar?")

    snapshot_ids = list_snapshots()
    if not snapshot_ids:
        st.error(f"Nenhum snapshot compatível foi encontrado em {OUTPUT_DIR}.")
        return

    selected_snapshot = st.sidebar.selectbox("Snapshot", snapshot_ids, index=0)
    prepared = load_prepared_snapshot(selected_snapshot)
    history = load_history()
    filtered_opportunities, filtered_students, proximity_preset = sidebar_controls(prepared)
    entity_table, _scored_opportunities = compute_views(prepared, filtered_opportunities, filtered_students, proximity_preset)

    tabs = st.tabs(["Radar Principal", "Timeline", "Shortlist", "Qualidade", "Próximo Nível"])
    with tabs[0]:
        main_entity_tab(prepared, entity_table, _scored_opportunities)
    with tabs[1]:
        timeline_tab(history, entity_table)
    with tabs[2]:
        shortlist_tab(entity_table)
    with tabs[3]:
        quality_tab(prepared)
    with tabs[4]:
        roadmap_tab()


if __name__ == "__main__":
    main()
