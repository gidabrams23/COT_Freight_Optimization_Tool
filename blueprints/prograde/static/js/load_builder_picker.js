function _formatFeet(value) {
  const rounded = Math.round(_feetNumber(value) * 10) / 10;
  return `${rounded.toFixed(1)}'`;
}

function _skuCategoryLabel(sku) {
  if (SESSION_BRAND === "pj") {
    const picker = String(sku.picker_category_label || sku.picker_category || "").trim();
    const fallback = String(sku.pj_category || sku.mcat || "").trim();
    return picker || fallback || "Uncategorized";
  }
  const resolved = String(sku.mcat || sku.pj_category || "Uncategorized").trim();
  return resolved || "Uncategorized";
}

function _skuModelLabel(sku) {
  const resolved = String(sku.model || "Unknown Model").trim();
  return resolved || "Unknown Model";
}

function _pjTongueMode(rawMode) {
  return String(rawMode || "").toLowerCase() === "gooseneck" ? "gooseneck" : "standard";
}

function _pjTongueProfile(sku) {
  const explicit = _pjTongueMode(String(sku.picker_tongue_profile || "").trim());
  if (String(sku.picker_tongue_profile || "").trim()) {
    return explicit;
  }
  const category = String(sku.pj_category || "").trim().toLowerCase();
  if (category.includes("gooseneck") || category === "pintle") {
    return "gooseneck";
  }
  const model = _skuModelLabel(sku).toUpperCase();
  const modelPrefix = model.replace(/[^A-Z0-9]/g, "").slice(0, 2);
  if (PJ_GOOSENECK_MODEL_PREFIXES.has(modelPrefix)) {
    return "gooseneck";
  }
  return "standard";
}

function _skuItemNumber(sku) {
  return String(sku.item_number || "").trim();
}

function _skuDescription(sku) {
  return String(sku.description || "").trim();
}

function _pjDeckLengthFt(sku) {
  return _feetNumber(
    sku.deck_length_ft
    ?? sku.bed_length_measured
    ?? sku.bed_length_stated
    ?? sku.bed_length
    ?? 0
  );
}

function _pjDeckHeightFt(sku) {
  return _feetNumber(
    sku.deck_height_ft
    ?? sku.height_top_ft
    ?? sku.height_mid_ft
    ?? 0
  );
}

function _pjDeckProfileLabel(sku) {
  return `L ${_formatFeet(_pjDeckLengthFt(sku))} x H ${_formatFeet(_pjDeckHeightFt(sku))}`;
}

function _pjDisplayItemCode(sku) {
  const explicit = String(sku.picker_item_code || "").trim().toUpperCase();
  if (explicit) return explicit;
  const itemNumber = _skuItemNumber(sku).toUpperCase().replace(/[^A-Z0-9]/g, "");
  if (!itemNumber) return "";
  let modelPrefix = _skuModelLabel(sku).toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 2);
  if (!modelPrefix) {
    modelPrefix = itemNumber.slice(0, 2);
  }
  let tail = itemNumber.startsWith(modelPrefix) ? itemNumber.slice(modelPrefix.length) : itemNumber;
  tail = tail.replace(/^[A-Z]+/, "");
  let digits = (tail.match(/(\d{2})/) || [null, ""])[1];
  if (!digits) {
    digits = (itemNumber.match(/(\d{2})/) || [null, ""])[1];
  }
  return digits ? `${modelPrefix}${digits}` : itemNumber;
}

function _skuTotalLength(sku) {
  const explicit = Number(sku.total_footprint);
  if (Number.isFinite(explicit) && explicit > 0) {
    return explicit;
  }
  const bed = _feetNumber(sku.bed_length || sku.bed_length_measured || sku.bed_length_stated);
  const tongue = _feetNumber(sku.tongue || sku.tongue_feet);
  return bed + tongue;
}

function updateSkuHierarchyButtons() {
  if (!SKU_COLLAPSE_MODELS_BTN || !SKU_EXPAND_ITEMS_BTN) return;
  SKU_COLLAPSE_MODELS_BTN.classList.toggle("active", skuHierarchyMode === "models");
  SKU_EXPAND_ITEMS_BTN.classList.toggle("active", skuHierarchyMode === "items");
}

function applySkuHierarchyMode() {
  if (SESSION_BRAND !== "bigtex") return;
  const showItems = skuHierarchyMode === "items";
  document.querySelectorAll(".sku-group").forEach((group) => {
    group.open = true;
  });
  document.querySelectorAll(".sku-model-group").forEach((group) => {
    group.open = showItems;
  });
  updateSkuHierarchyButtons();
}

function initSkuHierarchyControls() {
  if (SESSION_BRAND !== "bigtex") return;
  if (SKU_COLLAPSE_MODELS_BTN && SKU_COLLAPSE_MODELS_BTN.dataset.bound !== "1") {
    SKU_COLLAPSE_MODELS_BTN.dataset.bound = "1";
    SKU_COLLAPSE_MODELS_BTN.addEventListener("click", () => {
      skuHierarchyMode = "models";
      applySkuFilter(SKU_FILTER ? SKU_FILTER.value : "");
    });
  }
  if (SKU_EXPAND_ITEMS_BTN && SKU_EXPAND_ITEMS_BTN.dataset.bound !== "1") {
    SKU_EXPAND_ITEMS_BTN.dataset.bound = "1";
    SKU_EXPAND_ITEMS_BTN.addEventListener("click", () => {
      skuHierarchyMode = "items";
      applySkuFilter(SKU_FILTER ? SKU_FILTER.value : "");
    });
  }
  updateSkuHierarchyButtons();
}

function _pjModelCode(sku) {
  const explicit = String(sku.picker_model_code || "").trim().toUpperCase();
  if (explicit) return explicit;
  const model = _skuModelLabel(sku).toUpperCase().replace(/[^A-Z0-9]/g, "");
  if (model) return model.slice(0, 2);
  const item = _skuItemNumber(sku).toUpperCase().replace(/[^A-Z0-9]/g, "");
  return item.slice(0, 2);
}

function _getSkuTongueSelection(itemNumber, fallbackMode) {
  const key = String(itemNumber || "").trim().toUpperCase();
  if (!key) return _pjTongueMode(fallbackMode || "standard");
  if (pjSkuTongueSelection.has(key)) {
    return _pjTongueMode(pjSkuTongueSelection.get(key));
  }
  return _pjTongueMode(fallbackMode || "standard");
}

function _setSkuTongueSelection(itemNumber, mode) {
  const key = String(itemNumber || "").trim().toUpperCase();
  if (!key) return;
  pjSkuTongueSelection.set(key, _pjTongueMode(mode));
}

function buildSkuTreeData() {
  const tree = new Map();
  (SKU_RAW || []).forEach((raw) => {
    const sku = raw || {};
    const itemNumber = _skuItemNumber(sku);
    if (!itemNumber) return;

    const tongueProfile = SESSION_BRAND === "pj" ? _pjTongueProfile(sku) : "";

    const category = _skuCategoryLabel(sku);
    const model = _skuModelLabel(sku);
    const modelCode = SESSION_BRAND === "pj" ? _pjModelCode(sku) : model;
    const deckLength = _pjDeckLengthFt(sku);
    const deckHeight = _pjDeckHeightFt(sku);
    const deckProfile = _pjDeckProfileLabel(sku);
    const description = _skuDescription(sku);
    const totalLength = _skuTotalLength(sku);
    const itemDisplay = SESSION_BRAND === "pj" ? _pjDisplayItemCode(sku) : itemNumber;
    const groupLabel = SESSION_BRAND === "pj" ? modelCode : model;
    const search = `${itemNumber} ${itemDisplay} ${model} ${modelCode} ${category} ${description} ${deckProfile} ${tongueProfile}`.toLowerCase();

    if (!tree.has(category)) {
      tree.set(category, new Map());
    }
    const modelMap = tree.get(category);
    if (!modelMap.has(groupLabel)) {
      modelMap.set(groupLabel, []);
    }
    modelMap.get(groupLabel).push({
      itemNumber,
      itemDisplay,
      description,
      totalLength,
      deckLength,
      deckHeight,
      deckProfile,
      model,
      modelCode,
      tongueProfile,
      search,
    });
  });

  const alphaSort = (a, b) =>
    String(a[0] || "").localeCompare(String(b[0] || ""), undefined, { numeric: true, sensitivity: "base" });
  const itemSort = (a, b) =>
    String(a.itemNumber || "").localeCompare(String(b.itemNumber || ""), undefined, { numeric: true, sensitivity: "base" });

  return Array.from(tree.entries())
    .sort(alphaSort)
    .map(([category, modelMap]) => {
      const models = Array.from(modelMap.entries())
        .sort(alphaSort)
        .map(([model, items]) => {
          items.sort(itemSort);
          return { model, items, count: items.length };
        });
      const count = models.reduce((sum, m) => sum + m.count, 0);
      return { category, models, count };
    });
}

function renderSkuTree() {
  if (!SKU_LIST) return;
  SKU_LIST.innerHTML = "";

  const treeData = buildSkuTreeData();
  treeData.forEach((categoryGroup) => {
    const catDetails = document.createElement("details");
    catDetails.className = "sku-group";
    if (SESSION_BRAND === "bigtex") {
      catDetails.open = true;
    }

    const catSummary = document.createElement("summary");
    catSummary.className = "sku-group-summary";
    const catLeft = document.createElement("span");
    catLeft.className = "sku-group-left";
    const catCaret = document.createElement("span");
    catCaret.className = "sku-caret";
    const catLabel = document.createElement("span");
    catLabel.className = "sku-group-label";
    catLabel.textContent = categoryGroup.category;
    catLeft.appendChild(catCaret);
    catLeft.appendChild(catLabel);
    const catCount = document.createElement("span");
    catCount.className = "sku-count-badge";
    catCount.textContent = String(categoryGroup.count);
    catSummary.appendChild(catLeft);
    catSummary.appendChild(catCount);
    catDetails.appendChild(catSummary);

    const modelWrap = document.createElement("div");
    modelWrap.className = "sku-model-wrap";

    categoryGroup.models.forEach((modelGroup) => {
      const modelDetails = document.createElement("details");
      modelDetails.className = "sku-model-group";

      const modelSummary = document.createElement("summary");
      modelSummary.className = "sku-model-summary";
      const modelLeft = document.createElement("span");
      modelLeft.className = "sku-model-left";
      const modelCaret = document.createElement("span");
      modelCaret.className = "sku-caret";
      const modelLabel = document.createElement("span");
      modelLabel.className = "sku-model-label";
      modelLabel.textContent = modelGroup.model;
      modelLeft.appendChild(modelCaret);
      modelLeft.appendChild(modelLabel);
      const modelCount = document.createElement("span");
      modelCount.className = "sku-count-badge";
      modelCount.textContent = String(modelGroup.count);
      modelSummary.appendChild(modelLeft);
      modelSummary.appendChild(modelCount);
      modelDetails.appendChild(modelSummary);

      const itemsWrap = document.createElement("div");
      itemsWrap.className = "sku-items";
      const itemHeader = document.createElement("div");
      itemHeader.className = "sku-item-head";
      if (SESSION_BRAND === "pj") {
        itemHeader.classList.add("pj-table");
        itemHeader.innerHTML = "<span>Item</span><span>Deck L</span><span>Deck H</span><span>Tongue</span>";
      } else {
        itemHeader.innerHTML = "<span>Item</span><span>Total Length</span>";
      }
      itemsWrap.appendChild(itemHeader);

      modelGroup.items.forEach((item) => {
        const row = document.createElement("div");
        row.className = "sku-list-item sku-pick-item";
        if (SESSION_BRAND === "pj") {
          row.classList.add("pj-table");
        }
        row.draggable = true;
        row.dataset.item = item.itemNumber;
        row.dataset.itemLabel = SESSION_BRAND === "pj" ? item.itemDisplay : item.itemNumber;
        row.dataset.search = item.search;
        row.dataset.length = SESSION_BRAND === "pj"
          ? `L ${_formatFeet(item.deckLength)} x H ${_formatFeet(item.deckHeight)}`
          : _formatFeet(item.totalLength);
        row.dataset.description = item.description || "";
        if (SESSION_BRAND === "pj") {
          const selectedTongue = _getSkuTongueSelection(item.itemNumber, item.tongueProfile);
          row.dataset.defaultTongue = _pjTongueMode(item.tongueProfile);
          row.dataset.selectedTongue = selectedTongue;
        }

        const itemId = document.createElement("span");
        itemId.className = "sku-item-id";
        itemId.textContent = SESSION_BRAND === "pj" ? item.itemDisplay : item.itemNumber;
        const itemLen = document.createElement("span");
        itemLen.className = "sku-item-length";
        itemLen.textContent = SESSION_BRAND === "pj" ? _formatFeet(item.deckLength) : _formatFeet(item.totalLength);
        const itemHeight = document.createElement("span");
        itemHeight.className = "sku-item-height";
        if (SESSION_BRAND === "pj") {
          itemHeight.textContent = _formatFeet(item.deckHeight);
        }
        const itemTongue = document.createElement("span");
        itemTongue.className = "sku-item-tongue";
        if (SESSION_BRAND === "pj") {
          const btnStandard = document.createElement("button");
          btnStandard.type = "button";
          btnStandard.className = "sku-row-tongue-btn";
          btnStandard.dataset.mode = "standard";
          btnStandard.textContent = "Std";

          const btnGn = document.createElement("button");
          btnGn.type = "button";
          btnGn.className = "sku-row-tongue-btn";
          btnGn.dataset.mode = "gooseneck";
          btnGn.textContent = "GN";

          const syncButtons = () => {
            const selected = _pjTongueMode(row.dataset.selectedTongue || row.dataset.defaultTongue || "standard");
            btnStandard.classList.toggle("active", selected === "standard");
            btnGn.classList.toggle("active", selected === "gooseneck");
          };

          [btnStandard, btnGn].forEach((btn) => {
            const stop = (evt) => {
              evt.preventDefault();
              evt.stopPropagation();
            };
            btn.addEventListener("mousedown", stop);
            btn.addEventListener("click", (evt) => {
              stop(evt);
              const selected = _pjTongueMode(btn.dataset.mode || "standard");
              row.dataset.selectedTongue = selected;
              _setSkuTongueSelection(item.itemNumber, selected);
              syncButtons();
            });
            itemTongue.appendChild(btn);
          });
          syncButtons();
        }

        row.appendChild(itemId);
        row.appendChild(itemLen);
        if (SESSION_BRAND === "pj") {
          row.appendChild(itemHeight);
          row.appendChild(itemTongue);
        }
        itemsWrap.appendChild(row);
      });

      modelDetails.appendChild(itemsWrap);
      modelWrap.appendChild(modelDetails);
    });

    catDetails.appendChild(modelWrap);
    SKU_LIST.appendChild(catDetails);
  });
}

function applySkuFilter(rawQuery) {
  const query = String(rawQuery || "").trim().toLowerCase();
  let visibleItems = 0;

  document.querySelectorAll(".sku-pick-item").forEach((item) => {
    const search = String(item.dataset.search || "").toLowerCase();
    const visible = !query || search.includes(query);
    item.style.display = visible ? "" : "none";
    if (visible) visibleItems += 1;
  });

  document.querySelectorAll(".sku-model-group").forEach((modelGroup) => {
    const rows = Array.from(modelGroup.querySelectorAll(".sku-pick-item"));
    const visibleRowCount = rows.filter((row) => row.style.display !== "none").length;
    const visible = visibleRowCount > 0;
    modelGroup.style.display = visible ? "" : "none";
    const head = modelGroup.querySelector(".sku-item-head");
    if (head) {
      head.style.display = visible ? "" : "none";
    }
    if (query && visible) {
      modelGroup.open = true;
    }
  });

  document.querySelectorAll(".sku-group").forEach((categoryGroup) => {
    const modelWrap = categoryGroup.querySelector(".sku-model-wrap");
    const modelGroups = modelWrap ? Array.from(modelWrap.children).filter((el) => el.classList.contains("sku-model-group")) : [];
    const visibleModelCount = modelGroups.filter((group) => group.style.display !== "none").length;
    const visible = visibleModelCount > 0;
    categoryGroup.style.display = visible ? "" : "none";
    if (query && visible) {
      categoryGroup.open = true;
    }
  });

  if (!query) {
    applySkuHierarchyMode();
  }

  if (SKU_EMPTY) {
    SKU_EMPTY.classList.toggle("hidden", visibleItems > 0);
  }
}

if (SKU_FILTER) {
  SKU_FILTER.addEventListener("input", function () {
    applySkuFilter(this.value);
  });
}

function initInventoryUploadControls() {
  const uploadBtn = document.getElementById("inv-upload-btn");
  const uploadInput = document.getElementById("inv-upload-input");
  if (!uploadBtn || !uploadInput) return;
  if (uploadBtn.dataset.bound === "1") return;
  uploadBtn.dataset.bound = "1";

  uploadBtn.addEventListener("click", () => {
    if (uploadBtn.disabled) return;
    uploadInput.click();
  });

  uploadInput.addEventListener("change", async () => {
    const file = uploadInput.files && uploadInput.files.length ? uploadInput.files[0] : null;
    if (!file) return;
    try {
      uploadBtn.disabled = true;
      const formData = new FormData();
      formData.append("orders_file", file);
      formData.append("sheet_name", "All.Orders.Quick");

      const resp = await fetch(INVENTORY_UPLOAD_ENDPOINT, {
        method: "POST",
        body: formData,
      });
      let data = {};
      try {
        data = await resp.json();
      } catch (e) {
        data = {};
      }
      if (!resp.ok || !data.ok) {
        throw new Error(data.error || "Upload failed");
      }

      const result = data.import_result || {};
      showToast(
        `Inventory upload complete: ${result.valid_rows || 0} valid rows, ${result.distinct_items || 0} SKUs.`,
        "success",
      );
      await refreshBuilderFragments();
    } catch (e) {
      showToast(`Inventory upload failed: ${e.message}`, "error");
    } finally {
      uploadBtn.disabled = false;
      uploadInput.value = "";
    }
  });
}

function openPicker(zone, seq, targetPosId, preselectedItem, preselectedTongueMode) {
  pendingZone = zone;
  pendingSeq = seq;
  pendingTarget = targetPosId;
  pendingItem = preselectedItem || null;
  pendingTongueMode = preselectedTongueMode || null;

  const modal = document.getElementById("zone-modal");
  const opts = document.getElementById("modal-options");
  const title = document.getElementById("modal-title");
  opts.innerHTML = "";

  if (pendingItem && pendingZone) {
    const item = pendingItem;
    const zoneSelected = pendingZone;
    const target = pendingTarget;
    const insertIndex = pendingSeq;
    const selectedTongueMode = pendingTongueMode;
    closeModal();
    addUnit(item, zoneSelected, target, insertIndex, selectedTongueMode);
    return;
  }

  if (pendingItem) {
    const item = pendingItem;
    title.textContent = `Add ${pendingItem} to which zone?`;
    ZONES.forEach((z) => {
      const btn = document.createElement("button");
      btn.className = "btn btn-secondary";
      btn.textContent = ZONE_LABELS[z] || z;
      btn.onclick = () => {
        closeModal();
        addUnit(item, z, null, null, pendingTongueMode);
      };
      opts.appendChild(btn);
    });
  } else if (pendingZone) {
    const zoneSelected = pendingZone;
    const target = pendingTarget;
    title.textContent = `Choose unit to add to ${ZONE_LABELS[pendingZone] || pendingZone}`;
    const list = document.createElement("div");
    list.style = "max-height:260px;overflow-y:auto;margin-top:4px;border:1px solid var(--border-default);border-radius:var(--radius-md)";
    document.querySelectorAll(".sku-pick-item").forEach((el) => {
      if (el.style.display === "none") return;
      const row = document.createElement("div");
      row.style = "display:flex;align-items:center;justify-content:space-between;padding:6px 10px;border-bottom:1px solid var(--border-subtle);cursor:pointer;gap:8px";
      const tongueLabel = SESSION_BRAND === "pj"
        ? (_pjTongueMode(el.dataset.selectedTongue || el.dataset.defaultTongue || "standard") === "gooseneck" ? "GN" : "STD")
        : "";
      row.innerHTML = `<div style="font-family:var(--font-mono);font-size:12px;font-weight:600">${el.dataset.itemLabel || el.dataset.item}</div><div style="font-family:var(--font-mono);font-size:11px;color:var(--text-secondary)">${el.dataset.length || ""}${tongueLabel ? ` | ${tongueLabel}` : ""}</div>`;
      row.onclick = () => {
        const insertIndex = pendingSeq;
        const selectedTongueMode = _pjTongueMode(el.dataset.selectedTongue || el.dataset.defaultTongue || "standard");
        closeModal();
        addUnit(el.dataset.item, zoneSelected, target, insertIndex, selectedTongueMode);
      };
      row.onmouseover = () => {
        row.style.background = "var(--bg-secondary)";
      };
      row.onmouseout = () => {
        row.style.background = "";
      };
      list.appendChild(row);
    });
    opts.appendChild(list);
  } else {
    title.textContent = "Add to which zone?";
    ZONES.forEach((z) => {
      const btn = document.createElement("button");
      btn.className = "btn btn-secondary";
      btn.textContent = ZONE_LABELS[z] || z;
      btn.onclick = () => {
        pendingZone = z;
        openPicker(z, null, null, null);
      };
      opts.appendChild(btn);
    });
  }

  modal.classList.add("open");
}

function closeModal() {
  document.getElementById("zone-modal").classList.remove("open");
  pendingItem = null;
  pendingTongueMode = null;
  pendingZone = null;
  pendingSeq = null;
  pendingTarget = null;
}
