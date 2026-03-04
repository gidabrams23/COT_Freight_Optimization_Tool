(function () {
  function clone(value) {
    return JSON.parse(JSON.stringify(value || {}));
  }

  function parseMoney(value) {
    const text = `${value ?? ""}`.trim();
    if (!text) return null;
    const parsed = Number.parseFloat(text.replace(/[$,]/g, ""));
    if (!Number.isFinite(parsed)) return null;
    return parsed > 0 ? parsed : null;
  }

  function moneyInput(value) {
    if (value === null || value === undefined || value === "") return "";
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toFixed(2) : "";
  }

  function isBlankMoney(value) {
    if (value === null || value === undefined || value === "") return true;
    const parsed = Number(value);
    return !Number.isFinite(parsed) || parsed <= 0;
  }

  function rateInput(value) {
    if (isBlankMoney(value)) return "";
    return Number(value).toFixed(2);
  }

  function moneyText(value) {
    if (isBlankMoney(value)) return "\u2014";
    const parsed = Number(value);
    return `$${parsed.toFixed(2)}`;
  }

  function sameMoney(a, b) {
    if ((a === null || a === undefined || a === "") && (b === null || b === undefined || b === "")) return true;
    if (a === null || a === undefined || a === "" || b === null || b === undefined || b === "") return false;
    return Math.abs(Number(a) - Number(b)) < 0.0001;
  }

  function lanePendingKey(carrier, rowKey, plant) {
    return `${carrier}|${rowKey}|${plant}`;
  }

  function altPendingKey(code, field, plant) {
    return `${code}|${field}|${plant}`;
  }

  function isTrailerBooleanField(field) {
    return field === "requires_return_miles" || field === "apply_fuel_surcharge";
  }

  function init() {
    const root = document.getElementById("rates-v2-root");
    if (!root || root.dataset.initialized === "1") return;
    root.dataset.initialized = "1";

    const isAdmin = root.dataset.isAdmin === "1";
    const dataEl = document.getElementById("rates-v2-data");
    if (!dataEl) return;

    let parsed;
    try {
      parsed = JSON.parse(dataEl.textContent || "{}");
    } catch (error) {
      parsed = {};
    }
    if (!parsed || typeof parsed !== "object") return;

    const baseline = clone(parsed);
    const plants = Array.isArray(baseline.plants) ? baseline.plants : [];
    const plantNames = baseline.plant_names || {};
    const plantColors = baseline.plant_colors || {};

    const tabsEl = document.getElementById("rates-v2-carrier-tabs");
    const accessorialEl = document.getElementById("rates-v2-accessorials");
    const toolbarEl = document.getElementById("rates-v2-toolbar");
    const matrixShellEl = document.getElementById("rates-v2-matrix-shell");
    const matrixHeadEl = document.getElementById("rates-v2-matrix-head");
    const matrixBodyEl = document.getElementById("rates-v2-matrix-body");
    const trailerSectionEl = document.getElementById("rates-v2-trailer-section");
    const trailerListEl = document.getElementById("rates-v2-trailer-list");
    const exportBtn = document.getElementById("rates-v2-export-btn");
    const saveBtn = document.getElementById("rates-v2-save-btn");
    const discardBtn = document.getElementById("rates-v2-discard-btn");
    const statusEl = document.getElementById("rates-status");

    const kpiPlantsEl = document.getElementById("rates-v2-kpi-plants");
    const kpiDestinationsEl = document.getElementById("rates-v2-kpi-destinations");
    const kpiLanesEl = document.getElementById("rates-v2-kpi-lanes");
    const kpiAvgEl = document.getElementById("rates-v2-kpi-avg");
    const kpiChangedEl = document.getElementById("rates-v2-kpi-changed");

    if (!tabsEl || !accessorialEl || !toolbarEl || !matrixShellEl || !matrixHeadEl || !matrixBodyEl || !trailerSectionEl || !trailerListEl || !exportBtn) {
      return;
    }

    const state = {
      carrier: (baseline.carrier_order || ["fls"])[0] || "fls",
      editing: null,
      pending: {
        fls_lanes: {},
        ryder_lanes: {},
        lst_lanes: {},
        alternate_cells: {},
        accessorial: { fls: {}, ryder: {}, lst: {} },
      },
    };

    function setStatus(message) {
      if (!statusEl) return;
      statusEl.textContent = message || "";
    }

    function carrierData(key) {
      return (baseline.carriers || {})[key] || null;
    }

    function laneBaseCell(carrierKey, rowKey, plant) {
      const carrier = carrierData(carrierKey);
      if (!carrier) return null;
      const row = (carrier.lanes || {})[rowKey] || {};
      const cell = row[plant];
      return cell && typeof cell === "object" ? cell : null;
    }

    function laneValue(carrierKey, rowKey, plant) {
      if (carrierKey === "alternate") {
        const pending = state.pending.alternate_cells[altPendingKey(rowKey, "rate_per_mile", plant)];
        if (pending) return pending.value;
        const base = laneBaseCell(carrierKey, rowKey, plant);
        return base ? base.rate_per_mile : null;
      }
      const map = state.pending[`${carrierKey}_lanes`];
      const pending = map ? map[lanePendingKey(carrierKey, rowKey, plant)] : null;
      if (pending) return pending.rate_per_mile;
      const base = laneBaseCell(carrierKey, rowKey, plant);
      return base ? base.rate_per_mile : null;
    }

    function laneChanged(carrierKey, rowKey, plant) {
      if (carrierKey === "alternate") return Boolean(state.pending.alternate_cells[altPendingKey(rowKey, "rate_per_mile", plant)]);
      const map = state.pending[`${carrierKey}_lanes`];
      return Boolean(map && map[lanePendingKey(carrierKey, rowKey, plant)]);
    }

    function setLanePending(carrierKey, rowKey, plant, value) {
      if (carrierKey === "alternate") {
        const key = altPendingKey(rowKey, "rate_per_mile", plant);
        const base = laneBaseCell("alternate", rowKey, plant);
        const baseValue = base ? base.rate_per_mile : null;
        if (sameMoney(baseValue, value)) delete state.pending.alternate_cells[key];
        else {
          state.pending.alternate_cells[key] = { trailer_type_code: rowKey, origin_plant: plant, field: "rate_per_mile", value };
        }
        return;
      }
      const map = state.pending[`${carrierKey}_lanes`];
      if (!map) return;
      const key = lanePendingKey(carrierKey, rowKey, plant);
      const base = laneBaseCell(carrierKey, rowKey, plant);
      const baseValue = base ? base.rate_per_mile : null;
      if (sameMoney(baseValue, value)) {
        delete map[key];
        return;
      }
      const payload = { origin_plant: plant, rate_per_mile: value };
      if (carrierKey === "fls" || carrierKey === "lst") payload.destination_state = rowKey;
      if (carrierKey === "fls" && base && base.rate_id) {
        payload.rate_id = base.rate_id;
        payload.effective_year = base.effective_year;
      }
      map[key] = payload;
    }

    function accessorialBase(carrierKey, field) {
      const carrier = carrierData(carrierKey);
      const value = carrier && carrier.accessorial ? carrier.accessorial[field] : 0;
      return value === null || value === undefined ? 0 : Number(value) || 0;
    }

    function accessorialValue(carrierKey, field) {
      const pending = state.pending.accessorial[carrierKey] || {};
      if (Object.prototype.hasOwnProperty.call(pending, field)) return pending[field];
      return accessorialBase(carrierKey, field);
    }

    function setAccessorialPending(carrierKey, field, value) {
      const map = state.pending.accessorial[carrierKey];
      if (!map) return;
      const base = accessorialBase(carrierKey, field);
      if (sameMoney(base, value)) delete map[field];
      else map[field] = value;
    }

    function trailerSection(code) {
      return (baseline.trailer_sections || []).find((entry) => entry.code === code) || null;
    }

    function trailerValue(code, field, plant) {
      const pending = state.pending.alternate_cells[altPendingKey(code, field, plant)];
      if (pending) return pending.value;
      const section = trailerSection(code);
      if (!section) return null;
      const row = (section.rows || []).find((entry) => entry.field === field);
      if (!row || !row.values) return null;
      return row.values[plant] === undefined ? null : row.values[plant];
    }

    function setTrailerPending(code, field, plant, value) {
      const key = altPendingKey(code, field, plant);
      const section = trailerSection(code);
      const row = section ? (section.rows || []).find((entry) => entry.field === field) : null;
      const baseValue = row && row.values ? row.values[plant] : null;
      if (isTrailerBooleanField(field)) {
        if (Boolean(baseValue) === Boolean(value)) delete state.pending.alternate_cells[key];
        else state.pending.alternate_cells[key] = { trailer_type_code: code, origin_plant: plant, field, value: Boolean(value) };
        return;
      }
      if (sameMoney(baseValue, value)) delete state.pending.alternate_cells[key];
      else state.pending.alternate_cells[key] = { trailer_type_code: code, origin_plant: plant, field, value };
    }

    function trailerChanged(code, field, plant) {
      return Boolean(state.pending.alternate_cells[altPendingKey(code, field, plant)]);
    }

    function filteredRows() {
      const carrier = carrierData(state.carrier);
      if (!carrier) return [];
      return carrier.rows || [];
    }

    function unsavedCounts() {
      const laneCount = Object.keys(state.pending.fls_lanes).length + Object.keys(state.pending.ryder_lanes).length + Object.keys(state.pending.lst_lanes).length + Object.values(state.pending.alternate_cells).filter((entry) => entry.field === "rate_per_mile").length;
      const extraCount = Object.keys(state.pending.accessorial.fls).length + Object.keys(state.pending.accessorial.ryder).length + Object.keys(state.pending.accessorial.lst).length + Object.values(state.pending.alternate_cells).filter((entry) => entry.field !== "rate_per_mile").length;
      return { laneCount, total: laneCount + extraCount };
    }

    function renderTabs() {
      tabsEl.innerHTML = (baseline.carrier_order || []).map((carrierKey) => {
        const carrier = carrierData(carrierKey);
        if (!carrier) return "";
        const active = state.carrier === carrierKey;
        return `<button type="button" class="rates-v2-carrier-tab${active ? " is-active" : ""}" data-carrier="${carrierKey}" role="tab" aria-selected="${active ? "true" : "false"}">${carrier.label}</button>`;
      }).join("");
    }

    function renderAccessorial() {
      const cards = [
        { field: "per_stop", label: "Per Stop Fee", hint: "Per additional stop" },
        { field: "load_minimum", label: "Load Minimum", hint: "Minimum charge per load" },
        { field: "fuel_surcharge", label: "Fuel Surcharge", hint: "Per mile" },
      ];
      accessorialEl.innerHTML = cards.map((card) => {
        const value = accessorialValue(state.carrier, card.field);
        const changed = state.carrier === "alternate" ? false : !sameMoney(accessorialBase(state.carrier, card.field), value);
        const disabledAttr = isAdmin && state.carrier !== "alternate" ? "" : " disabled";
        return `<div class="rates-v2-accessorial-card${changed ? " is-changed" : ""}"><div class="rates-v2-accessorial-label">${card.label}</div><div class="rates-v2-accessorial-value-wrap"><span class="rates-v2-currency">$</span><input class="rates-v2-accessorial-input" data-accessorial-field="${card.field}" type="number" step="0.01" min="0" value="${moneyInput(value)}"${disabledAttr}></div><div class="rates-v2-accessorial-hint">${card.hint}</div></div>`;
      }).join("");
    }

    function renderKpis() {
      const carrier = carrierData(state.carrier);
      if (!carrier) return;
      let laneCount = 0;
      let total = 0;
      (carrier.rows || []).forEach((row) => {
        plants.forEach((plant) => {
          const value = laneValue(state.carrier, row.key, plant);
          if (isBlankMoney(value)) return;
          const parsed = Number(value);
          laneCount += 1;
          total += parsed;
        });
      });
      const changedLaneCount =
        state.carrier === "fls" ? Object.keys(state.pending.fls_lanes).length :
        state.carrier === "ryder" ? Object.keys(state.pending.ryder_lanes).length :
        state.carrier === "lst" ? Object.keys(state.pending.lst_lanes).length :
        Object.values(state.pending.alternate_cells).filter((entry) => entry.field === "rate_per_mile").length;

      if (kpiPlantsEl) kpiPlantsEl.textContent = `${plants.length} Plants`;
      if (kpiDestinationsEl) kpiDestinationsEl.textContent = `${(carrier.rows || []).length} Destinations`;
      if (kpiLanesEl) kpiLanesEl.textContent = `${laneCount} Lanes`;
      if (kpiAvgEl) kpiAvgEl.textContent = `Avg ${laneCount ? `$${(total / laneCount).toFixed(2)}/mi` : "$0.00/mi"}`;
      if (kpiChangedEl) {
        if (changedLaneCount > 0) {
          kpiChangedEl.classList.remove("hidden");
          kpiChangedEl.textContent = `${changedLaneCount} Changed Lanes`;
        } else {
          kpiChangedEl.classList.add("hidden");
        }
      }
    }

    function renderMatrix() {
      const carrier = carrierData(state.carrier);
      if (!carrier) return;
      matrixHeadEl.innerHTML = `<tr><th class="rates-v2-destination-head">Destination</th>${plants.map((plant) => `<th><span class="rates-v2-plant-chip" style="--plant-chip:${plantColors[plant] || "#64748b"}">${plant}</span><span class="rates-v2-plant-name">${plantNames[plant] || plant}</span></th>`).join("")}</tr>`;
      const rows = filteredRows();
      matrixBodyEl.innerHTML = rows.map((row) => {
        const cells = plants.map((plant) => {
          const value = laneValue(state.carrier, row.key, plant);
          const changed = laneChanged(state.carrier, row.key, plant);
          const editable = isAdmin;
          const active = !isBlankMoney(value);
          const editing = state.editing && state.editing.kind === "matrix" && state.editing.carrier === state.carrier && state.editing.rowKey === row.key && state.editing.plant === plant;
          const classes = ["rates-v2-cell", active ? "is-active" : "is-inactive", changed ? "is-changed" : "", editable ? "is-editable" : ""].filter(Boolean).join(" ");
          if (editing) return `<td class="${classes}" data-row="${row.key}" data-plant="${plant}"><input class="rates-v2-inline-input" data-edit-input="matrix" type="number" step="0.01" min="0" value="${rateInput(value)}">${changed ? '<span class="rates-v2-cell-dot" aria-hidden="true"></span>' : ""}</td>`;
          return `<td class="${classes}" data-row="${row.key}" data-plant="${plant}"><span class="rates-v2-cell-value">${moneyText(value)}</span>${changed ? '<span class="rates-v2-cell-dot" aria-hidden="true"></span>' : ""}</td>`;
        }).join("");
        return `<tr><td class="rates-v2-row-label">${row.label}</td>${cells}</tr>`;
      }).join("");
      if (!rows.length) matrixBodyEl.innerHTML = `<tr><td class="rates-v2-empty" colspan="${plants.length + 1}">No rate rows available.</td></tr>`;
      activateEditor();
    }

    function renderTrailerSections() {
      if (state.carrier !== "alternate") {
        trailerSectionEl.hidden = true;
        trailerListEl.innerHTML = "";
        return;
      }
      trailerSectionEl.hidden = false;
      trailerListEl.innerHTML = (baseline.trailer_sections || []).map((section) => {
        const activeCount = plants.filter((plant) => trailerValue(section.code, "rate_per_mile", plant) !== null).length;
        const changedSection = Object.values(state.pending.alternate_cells).some((entry) => entry.trailer_type_code === section.code);
        const rowHtml = (section.rows || []).map((row) => {
          const rowClass = row.field === "rate_per_mile" ? "rates-v2-row-primary" : "";
          const cells = plants.map((plant) => {
            const rateVal = trailerValue(section.code, "rate_per_mile", plant);
            const value = trailerValue(section.code, row.field, plant);
            const active = row.field === "rate_per_mile" ? !isBlankMoney(value) : !isBlankMoney(rateVal);
            const editable = isAdmin;
            const changed = trailerChanged(section.code, row.field, plant);
            const editing = state.editing && state.editing.kind === "trailer" && state.editing.code === section.code && state.editing.field === row.field && state.editing.plant === plant;
            const classes = ["rates-v2-cell", active ? "is-active" : "is-inactive", changed ? "is-changed" : "", editable ? "is-editable" : ""].filter(Boolean).join(" ");
            if (isTrailerBooleanField(row.field)) {
              const checked = Boolean(value);
              return `<td class="${classes}" data-code="${section.code}" data-field="${row.field}" data-plant="${plant}"><input type="checkbox" class="rates-v2-trailer-toggle" data-toggle-bool ${checked ? "checked" : ""} ${editable ? "" : "disabled"}><span class="rates-v2-cell-value rates-v2-cell-bool">${active ? (checked ? "Yes" : "No") : "\u2014"}</span>${changed ? '<span class="rates-v2-cell-dot" aria-hidden="true"></span>' : ""}</td>`;
            }
            if (editing) return `<td class="${classes}" data-code="${section.code}" data-field="${row.field}" data-plant="${plant}"><input class="rates-v2-inline-input" data-edit-input="trailer" type="number" step="0.01" min="0" value="${rateInput(value)}">${changed ? '<span class="rates-v2-cell-dot" aria-hidden="true"></span>' : ""}</td>`;
            return `<td class="${classes}" data-code="${section.code}" data-field="${row.field}" data-plant="${plant}"><span class="rates-v2-cell-value">${active ? moneyText(value) : "\u2014"}</span>${changed ? '<span class="rates-v2-cell-dot" aria-hidden="true"></span>' : ""}</td>`;
          }).join("");
          return `<tr class="${rowClass}"><td class="rates-v2-row-label">${row.label}</td>${cells}</tr>`;
        }).join("");
        return `<details class="rates-v2-trailer-card" data-trailer-code="${section.code}" open><summary><span class="rates-v2-trailer-summary-left"><span class="material-symbols-outlined" aria-hidden="true">${section.icon || "local_shipping"}</span><strong>${section.label}</strong></span><span class="rates-v2-trailer-summary-right"><span>${activeCount}/${plants.length} plants active</span>${changedSection ? '<span class="meta-chip rates-v2-updated-chip">Updated</span>' : ""}</span></summary><div class="rates-v2-trailer-table-wrap"><table class="settings-table rates-v2-matrix rates-v2-matrix-small"><thead><tr><th>Metric</th>${plants.map((plant) => `<th>${plant}</th>`).join("")}</tr></thead><tbody>${rowHtml}</tbody></table></div></details>`;
      }).join("");
      activateEditor();
    }

    function renderActionButtons() {
      if (!saveBtn || !discardBtn) return;
      const counts = unsavedCounts();
      if (counts.total > 0) {
        saveBtn.disabled = false;
        discardBtn.disabled = false;
        saveBtn.textContent = counts.laneCount > 0 ? `Save Rates (${counts.laneCount})` : `Save Rates (${counts.total})`;
      } else {
        saveBtn.textContent = "Save Rates";
        saveBtn.disabled = true;
        discardBtn.disabled = true;
      }
    }

    function renderAll() {
      const showAlternateOnly = state.carrier === "alternate";
      renderTabs();
      renderAccessorial();
      renderKpis();
      if (toolbarEl) toolbarEl.hidden = showAlternateOnly;
      if (matrixShellEl) matrixShellEl.hidden = showAlternateOnly;
      if (!showAlternateOnly) renderMatrix();
      renderTrailerSections();
      renderActionButtons();
    }

    function activateEditor() {
      const input = document.querySelector(".rates-v2-inline-input[data-edit-input]");
      if (!input) return;
      setTimeout(() => {
        input.focus();
        input.select();
      }, 0);
      const commit = () => {
        if (!state.editing) return;
        if (state.editing.kind === "matrix") {
          setLanePending(state.editing.carrier, state.editing.rowKey, state.editing.plant, parseMoney(input.value));
        } else {
          const value = parseMoney(input.value);
          setTrailerPending(state.editing.code, state.editing.field, state.editing.plant, value);
          if (state.editing.field === "rate_per_mile") {
            setLanePending("alternate", state.editing.code, state.editing.plant, value);
          }
        }
        state.editing = null;
        renderAll();
      };
      input.addEventListener("blur", commit, { once: true });
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          commit();
        }
        if (event.key === "Escape") {
          event.preventDefault();
          state.editing = null;
          renderAll();
        }
      });
    }

    tabsEl.addEventListener("click", (event) => {
      const button = event.target.closest("[data-carrier]");
      if (!button) return;
      const carrier = button.dataset.carrier;
      if (!carrierData(carrier)) return;
      state.carrier = carrier;
      state.editing = null;
      renderAll();
    });

    accessorialEl.addEventListener("blur", (event) => {
      const input = event.target.closest("[data-accessorial-field]");
      if (!input) return;
      if (state.carrier === "alternate") return;
      const field = input.dataset.accessorialField;
      const parsed = parseMoney(input.value);
      setAccessorialPending(state.carrier, field, parsed === null ? 0 : parsed);
      renderAll();
    }, true);

    accessorialEl.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      const input = event.target.closest("[data-accessorial-field]");
      if (!input) return;
      event.preventDefault();
      input.blur();
    });

    matrixBodyEl.addEventListener("click", (event) => {
      const cell = event.target.closest("td.rates-v2-cell.is-editable");
      if (!cell || !isAdmin) return;
      state.editing = { kind: "matrix", carrier: state.carrier, rowKey: cell.dataset.row, plant: cell.dataset.plant };
      renderMatrix();
    });

    trailerListEl.addEventListener("click", (event) => {
      if (event.target.closest("summary")) return;
      const toggle = event.target.closest("input[data-toggle-bool]");
      if (toggle && isAdmin) {
        const cell = toggle.closest("td.rates-v2-cell.is-editable");
        if (!cell) return;
        setTrailerPending(cell.dataset.code, cell.dataset.field, cell.dataset.plant, Boolean(toggle.checked));
        renderAll();
        return;
      }
      const cell = event.target.closest("td.rates-v2-cell.is-editable");
      if (!cell || !isAdmin) return;
      if (isTrailerBooleanField(cell.dataset.field)) return;
      state.editing = { kind: "trailer", code: cell.dataset.code, field: cell.dataset.field, plant: cell.dataset.plant };
      renderTrailerSections();
    });

    if (discardBtn) {
      discardBtn.addEventListener("click", () => {
        state.pending = { fls_lanes: {}, ryder_lanes: {}, lst_lanes: {}, alternate_cells: {}, accessorial: { fls: {}, ryder: {}, lst: {} } };
        state.editing = null;
        setStatus("Changes discarded.");
        renderAll();
      });
    }

    function applyPendingToBaseline() {
      Object.values(state.pending.fls_lanes).forEach((item) => {
        const rows = ((baseline.carriers || {}).fls || {}).lanes || {};
        if (!rows[item.destination_state]) rows[item.destination_state] = {};
        rows[item.destination_state][item.origin_plant] = { rate_per_mile: item.rate_per_mile, rate_id: item.rate_id || ((rows[item.destination_state][item.origin_plant] || {}).rate_id || null), effective_year: item.effective_year || ((rows[item.destination_state][item.origin_plant] || {}).effective_year || new Date().getFullYear()) };
      });
      Object.values(state.pending.ryder_lanes).forEach((item) => {
        const rows = ((baseline.carriers || {}).ryder || {}).lanes || {};
        if (!rows.ALL_STATES) rows.ALL_STATES = {};
        rows.ALL_STATES[item.origin_plant] = { rate_per_mile: item.rate_per_mile };
      });
      Object.values(state.pending.lst_lanes).forEach((item) => {
        const rows = ((baseline.carriers || {}).lst || {}).lanes || {};
        if (!rows[item.destination_state]) rows[item.destination_state] = {};
        rows[item.destination_state][item.origin_plant] = { rate_per_mile: item.rate_per_mile };
      });
      Object.values(state.pending.alternate_cells).forEach((item) => {
        const rows = ((baseline.carriers || {}).alternate || {}).lanes || {};
        if (!rows[item.trailer_type_code]) rows[item.trailer_type_code] = {};
        if (item.field === "rate_per_mile") rows[item.trailer_type_code][item.origin_plant] = { rate_per_mile: item.value };
        const section = (baseline.trailer_sections || []).find((entry) => entry.code === item.trailer_type_code);
        if (!section) return;
        const row = (section.rows || []).find((entry) => entry.field === item.field);
        if (!row) return;
        if (!row.values) row.values = {};
        row.values[item.origin_plant] = item.value;
      });
      ["fls", "ryder", "lst"].forEach((carrierKey) => {
        const carrier = (baseline.carriers || {})[carrierKey];
        if (!carrier) return;
        carrier.accessorial = carrier.accessorial || {};
        Object.entries(state.pending.accessorial[carrierKey] || {}).forEach(([field, value]) => {
          carrier.accessorial[field] = value;
        });
      });
    }

    if (saveBtn) {
      saveBtn.addEventListener("click", async () => {
      const pendingPayload = {
        fls_lanes: Object.values(state.pending.fls_lanes),
        ryder_lanes: Object.values(state.pending.ryder_lanes),
        lst_lanes: Object.values(state.pending.lst_lanes),
        alternate_cells: Object.values(state.pending.alternate_cells),
      };
      if (Object.keys(state.pending.accessorial.fls).length) pendingPayload.fls_accessorial = Object.assign({}, state.pending.accessorial.fls);
      if (Object.keys(state.pending.accessorial.ryder).length) pendingPayload.ryder_accessorial = Object.assign({}, state.pending.accessorial.ryder);
      if (Object.keys(state.pending.accessorial.lst).length) pendingPayload.lst_accessorial = Object.assign({}, state.pending.accessorial.lst);

      saveBtn.disabled = true;
      if (discardBtn) discardBtn.disabled = true;
      setStatus("Saving rate changes...");
      try {
        const response = await fetch("/settings/rates/batch-save", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pending: pendingPayload }),
        });
        if (!response.ok) throw new Error(`Save failed: ${response.status}`);
        await response.json();
        applyPendingToBaseline();
        state.pending = { fls_lanes: {}, ryder_lanes: {}, lst_lanes: {}, alternate_cells: {}, accessorial: { fls: {}, ryder: {}, lst: {} } };
        state.editing = null;
        setStatus("Rates saved.");
        renderAll();
      } catch (error) {
        setStatus("Unable to save rates.");
      } finally {
        saveBtn.disabled = false;
        if (discardBtn) discardBtn.disabled = false;
      }
      });
    }

    exportBtn.addEventListener("click", () => {
      const carrier = carrierData(state.carrier);
      if (!carrier) return;
      const rows = filteredRows();
      const lines = [];
      lines.push(["Destination", ...plants].join(","));
      rows.forEach((row) => {
        const values = plants.map((plant) => {
          const value = laneValue(state.carrier, row.key, plant);
          return isBlankMoney(value) ? "" : Number(value).toFixed(2);
        });
        lines.push([row.label, ...values].join(","));
      });
      const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
      const link = document.createElement("a");
      const url = URL.createObjectURL(blob);
      const now = new Date();
      const stamp = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}`;
      link.href = url;
      link.download = `${(carrier.label || state.carrier).toLowerCase().replace(/\s+/g, "_")}_rates_${stamp}.csv`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    });

    renderAll();
  }

  window.RatesV2 = { init };
})();
