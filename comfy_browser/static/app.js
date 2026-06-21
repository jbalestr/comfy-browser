// State
let DATA = [];
const active = { checkpoints: new Set(), loras: new Set(), embeddings: new Set(), samplers: new Set() };
let searchText = "";

const FILTER_GROUPS = [
  { key: "checkpoints", label: "Checkpoint" },
  { key: "loras", label: "LoRA" },
  { key: "embeddings", label: "Embedding" },
  { key: "samplers", label: "Sampler" },
];

// ---------- Data loading ----------

const POLL_INTERVAL_MS = 600;
let pollHandle = null;

async function loadData(forceRefresh) {
  const url = forceRefresh ? "/api/data?refresh=1" : "/api/data";
  const res = await fetch(url);
  const payload = await res.json();
  handleScanPayload(payload);

  if (payload.scanning) {
    startPolling();
  }
}

function startPolling() {
  if (pollHandle) return; // already polling
  pollHandle = setInterval(async () => {
    const res = await fetch("/api/data");
    const payload = await res.json();
    handleScanPayload(payload);
    if (!payload.scanning) {
      stopPolling();
    }
  }, POLL_INTERVAL_MS);
}

function stopPolling() {
  if (pollHandle) {
    clearInterval(pollHandle);
    pollHandle = null;
  }
}

function handleScanPayload(payload) {
  updateScanBanner(payload);
  if (payload.data !== null) {
    DATA = payload.data;
    buildFilters();
    render();
  }
}

function updateScanBanner(payload) {
  const banner = document.getElementById("scan-banner");
  if (!payload.scanning) {
    banner.style.display = "none";
    return;
  }
  banner.style.display = "block";
  if (payload.total > 0) {
    const pct = Math.round((payload.done / payload.total) * 100);
    banner.textContent = `Scanning new images: ${payload.done} / ${payload.total} (${pct}%)${payload.data ? " — showing previous results below" : ""}`;
  } else {
    banner.textContent = "Scanning folder...";
  }
}

// ---------- Filter sidebar ----------

function countValuesForField(field) {
  const counts = {};
  DATA.forEach((item) => {
    (item[field] || []).forEach((v) => {
      counts[v] = (counts[v] || 0) + 1;
    });
  });
  return Object.entries(counts).sort((a, b) => b[1] - a[1]);
}

function buildFilters() {
  const container = document.getElementById("filters");
  container.innerHTML = "";

  FILTER_GROUPS.forEach((group) => {
    const values = countValuesForField(group.key);
    if (values.length === 0) return;
    container.appendChild(buildFilterGroup(group, values));
  });
}

function buildFilterGroup(group, values) {
  const div = document.createElement("div");
  div.className = "filter-group";

  const heading = document.createElement("h3");
  heading.textContent = `${group.label} (${values.length})`;
  div.appendChild(heading);

  values.forEach(([value, count]) => {
    div.appendChild(buildFilterOption(group.key, value, count));
  });

  return div;
}

function buildFilterOption(groupKey, value, count) {
  const label = document.createElement("label");
  label.className = "filter-option";

  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) {
      active[groupKey].add(value);
    } else {
      active[groupKey].delete(value);
    }
    render();
  });

  const valueLabel = document.createElement("span");
  valueLabel.textContent = value;

  const countLabel = document.createElement("span");
  countLabel.className = "filter-count";
  countLabel.textContent = count;

  label.append(checkbox, valueLabel, countLabel);
  return label;
}

function clearAllFilters() {
  Object.values(active).forEach((set) => set.clear());
  searchText = "";
  document.getElementById("search").value = "";
  document.querySelectorAll(".filter-option input").forEach((cb) => {
    cb.checked = false;
  });
  render();
}

// ---------- Filtering logic ----------

function itemMatchesActiveFilters(item) {
  for (const key of Object.keys(active)) {
    if (active[key].size === 0) continue;
    const itemValues = new Set(item[key] || []);
    const hasAnyActiveValue = [...active[key]].some((v) => itemValues.has(v));
    if (!hasAnyActiveValue) return false;
  }
  return true;
}

function itemMatchesSearch(item) {
  if (!searchText) return true;
  const haystack = [
    item.positive_prompt || "",
    item.negative_prompt || "",
    item.filename || "",
  ].join(" ").toLowerCase();
  return haystack.includes(searchText.toLowerCase());
}

function getFilteredData() {
  return DATA.filter((item) => itemMatchesActiveFilters(item) && itemMatchesSearch(item));
}

// ---------- Rendering ----------

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function render() {
  const filtered = getFilteredData();
  document.getElementById("status").textContent = `${filtered.length} of ${DATA.length} images`;

  const grid = document.getElementById("grid");
  grid.innerHTML = "";

  if (filtered.length === 0) {
    grid.innerHTML = '<div class="empty">No images match the current filters.</div>';
    return;
  }

  filtered.forEach((item) => grid.appendChild(buildCard(item)));
}

function buildCard(item) {
  const card = document.createElement("div");
  card.className = "card";

  const tags = [...item.checkpoints, ...item.loras]
    .map((t) => `<span class="tag">${escapeHtml(t)}</span>`)
    .join("");

  card.innerHTML = `
    <img src="/image/${encodeURIComponent(item.filename)}" loading="lazy">
    <div class="card-info">
      <div class="fn">${escapeHtml(item.filename)}</div>
      ${item.seeds.length ? `<div>Seed: ${item.seeds.join(", ")}</div>` : ""}
      <div class="tag-row">${tags}</div>
    </div>
  `;

  card.addEventListener("click", () => openModal(item));
  return card;
}

// ---------- Modal ----------

function buildModalMetaHtml(item) {
  const section = (title, value) => (value ? `<h3>${title}</h3><p>${value}</p>` : "");

  return [
    section("Filename", escapeHtml(item.filename)),
    section("Size", item.width ? `${item.width} x ${item.height}` : ""),
    section("Seed", item.seeds.length ? item.seeds.join(", ") : ""),
    section("Sampler", item.samplers.length ? item.samplers.join(", ") : ""),
    section("Checkpoint", item.checkpoints.length ? item.checkpoints.join(", ") : ""),
    section("LoRAs", item.loras.length ? item.loras.join("\n") : ""),
    section("Embeddings", item.embeddings.length ? item.embeddings.join(", ") : ""),
    section("Prompt", item.positive_prompt ? escapeHtml(item.positive_prompt) : ""),
    section("Negative", item.negative_prompt ? escapeHtml(item.negative_prompt) : ""),
  ].join("");
}

function openModal(item) {
  document.getElementById("modal-img").src = `/image/${encodeURIComponent(item.filename)}`;
  document.getElementById("modal-meta").innerHTML = buildModalMetaHtml(item);
  document.getElementById("modal").classList.add("open");
}

function closeModal() {
  document.getElementById("modal").classList.remove("open");
}

// ---------- Wiring ----------

function init() {
  document.getElementById("modal-close").addEventListener("click", closeModal);
  document.getElementById("modal").addEventListener("click", (e) => {
    if (e.target.id === "modal") closeModal();
  });

  document.getElementById("search").addEventListener("input", (e) => {
    searchText = e.target.value;
    render();
  });

  document.getElementById("clear-btn").addEventListener("click", clearAllFilters);

  document.getElementById("refresh-btn").addEventListener("click", async () => {
    const btn = document.getElementById("refresh-btn");
    btn.disabled = true;
    btn.textContent = "Rescanning...";
    await loadData(true);
    waitForScanToFinishThenResetButton(btn);
  });

  loadData(false);
}

function waitForScanToFinishThenResetButton(btn) {
  const checkInterval = setInterval(async () => {
    const res = await fetch("/api/scan-status");
    const status = await res.json();
    if (!status.running) {
      clearInterval(checkInterval);
      btn.disabled = false;
      btn.textContent = "Rescan folder";
    }
  }, POLL_INTERVAL_MS);
}

init();
