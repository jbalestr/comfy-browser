// State
let DATA = [];
const active = {
  checkpoints: new Set(),
  loras: new Set(),
  embeddings: new Set(),
  samplers: new Set(),
  positive_phrases: new Set(),
  negative_phrases: new Set(),
};
let searchText = "";

// Per-group sort direction for the filter sidebar. Phrase groups
// default to rarest-first since common boilerplate tags (shared across
// almost every prompt) are visual noise - the rare, distinguishing
// tags are usually what you're looking for. Tag groups (checkpoint,
// sampler etc.) default to most-common-first since there are usually
// few distinct values and the dominant one is most useful up top.
const sortDirection = {
  checkpoints: "common",
  loras: "common",
  embeddings: "common",
  samplers: "common",
  positive_phrases: "rare",
  negative_phrases: "rare",
};

// Minimum occurrence count for a tag to show up in the sidebar at all.
// Defaults to 2 for phrase groups so pure one-off tags (likely typos
// or one-time experiments) don't flood a rarest-first list; adjustable
// per group via a number input in the sidebar.
const minCountThreshold = {
  positive_phrases: 2,
  negative_phrases: 2,
};

const FILTER_GROUPS = [
  { key: "checkpoints", label: "Checkpoint", type: "tag" },
  { key: "loras", label: "LoRA", type: "tag" },
  { key: "embeddings", label: "Embedding", type: "tag" },
  { key: "samplers", label: "Sampler", type: "tag" },
  { key: "positive_phrases", label: "Positive prompt tags", type: "phrase", sourceField: "positive_prompt" },
  { key: "negative_phrases", label: "Negative prompt tags", type: "phrase", sourceField: "negative_prompt" },
];

// Splits on commas, but only at paren-depth 0 - so a comma INSIDE a
// weighting group, e.g. "(triple braid, ponytail:1.1)", doesn't get
// split apart before stripPromptWeighting ever sees the group as a
// whole. Naive text.split(",") would cut this into "(triple braid" and
// "ponytail:1.1)", leaving a dangling unmatched paren in the tag list.
function splitTopLevelCommas(text) {
  const parts = [];
  let depth = 0;
  let current = "";
  for (const ch of text) {
    if (ch === "(") depth++;
    else if (ch === ")") depth = Math.max(0, depth - 1);
    if (ch === "," && depth === 0) {
      parts.push(current);
      current = "";
    } else {
      current += ch;
    }
  }
  parts.push(current);
  return parts;
}

// Strips ComfyUI/A1111-style prompt-weighting syntax so "(blue dress:1.2)"
// and "blue dress" are treated as the same tag rather than two different
// ones. Syntax reference: (tag) or (tag:weight) increases attention,
// nesting multiplies (so layers are peeled one at a time), and a
// backslash-escaped paren \( \) is literal text the author wants kept,
// not a weighting marker.
function stripPromptWeighting(tag) {
  let s = tag.replace(/\\\(/g, "\x00LPAREN\x00").replace(/\\\)/g, "\x00RPAREN\x00");

  let prev;
  do {
    prev = s;
    const trimmed = s.trim();
    if (trimmed.startsWith("(") && trimmed.endsWith(")")) {
      // Confirm the first '(' is actually matched by the last ')'
      // (not two separate groups like "(a)(b)") before stripping.
      let depth = 0;
      let isSingleWrappedGroup = true;
      for (let i = 0; i < trimmed.length; i++) {
        if (trimmed[i] === "(") depth++;
        else if (trimmed[i] === ")") {
          depth--;
          if (depth === 0 && i !== trimmed.length - 1) {
            isSingleWrappedGroup = false;
            break;
          }
        }
      }
      if (isSingleWrappedGroup) {
        let inner = trimmed.slice(1, -1);
        inner = inner.replace(/:[\d.]+$/, ""); // drop an exposed trailing :weight
        s = inner;
      }
    }
  } while (s !== prev);

  return s.replace(/\x00LPAREN\x00/g, "(").replace(/\x00RPAREN\x00/g, ")").trim();
}

// Splits a ComfyUI-style comma-separated prompt into individual tags,
// preserving multi-word tags ("blue dress", "looking at viewer") as
// single units rather than splitting on whitespace too. This matches
// how these prompts are actually authored - comma is the real
// delimiter, not space.
function splitPromptIntoTags(text) {
  if (!text) return [];
  return splitTopLevelCommas(text)
    .map((t) => t.trim().toLowerCase())
    .filter((t) => t.length > 0)
    .map((t) => t.replace(/embedding:[^\s]+/gi, "").trim())
    .map(stripPromptWeighting)
    // A weighting group can itself contain multiple comma-separated
    // descriptors, e.g. "(triple braid, ponytail:1.1)" - after the
    // weighting wrapper is stripped this is a plain comma list again,
    // so split it into separate filterable tags.
    .flatMap((t) => t.split(",").map((s) => s.trim()))
    .filter((t) => t.length > 0);
}

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

function getItemValuesForGroup(item, group) {
  if (group.type === "phrase") {
    return splitPromptIntoTags(item[group.sourceField]);
  }
  return item[group.key] || [];
}

function countValuesForGroup(group, baseSet) {
  const counts = {};
  baseSet.forEach((item) => {
    getItemValuesForGroup(item, group).forEach((v) => {
      counts[v] = (counts[v] || 0) + 1;
    });
  });
  const entries = Object.entries(counts);
  const direction = sortDirection[group.key] || "common";
  entries.sort((a, b) => (direction === "rare" ? a[1] - b[1] : b[1] - a[1]));
  return entries;
}

const MAX_OPTIONS_PER_GROUP = 60; // long lists get capped; sort direction decides which end survives

function buildFilters() {
  const container = document.getElementById("filters");
  container.innerHTML = "";

  FILTER_GROUPS.forEach((group) => {
    const baseSet = getFilteredData(group.key); // matches everything except this group's own filters
    const values = countValuesForGroup(group, baseSet);
    if (values.length === 0) return;
    container.appendChild(buildFilterGroup(group, values));
  });
}

function buildFilterGroup(group, values) {
  const div = document.createElement("div");
  div.className = "filter-group";

  const threshold = minCountThreshold[group.key] || 1;
  const aboveThreshold = values.filter(([, count]) => count >= threshold);

  const header = document.createElement("div");
  header.className = "filter-group-header";

  const heading = document.createElement("h3");
  heading.textContent = `${group.label} (${aboveThreshold.length})`;
  header.appendChild(heading);

  if (group.type === "phrase") {
    const sortBtn = document.createElement("button");
    sortBtn.className = "sort-toggle";
    const direction = sortDirection[group.key] || "rare";
    sortBtn.textContent = direction === "rare" ? "Rarest first" : "Common first";
    sortBtn.title = "Click to flip sort order";
    sortBtn.addEventListener("click", () => {
      sortDirection[group.key] = direction === "rare" ? "common" : "rare";
      buildFilters();
    });
    header.appendChild(sortBtn);
  }

  div.appendChild(header);

  if (group.type === "phrase") {
    div.appendChild(buildThresholdControl(group, threshold));
  }

  const scrollWrap = document.createElement("div");
  scrollWrap.className = "filter-group-scroll";

  aboveThreshold.slice(0, MAX_OPTIONS_PER_GROUP).forEach(([value, count]) => {
    scrollWrap.appendChild(buildFilterOption(group, value, count));
  });

  div.appendChild(scrollWrap);
  return div;
}

function buildThresholdControl(group, currentThreshold) {
  const wrap = document.createElement("div");
  wrap.className = "threshold-control";

  const label = document.createElement("label");
  label.textContent = "Min occurrences:";

  const input = document.createElement("input");
  input.type = "number";
  input.min = "1";
  input.value = currentThreshold;
  input.addEventListener("change", () => {
    const val = Math.max(1, parseInt(input.value, 10) || 1);
    minCountThreshold[group.key] = val;
    buildFilters();
  });

  wrap.append(label, input);
  return wrap;
}

function buildFilterOption(group, value, count) {
  const label = document.createElement("label");
  label.className = "filter-option";

  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = active[group.key].has(value);
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) {
      active[group.key].add(value);
    } else {
      active[group.key].delete(value);
    }
    buildFilters(); // recompute all counts against the new active set
    render();
  });

  const valueLabel = document.createElement("span");
  valueLabel.className = "filter-label";
  valueLabel.textContent = value;
  valueLabel.title = value; // full text on hover when truncated

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
  buildFilters();
  render();
}

// ---------- Filtering logic ----------

function itemMatchesGroup(item, group) {
  const activeSet = active[group.key];
  if (activeSet.size === 0) return true;
  const itemValues = new Set(getItemValuesForGroup(item, group));

  if (group.type === "phrase") {
    // Progressive narrowing: selecting "outdoor" then "wilma flintstone"
    // should require ALL of them to be present, not any.
    return [...activeSet].every((v) => itemValues.has(v));
  }
  // Tag groups (checkpoint, LoRA, etc): an image typically has one
  // checkpoint, so multiple selections within the group are OR'd -
  // "show images using checkpoint A OR checkpoint B".
  return [...activeSet].some((v) => itemValues.has(v));
}

function itemMatchesActiveFilters(item, excludeGroupKey) {
  for (const group of FILTER_GROUPS) {
    if (group.key === excludeGroupKey) continue;
    if (!itemMatchesGroup(item, group)) return false;
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

function getFilteredData(excludeGroupKey) {
  return DATA.filter(
    (item) => itemMatchesActiveFilters(item, excludeGroupKey) && itemMatchesSearch(item)
  );
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
