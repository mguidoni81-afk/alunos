// ==UserScript==
// @name         Ranking dos Concursos Scraper
// @namespace    https://rankingdosconcursos.com.br/
// @version      0.2.0
// @description  Extrai dados brutos do Ranking dos Concursos para JSON e CSV.
// @author       Codex
// @match        https://www.rankingdosconcursos.com.br/*
// @grant        none
// ==/UserScript==

(function () {
  "use strict";

  const GREEN_STATUS = "#22c55e";
  const BLUE_STATUS = "#3b82f6";
  const PANEL_ID = "alunos-consultoria-scraper-panel";
  const DEFAULT_DELAY_MS = 1200;

  function cleanText(value) {
    return (value || "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
  }

  function parseNumber(value) {
    const text = cleanText(value);
    if (!text) {
      return null;
    }

    const normalized = text.includes(",")
      ? text.replace(/\./g, "").replace(",", ".")
      : text;
    const parsed = Number.parseFloat(normalized);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function parsePlacement(value) {
    const match = cleanText(value).match(/(\d+)/);
    return match ? Number.parseInt(match[1], 10) : null;
  }

  function stripLeadingPlacement(value) {
    return cleanText(value).replace(/^\d+\s*[^A-Za-z0-9]*\s*/, "").trim();
  }

  function slugify(value) {
    return cleanText(value)
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "") || "concurso";
  }

  function escapeCsv(value) {
    if (value === null || value === undefined) {
      return "";
    }

    const text = String(value);
    if (/[",\n]/.test(text)) {
      return `"${text.replace(/"/g, '""')}"`;
    }
    return text;
  }

  function toCsv(rows, columns) {
    const header = columns.map((column) => escapeCsv(column.header)).join(",");
    const body = rows.map((row) =>
      columns.map((column) => escapeCsv(row[column.key])).join(",")
    );
    return [header, ...body].join("\n");
  }

  function downloadText(filename, content, mimeType) {
    const blob = new Blob([content], { type: `${mimeType};charset=utf-8` });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  function absoluteUrl(href) {
    if (!href) {
      return null;
    }
    return new URL(href, window.location.origin).toString();
  }

  function getCurrentFilters(doc = document, url = new URL(window.location.href)) {
    const checked = doc.querySelector('input[name="tC"]:checked');
    return {
      track_type: checked ? checked.value : url.searchParams.get("tC") || "2",
      view_type: url.searchParams.get("tV") || "0",
    };
  }

  function getContestOptions(doc = document) {
    const select = doc.querySelector("#sCa");
    if (!select) {
      return [];
    }

    return Array.from(select.options)
      .filter((option) => option.value)
      .map((option) => ({
        value: option.value,
        full_text: cleanText(option.dataset.fulltext || option.textContent),
        display_text: cleanText(option.textContent),
        selected: option.selected,
      }));
  }

  function getStatusFlags(container) {
    const spans = Array.from(container.querySelectorAll('span[style*="background-color"]'));
    const joinedStyle = spans
      .map((span) => (span.getAttribute("style") || "").toLowerCase())
      .join(" ");
    const joinedTitle = spans
      .map((span) => (span.getAttribute("title") || "").toLowerCase())
      .join(" ");

    return {
      named: joinedStyle.includes(BLUE_STATUS) || joinedTitle.includes("nomeado"),
      inside_vacancies: joinedStyle.includes(GREEN_STATUS) || joinedTitle.includes("dentro das vagas"),
    };
  }

  function parseNominationLink(nameCell) {
    const link = nameCell.querySelector('a[href*="nomeacao_email.php"]');
    if (!link) {
      return {
        nomination_link_href: null,
        nomination_token: null,
        inscription: null,
        contest_id: null,
        cargo_id: null,
        nomination_view_type: null,
        nomination_placement_param: null,
        nomination_candidate_name_param: null,
        nomination_contest_name_param: null,
        nomination_cargo_name_param: null,
        nomination_return_path: null,
      };
    }

    const url = new URL(link.getAttribute("href"), window.location.origin);
    const params = url.searchParams;

    return {
      nomination_link_href: url.toString(),
      nomination_token: params.get("t"),
      inscription: params.get("inscricao"),
      contest_id: params.get("id_concurso"),
      cargo_id: params.get("id_cargo"),
      nomination_view_type: params.get("tv"),
      nomination_placement_param: params.get("colocacao"),
      nomination_candidate_name_param: params.get("nome"),
      nomination_contest_name_param: params.get("concurso"),
      nomination_cargo_name_param: params.get("cargo"),
      nomination_return_path: params.get("return"),
    };
  }

  function parseCrossContestResults(cell) {
    const items = [];
    let buffer = [];

    function flush() {
      if (!buffer.length) {
        return;
      }

      const wrapper = cell.ownerDocument.createElement("div");
      buffer.forEach((node) => wrapper.appendChild(node.cloneNode(true)));
      const text = cleanText(wrapper.textContent);
      if (!text) {
        buffer = [];
        return;
      }

      const link = wrapper.querySelector("a[href]");
      const href = link ? link.getAttribute("href") : null;
      const url = href ? new URL(href, window.location.origin) : null;
      const flags = getStatusFlags(wrapper);

      items.push({
        contest_label: stripLeadingPlacement(text),
        contest_value: url ? url.searchParams.get("sCa") : null,
        ranking_text: text,
        ranking_position: parsePlacement(text),
        named: flags.named,
        inside_vacancies: flags.inside_vacancies,
        href: url ? url.toString() : null,
      });

      buffer = [];
    }

    Array.from(cell.childNodes).forEach((node) => {
      if (node.nodeName === "BR") {
        flush();
        return;
      }
      buffer.push(node);
    });
    flush();

    return items;
  }

  function normalizeHeaderName(value) {
    return cleanText(value)
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function extractTableSchema(table) {
    const headers = Array.from(table.querySelectorAll("thead th")).map((header, index) => ({
      index,
      raw_text: cleanText(header.textContent),
      normalized_text: normalizeHeaderName(header.textContent),
    }));

    return headers;
  }

  function findCellByHeader(cells, headers, matcher) {
    const header = headers.find((item) => matcher(item.normalized_text, item.raw_text));
    if (!header) {
      return null;
    }
    return cells[header.index] || null;
  }

  function parseCandidateRow(row, headers) {
    const cells = Array.from(row.querySelectorAll(":scope > td"));
    if (cells.length < 4) {
      return null;
    }

    const statusCell = cells[0] || null;
    const nameCell =
      findCellByHeader(cells, headers, (normalized) => normalized === "nome") ||
      cells[1] ||
      null;

    const objectiveCell = findCellByHeader(cells, headers, (normalized) =>
      normalized.includes("obj final")
    );
    const discursiveCell = findCellByHeader(cells, headers, (normalized) =>
      normalized.includes("disc final")
    );
    const titleCell = findCellByHeader(cells, headers, (normalized) =>
      normalized.includes("tit final")
    );
    const finalCell = findCellByHeader(cells, headers, (normalized) =>
      normalized === "nota final" || normalized.endsWith("nota final")
    );
    const rankingCell = findCellByHeader(cells, headers, (normalized) =>
      normalized.includes("colocacao")
    );
    const otherCell = findCellByHeader(cells, headers, (normalized, raw) =>
      normalized.includes("fez tb") || raw.toLowerCase().includes("fez tb")
    );

    if (!nameCell) {
      return null;
    }

    const nameSpan = nameCell.querySelector("span");
    const status = statusCell ? getStatusFlags(statusCell) : { named: false, inside_vacancies: false };

    return {
      name: cleanText(nameSpan ? nameSpan.textContent : nameCell.textContent),
      objective_score: objectiveCell ? parseNumber(objectiveCell.textContent) : null,
      discursive_score: discursiveCell ? parseNumber(discursiveCell.textContent) : null,
      title_score: titleCell ? parseNumber(titleCell.textContent) : null,
      final_score: finalCell ? parseNumber(finalCell.textContent) : null,
      ranking_text: rankingCell ? cleanText(rankingCell.textContent) : "",
      ranking_position: rankingCell ? parsePlacement(rankingCell.textContent) : null,
      named: status.named,
      inside_vacancies: status.inside_vacancies,
      other_results: otherCell ? parseCrossContestResults(otherCell) : [],
      raw_row_text: cleanText(row.textContent),
      detected_columns: headers.map((header) => header.raw_text).join(" | "),
      ...parseNominationLink(nameCell),
    };
  }

  function parsePage(doc, sourceUrl, rawHtml) {
    const contests = getContestOptions(doc);
    const selectedContest =
      contests.find((contest) => contest.selected) ||
      contests.find((contest) => contest.value === new URL(sourceUrl).searchParams.get("sCa")) ||
      null;

    const table = doc.querySelector("table.table");
    const headers = table ? extractTableSchema(table) : [];
    const candidates = Array.from(doc.querySelectorAll("table.table tbody tr"))
      .map((row) => parseCandidateRow(row, headers))
      .filter(Boolean);

    return {
      source_url: sourceUrl,
      page_title: cleanText(doc.title),
      fetched_at: new Date().toISOString(),
      selected_contest: selectedContest,
      contests,
      headers,
      candidates,
      raw_html: rawHtml,
    };
  }

  function getDocumentTextSample(doc) {
    return cleanText(doc.body ? doc.body.innerText : "").slice(0, 400);
  }

  function inspectFetchedPage(doc, pageData, requestedContest) {
    const hasTable = Boolean(doc.querySelector("table.table"));
    const selectedValue = pageData.selected_contest ? pageData.selected_contest.value : null;
    const title = (pageData.page_title || "").toLowerCase();
    const sample = getDocumentTextSample(doc).toLowerCase();
    const issues = [];

    if (!hasTable) {
      issues.push("sem_tabela");
    }

    if (selectedValue && requestedContest && selectedValue !== requestedContest.value) {
      issues.push(`concurso_retornado_diferente:${selectedValue}`);
    }

    const suspiciousTokens = [
      "just a moment",
      "cloudflare",
      "access denied",
      "forbidden",
      "too many requests",
      "rate limit",
      "aguarde",
      "bloqueado",
      "blocked",
      "captcha",
      "erro 403",
      "erro 429",
      "request unsuccessful",
    ];

    const matchedToken = suspiciousTokens.find(
      (token) => title.includes(token) || sample.includes(token)
    );
    if (matchedToken) {
      issues.push(`pagina_suspeita:${matchedToken}`);
    }

    const legitimateEmptyTokens = [
      "nenhum resultado",
      "nenhum candidato",
      "nenhum dado",
      "sem resultados",
      "nao ha resultados",
      "não há resultados",
    ];

    const isLegitimateEmptyPage =
      pageData.candidates.length === 0 &&
      legitimateEmptyTokens.some((token) => sample.includes(token));

    const looksSuspicious =
      !isLegitimateEmptyPage &&
      (
        issues.length > 0 ||
        (!hasTable && pageData.candidates.length === 0)
      );

    return {
      looks_suspicious: looksSuspicious,
      issues,
      sample: getDocumentTextSample(doc),
    };
  }

  function summarizeContest(pageData) {
    return {
      contest_name: pageData.selected_contest ? pageData.selected_contest.full_text : "",
      contest_value: pageData.selected_contest ? pageData.selected_contest.value : "",
      source_url: pageData.source_url,
      page_title: pageData.page_title,
      fetched_at: pageData.fetched_at,
      candidates_count: pageData.candidates.length,
      named_count: pageData.candidates.filter((candidate) => candidate.named).length,
      inside_vacancies_count: pageData.candidates.filter((candidate) => candidate.inside_vacancies).length,
    };
  }

  function flattenCandidates(pageData) {
    return pageData.candidates.map((candidate) => ({
      contest_name: pageData.selected_contest ? pageData.selected_contest.full_text : "",
      contest_value: pageData.selected_contest ? pageData.selected_contest.value : "",
      source_url: pageData.source_url,
      page_title: pageData.page_title,
      fetched_at: pageData.fetched_at,
      name: candidate.name,
      objective_score: candidate.objective_score,
      discursive_score: candidate.discursive_score,
      title_score: candidate.title_score,
      final_score: candidate.final_score,
      ranking_text: candidate.ranking_text,
      ranking_position: candidate.ranking_position,
      named: candidate.named,
      inside_vacancies: candidate.inside_vacancies,
      other_results_count: candidate.other_results.length,
      named_in_other_contests: candidate.other_results.filter((item) => item.named).length,
      inside_vacancies_in_other_contests: candidate.other_results.filter((item) => item.inside_vacancies).length,
      other_results_summary: candidate.other_results.map((item) => item.ranking_text).join(" | "),
      detected_columns: candidate.detected_columns,
      raw_row_text: candidate.raw_row_text,
      nomination_link_href: candidate.nomination_link_href,
      nomination_token: candidate.nomination_token,
      inscription: candidate.inscription,
      contest_id: candidate.contest_id,
      cargo_id: candidate.cargo_id,
      nomination_view_type: candidate.nomination_view_type,
      nomination_placement_param: candidate.nomination_placement_param,
      nomination_candidate_name_param: candidate.nomination_candidate_name_param,
      nomination_contest_name_param: candidate.nomination_contest_name_param,
      nomination_cargo_name_param: candidate.nomination_cargo_name_param,
      nomination_return_path: candidate.nomination_return_path,
    }));
  }

  function flattenOtherResults(pageData) {
    return pageData.candidates.flatMap((candidate) =>
      candidate.other_results.map((result) => ({
        source_contest_name: pageData.selected_contest ? pageData.selected_contest.full_text : "",
        source_contest_value: pageData.selected_contest ? pageData.selected_contest.value : "",
        source_url: pageData.source_url,
        page_title: pageData.page_title,
        fetched_at: pageData.fetched_at,
        candidate_name: candidate.name,
        candidate_ranking_text: candidate.ranking_text,
        candidate_ranking_position: candidate.ranking_position,
        target_contest_label: result.contest_label,
        target_contest_value: result.contest_value,
        target_ranking_text: result.ranking_text,
        target_ranking_position: result.ranking_position,
        target_named: result.named,
        target_inside_vacancies: result.inside_vacancies,
        target_href: absoluteUrl(result.href),
      }))
    );
  }

  async function fetchContestPage(contest, filters, options = {}) {
    const maxAttempts = options.max_attempts || 3;
    const baseDelayMs = options.base_delay_ms || 2500;
    const url = new URL(window.location.href);
    url.searchParams.set("tC", filters.track_type);
    url.searchParams.set("sCa", contest.value);
    url.searchParams.set("tV", filters.view_type);
    let lastError = null;

    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      try {
        const response = await fetch(url.toString(), {
          credentials: "include",
          cache: "no-store",
          headers: {
            "X-Requested-With": "Tampermonkey",
            "Accept": "text/html,application/xhtml+xml",
          },
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const htmlText = await response.text();
        const doc = new DOMParser().parseFromString(htmlText, "text/html");
        const pageData = parsePage(doc, url.toString(), htmlText);
        const inspection = inspectFetchedPage(doc, pageData, contest);

        if (inspection.looks_suspicious) {
          const reason = inspection.issues.join(", ") || "pagina_sem_dados_validos";
          throw new Error(`${reason} | amostra: ${inspection.sample}`);
        }

        return pageData;
      } catch (error) {
        lastError = error;
        if (attempt >= maxAttempts) {
          break;
        }
        const waitMs = baseDelayMs * attempt;
        await sleep(waitMs);
      }
    }

    throw new Error(`Falha ao buscar ${contest.full_text}: ${lastError ? lastError.message : "erro desconhecido"}`);
  }

  function exportSnapshotBundle(label, snapshot) {
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const baseName = `${slugify(label)}_${stamp}`;

    const selectorContestsCsv = toCsv(snapshot.selector_contests, [
      { key: "value", header: "value" },
      { key: "full_text", header: "full_text" },
      { key: "display_text", header: "display_text" },
      { key: "selected", header: "selected" },
    ]);

    const contestPagesCsv = toCsv(snapshot.contest_pages, [
      { key: "contest_name", header: "contest_name" },
      { key: "contest_value", header: "contest_value" },
      { key: "source_url", header: "source_url" },
      { key: "page_title", header: "page_title" },
      { key: "fetched_at", header: "fetched_at" },
      { key: "candidates_count", header: "candidates_count" },
      { key: "named_count", header: "named_count" },
      { key: "inside_vacancies_count", header: "inside_vacancies_count" },
    ]);

    const candidatesCsv = toCsv(snapshot.candidates, [
      { key: "contest_name", header: "contest_name" },
      { key: "contest_value", header: "contest_value" },
      { key: "source_url", header: "source_url" },
      { key: "page_title", header: "page_title" },
      { key: "fetched_at", header: "fetched_at" },
      { key: "name", header: "name" },
      { key: "objective_score", header: "objective_score" },
      { key: "discursive_score", header: "discursive_score" },
      { key: "title_score", header: "title_score" },
      { key: "final_score", header: "final_score" },
      { key: "ranking_text", header: "ranking_text" },
      { key: "ranking_position", header: "ranking_position" },
      { key: "named", header: "named" },
      { key: "inside_vacancies", header: "inside_vacancies" },
      { key: "other_results_count", header: "other_results_count" },
      { key: "named_in_other_contests", header: "named_in_other_contests" },
      { key: "inside_vacancies_in_other_contests", header: "inside_vacancies_in_other_contests" },
      { key: "other_results_summary", header: "other_results_summary" },
      { key: "detected_columns", header: "detected_columns" },
      { key: "raw_row_text", header: "raw_row_text" },
      { key: "nomination_link_href", header: "nomination_link_href" },
      { key: "nomination_token", header: "nomination_token" },
      { key: "inscription", header: "inscription" },
      { key: "contest_id", header: "contest_id" },
      { key: "cargo_id", header: "cargo_id" },
      { key: "nomination_view_type", header: "nomination_view_type" },
      { key: "nomination_placement_param", header: "nomination_placement_param" },
      { key: "nomination_candidate_name_param", header: "nomination_candidate_name_param" },
      { key: "nomination_contest_name_param", header: "nomination_contest_name_param" },
      { key: "nomination_cargo_name_param", header: "nomination_cargo_name_param" },
      { key: "nomination_return_path", header: "nomination_return_path" },
    ]);

    const otherResultsCsv = toCsv(snapshot.other_results, [
      { key: "source_contest_name", header: "source_contest_name" },
      { key: "source_contest_value", header: "source_contest_value" },
      { key: "source_url", header: "source_url" },
      { key: "page_title", header: "page_title" },
      { key: "fetched_at", header: "fetched_at" },
      { key: "candidate_name", header: "candidate_name" },
      { key: "candidate_ranking_text", header: "candidate_ranking_text" },
      { key: "candidate_ranking_position", header: "candidate_ranking_position" },
      { key: "target_contest_label", header: "target_contest_label" },
      { key: "target_contest_value", header: "target_contest_value" },
      { key: "target_ranking_text", header: "target_ranking_text" },
      { key: "target_ranking_position", header: "target_ranking_position" },
      { key: "target_named", header: "target_named" },
      { key: "target_inside_vacancies", header: "target_inside_vacancies" },
      { key: "target_href", header: "target_href" },
    ]);

    downloadText(`${baseName}_selector_contests.csv`, selectorContestsCsv, "text/csv");
    downloadText(`${baseName}_contest_pages.csv`, contestPagesCsv, "text/csv");
    downloadText(`${baseName}_candidates.csv`, candidatesCsv, "text/csv");
    downloadText(`${baseName}_other_results.csv`, otherResultsCsv, "text/csv");
    downloadText(`${baseName}_data.json`, JSON.stringify(snapshot, null, 2), "application/json");
  }

  function createPanel() {
    const existing = document.getElementById(PANEL_ID);
    if (existing) {
      return existing;
    }

    const panel = document.createElement("div");
    panel.id = PANEL_ID;
    panel.innerHTML = `
      <div style="font-weight: 700; font-size: 14px;">Alunos Consultoria Scraper</div>
      <div style="font-size: 12px; color: #475569; margin-top: 6px;">Extracao bruta primeiro. Algoritmos depois.</div>
      <label style="display:block; margin-top: 10px; font-size: 12px;">
        Delay entre requisicoes (ms)
        <input id="${PANEL_ID}-delay" type="number" min="0" value="${DEFAULT_DELAY_MS}" style="width: 100%; margin-top: 4px; padding: 6px; border: 1px solid #cbd5e1; border-radius: 6px;">
      </label>
      <div style="display:grid; grid-template-columns: 1fr; gap: 8px; margin-top: 12px;">
        <button id="${PANEL_ID}-current" style="padding: 8px 10px; border: 0; border-radius: 8px; background: #0f766e; color: #fff; cursor: pointer;">Exportar concurso atual</button>
        <button id="${PANEL_ID}-all" style="padding: 8px 10px; border: 0; border-radius: 8px; background: #1d4ed8; color: #fff; cursor: pointer;">Varrer todos do filtro atual</button>
        <button id="${PANEL_ID}-cancel" style="padding: 8px 10px; border: 1px solid #cbd5e1; border-radius: 8px; background: #fff; color: #0f172a; cursor: pointer;">Cancelar varredura</button>
      </div>
      <pre id="${PANEL_ID}-log" style="white-space: pre-wrap; margin: 12px 0 0; padding: 10px; max-height: 260px; overflow: auto; border-radius: 8px; background: #0f172a; color: #e2e8f0; font-size: 11px;"></pre>
    `;

    Object.assign(panel.style, {
      position: "fixed",
      top: "16px",
      right: "16px",
      zIndex: "999999",
      width: "320px",
      background: "#f8fafc",
      color: "#0f172a",
      border: "1px solid #cbd5e1",
      borderRadius: "12px",
      boxShadow: "0 15px 45px rgba(15, 23, 42, 0.20)",
      padding: "14px",
      fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    });

    document.body.appendChild(panel);
    return panel;
  }

  const panel = createPanel();
  const logEl = panel.querySelector(`#${PANEL_ID}-log`);
  const delayInput = panel.querySelector(`#${PANEL_ID}-delay`);
  const currentButton = panel.querySelector(`#${PANEL_ID}-current`);
  const allButton = panel.querySelector(`#${PANEL_ID}-all`);
  const cancelButton = panel.querySelector(`#${PANEL_ID}-cancel`);

  const runState = {
    cancelled: false,
    running: false,
  };

  function log(message) {
    const timestamp = new Date().toLocaleTimeString();
    logEl.textContent += `[${timestamp}] ${message}\n`;
    logEl.scrollTop = logEl.scrollHeight;
  }

  function readOptions() {
    return {
      delay_ms: Math.max(0, Number.parseInt(delayInput.value, 10) || DEFAULT_DELAY_MS),
    };
  }

  function setRunningState(active) {
    runState.running = active;
    currentButton.disabled = active;
    allButton.disabled = active;
    currentButton.style.opacity = active ? "0.7" : "1";
    allButton.style.opacity = active ? "0.7" : "1";
    currentButton.style.cursor = active ? "wait" : "pointer";
    allButton.style.cursor = active ? "wait" : "pointer";
  }

  async function exportCurrentContest() {
    const pageData = parsePage(document, window.location.href, document.documentElement.outerHTML);
    const snapshot = {
      scraped_at: new Date().toISOString(),
      mode: "current_contest",
      filters: getCurrentFilters(),
      selector_contests: pageData.contests,
      contest_pages: [summarizeContest(pageData)],
      candidates: flattenCandidates(pageData),
      other_results: flattenOtherResults(pageData),
      pages: [pageData],
    };

    const label = pageData.selected_contest ? pageData.selected_contest.full_text : "concurso_atual";
    exportSnapshotBundle(label, snapshot);
    log(`Exportacao concluida para o concurso atual: ${label}.`);
  }

  async function exportAllContests() {
    const options = readOptions();
    const filters = getCurrentFilters();
    const contests = getContestOptions(document);

    if (!contests.length) {
      throw new Error("Nenhum concurso foi encontrado no seletor da pagina.");
    }

    log(`Iniciando varredura de ${contests.length} concursos com tC=${filters.track_type} e tV=${filters.view_type}.`);

    const pages = [];
    const contestPages = [];
    const candidates = [];
    const otherResults = [];

    for (let index = 0; index < contests.length; index += 1) {
      if (runState.cancelled) {
        log("Varredura cancelada pela interface.");
        break;
      }

      const contest = contests[index];
      log(`(${index + 1}/${contests.length}) Buscando ${contest.full_text}...`);
      let pageData;
      try {
        pageData = await fetchContestPage(contest, filters, {
          max_attempts: 3,
          base_delay_ms: Math.max(2500, options.delay_ms * 2),
        });
      } catch (error) {
        log(`Falha em ${contest.full_text}: ${error.message}`);
        continue;
      }
      pages.push(pageData);
      contestPages.push(summarizeContest(pageData));
      candidates.push(...flattenCandidates(pageData));
      otherResults.push(...flattenOtherResults(pageData));

      log(
        `Concurso ${contest.full_text}: ${pageData.candidates.length} candidatos, ${pageData.candidates.filter((candidate) => candidate.named).length} marcados como nomeados.`
      );

      if (index < contests.length - 1 && options.delay_ms > 0) {
        await sleep(options.delay_ms);
      }
    }

    const snapshot = {
      scraped_at: new Date().toISOString(),
      mode: "all_contests_from_current_filter",
      filters,
      selector_contests: contests,
      contest_pages: contestPages,
      candidates,
      other_results: otherResults,
      pages,
    };

    exportSnapshotBundle(`ranking_${filters.track_type}`, snapshot);
    log(`Varredura finalizada. Concursos processados: ${pages.length}.`);
  }

  currentButton.addEventListener("click", async () => {
    if (runState.running) {
      return;
    }

    runState.cancelled = false;
    setRunningState(true);
    log("Preparando exportacao do concurso atual...");

    try {
      await exportCurrentContest();
    } catch (error) {
      log(`Erro: ${error.message}`);
      console.error(error);
    } finally {
      setRunningState(false);
    }
  });

  allButton.addEventListener("click", async () => {
    if (runState.running) {
      return;
    }

    runState.cancelled = false;
    setRunningState(true);
    log("Preparando varredura de todos os concursos do filtro atual...");

    try {
      await exportAllContests();
    } catch (error) {
      log(`Erro: ${error.message}`);
      console.error(error);
    } finally {
      setRunningState(false);
    }
  });

  cancelButton.addEventListener("click", () => {
    runState.cancelled = true;
    log("Solicitacao de cancelamento registrada.");
  });

  log("Painel carregado. Agora o foco esta em extracao bruta de dados.");
})();
