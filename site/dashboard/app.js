const state = {
  report: null,
  artists: null,
  selectedArtists: new Map(),
  dataMode: "demo",
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const fmt = (num) => Number(num || 0).toLocaleString("ru-RU");
const pct = (part, whole) => (!whole ? 0 : Math.round((part / whole) * 10000) / 100);

async function loadJson(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`Не удалось загрузить ${path}`);
  return await res.json();
}

async function loadDashboardData() {
  const [report, artists] = await Promise.all([
    loadJson("../spotify_library_audit_report.json"),
    loadJson("../ACTUAL SPOTIFY ARTISTS.json"),
  ]);
  return { report, artists, mode: "demo" };
}

async function loadSelectedArtists() {
  const res = await fetch("/api/selected-artists", { cache: "no-store" });
  if (!res.ok) throw new Error("Не удалось загрузить selected artists");
  return await res.json();
}

async function saveSelectedArtists() {
  const res = await fetch("/api/selected-artists", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected: [...state.selectedArtists.values()] }),
  });
  if (!res.ok) throw new Error("Не удалось сохранить selected artists");
  return await res.json();
}

function normalizeArtistKey(name) {
  return String(name || "").trim().toLowerCase();
}

function buildInsights(report, artistsPayload) {
  const summary = report.summary;
  const followedArtists = artistsPayload.artists || [];
  const found = report.found || [];
  const notFound = report.not_found || [];
  const artistStats = report.artist_stats || [];
  const potential = report.potential_artists_for_add || [];

  const sourceMap = new Map();
  [...found, ...notFound].forEach((item) => {
    const source = item.source || "unknown";
    if (!sourceMap.has(source)) sourceMap.set(source, { source, total: 0, found: 0, missing: 0 });
    const row = sourceMap.get(source);
    row.total += 1;
    if (item.spotify_id || item.match_type) row.found += 1;
    else row.missing += 1;
  });
  const sourceBreakdown = [...sourceMap.values()].map((row) => ({
    ...row,
    foundPercent: pct(row.found, row.total),
  }));

  const missingArtistsMap = new Map();
  notFound.forEach((item) => {
    const key = normalizeArtistKey(item.artist);
    if (!missingArtistsMap.has(key)) missingArtistsMap.set(key, { artist: item.artist, count: 0 });
    missingArtistsMap.get(key).count += 1;
  });
  const topMissingArtists = [...missingArtistsMap.values()]
    .sort((a, b) => b.count - a.count || a.artist.localeCompare(b.artist, "ru"))
    .slice(0, 12);

  const topCoreArtists = [...artistStats]
    .sort((a, b) =>
      b.added_track_count - a.added_track_count ||
      b.discography_track_count - a.discography_track_count ||
      a.artist.localeCompare(b.artist, "ru")
    )
    .slice(0, 12);

  const priorityRepairs = [...artistStats]
    .filter((item) => item.missing_track_count > 0)
    .sort((a, b) =>
      b.missing_track_count - a.missing_track_count ||
      b.added_track_count - a.added_track_count ||
      a.artist.localeCompare(b.artist, "ru")
    )
    .slice(0, 10);

  const vkMissing = sourceBreakdown.find((row) => row.source === "vk")?.missing || 0;
  const vkShare = pct(vkMissing, summary.not_found_total);

  return {
    followedArtists,
    sourceBreakdown,
    topMissingArtists,
    topCoreArtists,
    priorityRepairs,
    advice: [
      {
        title: "Покрытие уже рабочее, но не полное",
        text: `Сейчас в Spotify отражено ${summary.found_percent}% полной дискографии. Это уже сильная база, но ${summary.not_found_percent}% вкуса всё ещё не учитывается в рекомендациях.`,
      },
      {
        title: "Главный шум идёт из VK-хвоста",
        text: `${vkShare}% всех ненайденных треков приходится на VK. Обычно это ремиксы, редкие версии, кривой нейминг и треки, которые Spotify не ловит с первого раза.`,
      },
      {
        title: "Spotify пока слабо понимает центр вкуса",
        text: `У тебя ${fmt(summary.followed_artists_count)} followed artists, но при этом уже ${fmt(summary.found_total)} найденных треков и ${fmt(summary.potential_artists_count)} сильных кандидатов на follow.`,
      },
    ],
    djModes: [
      {
        title: "Core Rotation",
        subtitle: "Эти имена уже формируют центр вкуса. Чем сильнее они отражены в Spotify, тем точнее будет AI DJ.",
        artists: topCoreArtists.slice(0, 6).map((x) => x.artist),
      },
      {
        title: "Follow Next",
        subtitle: "Сначала усили follow для артистов, которых ты уже много слушаешь, но Spotify ещё не видит как явный сигнал.",
        artists: potential.slice(0, 6).map((x) => x.artist),
      },
      {
        title: "Recovery Queue",
        subtitle: "Если вручную добить эти провалы, рекомендации и AI DJ станут ближе к твоему реальному вкусу.",
        artists: priorityRepairs.slice(0, 6).map((x) => x.artist),
      },
    ],
    steps: [
      "Сначала подписать top potential artists с уже найденными треками.",
      "Потом вручную добить артистов, у которых больше всего missing tracks.",
      "После этого обновить лайки и ещё раз пересобрать audit report.",
    ],
  };
}

function renderMetrics(summary) {
  const metrics = [
    ["Liked songs сейчас", summary.actual_spotify_likes_count, "Текущий объём Spotify-библиотеки."],
    ["Полная дискография", summary.discography_count, "Все твои треки из объединённого архива."],
    ["Potential artists", summary.potential_artists_count, "Кандидаты на follow для усиления сигнала вкуса."],
    ["Followed artists", summary.followed_artists_count, "Что Spotify уже видит как явный вектор вкуса."],
  ];
  $("#metrics").innerHTML = metrics.map(([label, value, sub]) => `
    <article class="glass metric-card">
      <div class="metric-label">${label}</div>
      <div class="metric-value">${fmt(value)}</div>
      <div class="metric-sub">${sub}</div>
    </article>
  `).join("");
}

function renderCoverage(summary) {
  $("#heroCoverage").textContent = `${summary.found_percent}%`;
  $("#heroCoverageText").textContent = `${fmt(summary.found_total)} из ${fmt(summary.discography_count)} треков уже в Spotify likes`;
  $("#heroStatus").textContent = `${fmt(summary.followed_artists_count)} artists`;
  $("#heroStatusText").textContent = `${fmt(summary.potential_artists_count)} potential artists for add`;
  $("#coverageValue").textContent = `${summary.found_percent}%`;
  $("#coverageRing").style.setProperty("--found", summary.found_percent);

  const legend = [
    ["var(--mint)", "Найдено в Spotify", `${fmt(summary.found_total)} · ${summary.found_percent}%`],
    ["var(--red)", "Не найдено", `${fmt(summary.not_found_total)} · ${summary.not_found_percent}%`],
    ["var(--gold)", "Potential artists", `${fmt(summary.potential_artists_count)} артистов`],
  ];
  $("#coverageLegend").innerHTML = legend.map(([color, title, value]) => `
    <div class="legend-row">
      <div class="swatch" style="background:${color}"></div>
      <div>${title}</div>
      <strong>${value}</strong>
    </div>
  `).join("");
}

function applyDataMode(mode) {
  state.dataMode = mode;
  $("#heroEyebrow").textContent = "yaMusicToSpotify • Dashboard Demo";
  $("#heroStatus").textContent = "Demo";
  $("#heroStatusText").textContent = "Bundled sample data for the public repository";
}

function renderSourceRows(sourceBreakdown) {
  $("#sourceRows").innerHTML = sourceBreakdown.map((row) => {
    const ok = row.total ? (row.found / row.total) * 100 : 0;
    return `
      <div class="source-row">
        <div class="source-top">
          <strong>${row.source}</strong>
          <span>${fmt(row.found)} / ${fmt(row.total)} · ${row.foundPercent}%</span>
        </div>
        <div class="source-bar">
          <div class="source-bar-ok" style="width:${ok}%"></div>
          <div class="source-bar-miss" style="width:${100 - ok}%"></div>
        </div>
      </div>
    `;
  }).join("");
}

function renderAdvice(insights) {
  $("#insightCards").innerHTML = insights.advice.map((item) => `
    <article class="glass insight-card">
      <h3>${item.title}</h3>
      <p>${item.text}</p>
    </article>
  `).join("");

  $("#djModes").innerHTML = insights.djModes.map((mode) => `
    <article class="glass mode-card">
      <h3>${mode.title}</h3>
      <p>${mode.subtitle}</p>
      <div class="chips">${mode.artists.map((artist) => `<span class="chip">${artist}</span>`).join("")}</div>
    </article>
  `).join("");

  $("#nextSteps").innerHTML = insights.steps.map((step, i) => `
    <div class="step-row">
      <div class="step-n">${i + 1}</div>
      <div>${step}</div>
    </div>
  `).join("");

  $("#missingArtists").innerHTML = insights.topMissingArtists.map((item) => `
    <div class="rank-row">
      <div>
        <strong>${item.artist}</strong>
        <div class="metric-sub">Частота среди ненайденных</div>
      </div>
      <span class="tag miss">${item.count} треков</span>
    </div>
  `).join("");
}

function refreshSelectionUi(statusText = null) {
  $("#selectedCount").textContent = fmt(state.selectedArtists.size);
  if (statusText) $("#selectionStatus").textContent = statusText;
  const previewItems = [...state.selectedArtists.values()]
    .sort((a, b) => b.added_track_count - a.added_track_count || a.artist.localeCompare(b.artist, "ru"))
    .slice(0, 24);
  $("#selectedPreview").innerHTML = previewItems.length
    ? previewItems.map((item) => `<span class="chip">${item.artist} · ${fmt(item.added_track_count)}</span>`).join("")
    : `<span class="chip">Пока ничего не выбрано</span>`;
}

function downloadSelectedArtists() {
  const payload = {
    updated_at: new Date().toISOString(),
    selected_count: state.selectedArtists.size,
    selected: [...state.selectedArtists.values()],
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "selected_potential_artists.json";
  a.click();
  URL.revokeObjectURL(url);
}

function makeSlimArtistStats(rows) {
  return rows.map((row) => ({
    artist: row.artist,
    discography_track_count: row.discography_track_count,
    added_track_count: row.added_track_count,
    missing_track_count: row.missing_track_count,
    followed_on_spotify: row.followed_on_spotify,
    spotify_followed_artist_name: row.spotify_followed_artist_name,
    potential_for_add: row.potential_for_add,
  }));
}

function initTables(report, artistsPayload) {
  const tabs = [
    {
      id: "all-artists",
      title: "Все артисты",
      description: "Полная карта артистов из дискографии.",
      data: makeSlimArtistStats(report.artist_stats || []),
      searchKeys: ["artist", "spotify_followed_artist_name"],
      pageSize: 28,
      columns: [
        { key: "artist", label: "Артист" },
        { key: "discography_track_count", label: "Всего" },
        { key: "added_track_count", label: "Добавлено" },
        { key: "missing_track_count", label: "Не найдено" },
        { key: "followed_on_spotify", label: "Followed" },
        { key: "potential_for_add", label: "Potential" },
      ],
      format: (row) => ({
        artist: row.artist,
        discography_track_count: fmt(row.discography_track_count),
        added_track_count: fmt(row.added_track_count),
        missing_track_count: fmt(row.missing_track_count),
        followed_on_spotify: row.followed_on_spotify ? '<span class="tag ok">Да</span>' : '<span class="tag miss">Нет</span>',
        potential_for_add: row.potential_for_add ? '<span class="tag warn">Добавить</span>' : '<span class="tag ok">Ок</span>',
      }),
    },
    {
      id: "potential",
      title: "Potential artists",
      description: "Кого стоит фолловить в первую очередь.",
      data: makeSlimArtistStats(report.potential_artists_for_add || []),
      searchKeys: ["artist"],
      pageSize: 24,
      columns: [
        { key: "__select__", label: "Выбор" },
        { key: "artist", label: "Артист" },
        { key: "added_track_count", label: "Добавлено" },
        { key: "discography_track_count", label: "Всего" },
        { key: "missing_track_count", label: "Не найдено" },
      ],
      format: (row) => ({
        __select__: `<span class="checkbox-wrap"><input type="checkbox" data-select-artist="${row.artist.replace(/"/g, "&quot;")}"></span>`,
        artist: row.artist,
        added_track_count: fmt(row.added_track_count),
        discography_track_count: fmt(row.discography_track_count),
        missing_track_count: fmt(row.missing_track_count),
      }),
    },
    {
      id: "followed",
      title: "Spotify artists",
      description: "Текущий список followed artists в Spotify.",
      data: (artistsPayload.artists || []).map((row) => ({
        name: row.name,
        genres: (row.genres || []).join(", "),
        external_url: row.external_url,
      })),
      searchKeys: ["name", "genres"],
      pageSize: 20,
      columns: [
        { key: "name", label: "Артист" },
        { key: "genres", label: "Жанры" },
        { key: "external_url", label: "Spotify" },
      ],
      format: (row) => ({
        name: row.name,
        genres: row.genres || '<span class="tag warn">нет genre-данных</span>',
        external_url: row.external_url ? `<a href="${row.external_url}" target="_blank" rel="noreferrer">open</a>` : "—",
      }),
    },
    {
      id: "not-found",
      title: "Ненайденные треки",
      description: "Все треки, которых пока нет в Spotify likes.",
      data: report.not_found || [],
      searchKeys: ["artist", "title", "source"],
      pageSize: 26,
      columns: [
        { key: "chronological_index", label: "#" },
        { key: "source", label: "Источник" },
        { key: "artist", label: "Артист" },
        { key: "title", label: "Трек" },
      ],
      format: (row) => ({
        chronological_index: fmt(row.chronological_index),
        source: row.source || "?",
        artist: row.artist,
        title: row.title,
      }),
    },
  ];

  const tabsEl = $("#tabs");
  const panelsEl = $("#tablePanels");
  tabsEl.innerHTML = "";
  panelsEl.innerHTML = "";

  tabs.forEach((tabConfig, index) => {
    const button = document.createElement("button");
    button.className = `tab${index === 0 ? " active" : ""}`;
    button.textContent = `${tabConfig.title} (${fmt(tabConfig.data.length)})`;
    button.dataset.tab = tabConfig.id;
    tabsEl.appendChild(button);

    const panel = document.createElement("section");
    panel.className = `table-panel${index === 0 ? " active" : ""}`;
    panel.dataset.panel = tabConfig.id;
    panel.innerHTML = `
      <div class="table-toolbar">
        <div>
          <h3>${tabConfig.title}</h3>
          <p class="metric-sub">${tabConfig.description}</p>
        </div>
        <div class="search-wrap"><input type="text" placeholder="Поиск по таблице..."></div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr>${tabConfig.columns.map((col) => `<th data-key="${col.key}">${col.label}</th>`).join("")}</tr></thead>
          <tbody></tbody>
        </table>
      </div>
      <div class="pager">
        <div class="metric-sub pager-meta"></div>
        <div class="pager-controls">
          <button class="prev">Назад</button>
          <button class="next">Дальше</button>
        </div>
      </div>
    `;
    panelsEl.appendChild(panel);

    const panelState = {
      search: "",
      page: 1,
      sortKey: (tabConfig.columns.find((col) => col.key !== "__select__") || tabConfig.columns[0]).key,
      sortDir: 1,
    };

    const input = $("input", panel);
    const tbody = $("tbody", panel);
    const meta = $(".pager-meta", panel);
    const prev = $(".prev", panel);
    const next = $(".next", panel);

    const sortValue = (row, key) => {
      const value = row[key];
      if (typeof value === "boolean") return value ? 1 : 0;
      return value ?? "";
    };

    function getRows() {
      const q = panelState.search.trim().toLowerCase();
      let rows = tabConfig.data.filter((row) => {
        if (!q) return true;
        return tabConfig.searchKeys.some((key) => String(row[key] ?? "").toLowerCase().includes(q));
      });
      rows = rows.slice().sort((a, b) => {
        const av = sortValue(a, panelState.sortKey);
        const bv = sortValue(b, panelState.sortKey);
        if (typeof av === "number" && typeof bv === "number") return (av - bv) * panelState.sortDir;
        return String(av).localeCompare(String(bv), "ru", { sensitivity: "base" }) * panelState.sortDir;
      });
      return rows;
    }

    function render() {
      const rows = getRows();
      const pages = Math.max(1, Math.ceil(rows.length / tabConfig.pageSize));
      panelState.page = Math.min(panelState.page, pages);
      const start = (panelState.page - 1) * tabConfig.pageSize;
      const pageRows = rows.slice(start, start + tabConfig.pageSize);
      tbody.innerHTML = pageRows.map((row) => {
        const formatted = tabConfig.format(row);
        return `<tr>${tabConfig.columns.map((col) => `<td class="${col.key === "__select__" ? "checkbox-cell" : ""}">${formatted[col.key] ?? ""}</td>`).join("")}</tr>`;
      }).join("");
      $$('input[data-select-artist]', tbody).forEach((checkbox) => {
        const artist = checkbox.dataset.selectArtist;
        checkbox.checked = state.selectedArtists.has(artist);
        checkbox.addEventListener("change", () => {
          const row = tabConfig.data.find((item) => item.artist === artist);
          if (!row) return;
          if (checkbox.checked) state.selectedArtists.set(artist, row);
          else state.selectedArtists.delete(artist);
          refreshSelectionUi();
        });
      });
      meta.textContent = `Показано ${fmt(pageRows.length)} из ${fmt(rows.length)} · страница ${panelState.page}/${pages}`;
      prev.disabled = panelState.page <= 1;
      next.disabled = panelState.page >= pages;
    }

    input.addEventListener("input", (event) => {
      panelState.search = event.target.value;
      panelState.page = 1;
      render();
    });
    prev.addEventListener("click", () => {
      if (panelState.page > 1) {
        panelState.page -= 1;
        render();
      }
    });
    next.addEventListener("click", () => {
      panelState.page += 1;
      render();
    });
    $$("th", panel).forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.key;
        if (panelState.sortKey === key) panelState.sortDir *= -1;
        else {
          panelState.sortKey = key;
          panelState.sortDir = 1;
        }
        render();
      });
    });

    render();
  });

  tabsEl.addEventListener("click", (event) => {
    const btn = event.target.closest(".tab");
    if (!btn) return;
    const id = btn.dataset.tab;
    $$(".tab", tabsEl).forEach((node) => node.classList.toggle("active", node === btn));
    $$(".table-panel", panelsEl).forEach((node) => node.classList.toggle("active", node.dataset.panel === id));
  });
}

async function bootstrap() {
  try {
    const [{ report, artists, mode }, selected] = await Promise.all([
      loadDashboardData(),
      loadSelectedArtists(),
    ]);

    state.report = report;
    state.artists = artists;
    applyDataMode(mode);
    (selected.selected || []).forEach((item) => {
      if (item.artist) state.selectedArtists.set(item.artist, item);
    });

    renderMetrics(report.summary);
    renderCoverage(report.summary);
    const insights = buildInsights(report, artists);
    renderSourceRows(insights.sourceBreakdown);
    renderAdvice(insights);
    initTables(report, artists);
    refreshSelectionUi(
      selected.updated_at
        ? `Сохранено локально: ${new Date(selected.updated_at).toLocaleString("ru-RU")}`
        : "Список ещё не сохранён"
    );

    $("#saveSelectionBtn").addEventListener("click", async () => {
      $("#selectionStatus").textContent = "Сохраняю...";
      try {
        const result = await saveSelectedArtists();
        refreshSelectionUi(`Сохранено локально · ${fmt(result.selected_count)} артистов`);
      } catch (error) {
        refreshSelectionUi(`Ошибка сохранения: ${error.message}`);
      }
    });

    $("#clearSelectionBtn").addEventListener("click", () => {
      state.selectedArtists.clear();
      $$('input[data-select-artist]').forEach((node) => { node.checked = false; });
      refreshSelectionUi("Выбор очищен. Если хочешь записать это на диск, нажми «Сохранить выбор»");
    });

    $("#downloadSelectionBtn").addEventListener("click", () => {
      downloadSelectedArtists();
      refreshSelectionUi("JSON со списком выбранных артистов скачан");
    });
  } catch (error) {
    document.body.innerHTML = `
      <div class="page">
        <section class="glass panel" style="margin-top:24px">
          <h1 style="font-size:42px">Дашборд не смог загрузить данные</h1>
          <p class="lead" style="max-width:60em">Ошибка: ${error.message}</p>
          <p class="metric-sub" style="margin-top:16px">
            Проверь, что рядом лежат файлы <span class="mono">spotify_library_audit_report.json</span> и
            <span class="mono">ACTUAL SPOTIFY ARTISTS.json</span>, и что страница открыта через локальный сервер.
          </p>
        </section>
      </div>
    `;
  }
}

bootstrap();
