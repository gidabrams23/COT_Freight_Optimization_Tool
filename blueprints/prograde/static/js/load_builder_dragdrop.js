function initSkuDragAndDrop() {
  const skuItems = document.querySelectorAll(".sku-pick-item");
  const inventoryPickItems = document.querySelectorAll(".inventory-pick-item");
  const unitCards = document.querySelectorAll(".unit-card[data-pos-id]");
  const stackHandles = document.querySelectorAll("[data-stack-handle]");
  const deckTargets = document.querySelectorAll("[data-drop-zone]");
  const stackTargets = document.querySelectorAll("[data-stack-drop]");
  const columnSlots = document.querySelectorAll("[data-column-slot]");
  const cardDropTargets = document.querySelectorAll(".unit-card[data-pos-id]");
  let draggedUnitId = null;
  let draggedStack = null;

  const bindOnce = (el, flagName) => {
    if (!el || el.dataset[flagName] === "1") return false;
    el.dataset[flagName] = "1";
    return true;
  };

  const clearHighlights = () => {
    document.querySelectorAll(".drop-target-active").forEach((el) => el.classList.remove("drop-target-active"));
  };

  const parseStackPayload = (raw) => {
    if (!raw) return null;
    const parts = String(raw).split("|");
    if (parts.length !== 2) return null;
    const seq = Number(parts[1]);
    if (!Number.isInteger(seq)) return null;
    return { zone: parts[0], sequence: seq };
  };

  const getPayload = (evt) => {
    const transfer = evt.dataTransfer || null;
    const stackRaw = transfer ? transfer.getData("application/x-prograde-stack") : "";
    const unitRaw = transfer ? transfer.getData("application/x-prograde-unit") : "";
    const skuRaw = transfer ? transfer.getData("application/x-prograde-sku") : "";
    const skuModeRaw = transfer ? transfer.getData("application/x-prograde-sku-mode") : "";
    const plainRaw = transfer ? transfer.getData("text/plain") : "";

    if (stackRaw) {
      const parsed = parseStackPayload(stackRaw);
      if (parsed) return { type: "stack", ...parsed };
    }
    if (draggedStack) {
      return { type: "stack", ...draggedStack };
    }

    const unitId = unitRaw || draggedUnitId;
    if (unitId) {
      return { type: "unit", positionId: unitId };
    }

    const skuItem = skuRaw || draggedSkuItem || plainRaw;
    if (skuItem) {
      return { type: "sku", itemNumber: skuItem, tongueMode: _pjTongueMode(skuModeRaw || "standard") };
    }
    return null;
  };

  skuItems.forEach((item) => {
    if (!bindOnce(item, "dragBound")) return;
    item.addEventListener("dragstart", (evt) => {
      draggedSkuItem = item.dataset.item || null;
      const tongueMode = _pjTongueMode(item.dataset.selectedTongue || item.dataset.defaultTongue || "standard");
      activeDrag = draggedSkuItem ? { type: "sku", itemNumber: draggedSkuItem, tongueMode } : null;
      item.classList.add("dragging");
      if (evt.dataTransfer) {
        evt.dataTransfer.effectAllowed = "copy";
        evt.dataTransfer.setData("application/x-prograde-sku", draggedSkuItem || "");
        evt.dataTransfer.setData("application/x-prograde-sku-mode", tongueMode);
        evt.dataTransfer.setData("text/plain", draggedSkuItem || "");
      }
    });
    item.addEventListener("dragend", () => {
      draggedSkuItem = null;
      activeDrag = null;
      item.classList.remove("dragging");
      clearHighlights();
    });
  });

  inventoryPickItems.forEach((item) => {
    if (!bindOnce(item, "clickBound")) return;
    item.addEventListener("click", () => {
      if (item.disabled) return;
      const itemNumber = String(item.dataset.item || "").trim();
      const inventoryTongueMode = _pjTongueMode(item.dataset.pjTongueMode || "standard");
      if (!itemNumber) return;
      openPicker(null, null, null, itemNumber, inventoryTongueMode);
    });
  });

  inventoryPickItems.forEach((item) => {
    if (!bindOnce(item, "dragBound")) return;
    item.addEventListener("dragstart", (evt) => {
      if (item.disabled) {
        evt.preventDefault();
        return;
      }
      draggedSkuItem = item.dataset.item || null;
      const inventoryTongueMode = _pjTongueMode(item.dataset.pjTongueMode || "standard");
      activeDrag = draggedSkuItem ? { type: "sku", itemNumber: draggedSkuItem, tongueMode: inventoryTongueMode } : null;
      item.classList.add("dragging");
      if (evt.dataTransfer) {
        evt.dataTransfer.effectAllowed = "copy";
        evt.dataTransfer.setData("application/x-prograde-sku", draggedSkuItem || "");
        evt.dataTransfer.setData("application/x-prograde-sku-mode", inventoryTongueMode);
        evt.dataTransfer.setData("text/plain", draggedSkuItem || "");
      }
    });
    item.addEventListener("dragend", () => {
      draggedSkuItem = null;
      activeDrag = null;
      item.classList.remove("dragging");
      clearHighlights();
    });
  });

  unitCards.forEach((card) => {
    if (!bindOnce(card, "dragBound")) return;
    card.addEventListener("dragstart", (evt) => {
      draggedUnitId = card.dataset.posId || null;
      activeDrag = draggedUnitId ? { type: "unit", positionId: draggedUnitId } : null;
      card.classList.add("dragging");
      if (evt.dataTransfer) {
        evt.dataTransfer.effectAllowed = "move";
        evt.dataTransfer.setData("application/x-prograde-unit", draggedUnitId || "");
        evt.dataTransfer.setData("text/plain", draggedUnitId || "");
      }
    });
    card.addEventListener("dragend", () => {
      draggedUnitId = null;
      activeDrag = null;
      card.classList.remove("dragging");
      clearHighlights();
    });
  });

  stackHandles.forEach((handle) => {
    if (!bindOnce(handle, "dragBound")) return;
    const stackCol = handle.closest("[data-stack-col]");
    handle.addEventListener("dragstart", (evt) => {
      const zone = handle.dataset.zone || "";
      const sequence = Number(handle.dataset.seq || 0);
      if (!zone || !Number.isInteger(sequence) || sequence <= 0) {
        evt.preventDefault();
        return;
      }
      draggedStack = { zone, sequence };
      activeDrag = { type: "stack", zone, sequence };
      if (stackCol) stackCol.classList.add("stack-dragging");
      if (evt.dataTransfer) {
        evt.dataTransfer.effectAllowed = "move";
        evt.dataTransfer.setData("application/x-prograde-stack", `${zone}|${sequence}`);
      }
    });
    handle.addEventListener("dragend", () => {
      draggedStack = null;
      activeDrag = null;
      if (stackCol) stackCol.classList.remove("stack-dragging");
      clearHighlights();
    });
  });

  const bindDropTarget = (target, onDrop) => {
    if (!bindOnce(target, "dropBound")) return;
    target.addEventListener("dragenter", (evt) => {
      evt.preventDefault();
      target.classList.add("drop-target-active");
    });
    target.addEventListener("dragover", (evt) => {
      evt.preventDefault();
      const payload = getPayload(evt);
      if (evt.dataTransfer) {
        evt.dataTransfer.dropEffect = payload && payload.type === "sku" ? "copy" : "move";
      }
    });
    target.addEventListener("dragleave", (evt) => {
      if (!target.contains(evt.relatedTarget)) {
        target.classList.remove("drop-target-active");
      }
    });
    target.addEventListener("drop", async (evt) => {
      evt.preventDefault();
      evt.stopPropagation();
      target.classList.remove("drop-target-active");
      const payload = getPayload(evt);
      clearHighlights();
      if (!payload) return;
      try {
        await onDrop(payload);
      } catch (e) {
        showToast(`Drop failed: ${e.message || e}`, "error");
      }
    });
  };

  const dispatchDrop = async (target, payload) => {
    if (!payload) return;
    if (target.matches("[data-column-slot]")) {
      const zone = target.dataset.zone;
      const insertIndex = Number(target.dataset.insertIndex || 0);
      if (!zone) return;
      if (payload.type === "sku") {
        await addUnit(payload.itemNumber, zone, null, insertIndex, payload.tongueMode);
      } else if (payload.type === "unit") {
        await moveUnit(payload.positionId, zone, null, insertIndex);
      } else if (payload.type === "stack") {
        await moveStack(payload.zone, payload.sequence, zone, insertIndex);
      }
      return;
    }

    if (target.matches("[data-stack-drop]")) {
      const zone = target.dataset.zone;
      const stackTarget = target.dataset.target;
      const targetSeq = Number(target.dataset.targetSeq || 0);
      if (!zone) return;
      if (payload.type === "sku") {
        await addUnit(payload.itemNumber, zone, stackTarget || null, null, payload.tongueMode);
      } else if (payload.type === "unit") {
        await moveUnit(payload.positionId, zone, targetSeq > 0 ? targetSeq : null, null);
      }
      return;
    }

    if (target.matches(".unit-card[data-pos-id]")) {
      const zone = target.dataset.zone;
      const posId = target.dataset.posId;
      const targetSeq = Number(target.dataset.seq || 0);
      if (!zone || !posId) return;
      if (payload.type === "sku") {
        await addUnit(payload.itemNumber, zone, posId, null, payload.tongueMode);
      } else if (payload.type === "unit") {
        await moveUnit(payload.positionId, zone, targetSeq > 0 ? targetSeq : null, null);
      }
      return;
    }

    if (target.matches("[data-drop-zone]")) {
      const zone = target.dataset.dropZone;
      if (!zone) return;
      if (payload.type === "sku") {
        await addUnit(payload.itemNumber, zone, null, null, payload.tongueMode);
      } else if (payload.type === "unit") {
        await moveUnit(payload.positionId, zone, null, null);
      } else if (payload.type === "stack") {
        await moveStack(payload.zone, payload.sequence, zone, null);
      }
    }
  };

  deckTargets.forEach((deck) => {
    bindDropTarget(deck, async (payload) => {
      await dispatchDrop(deck, payload);
    });
  });

  stackTargets.forEach((stack) => {
    bindDropTarget(stack, async (payload) => {
      await dispatchDrop(stack, payload);
    });
  });

  columnSlots.forEach((slot) => {
    bindDropTarget(slot, async (payload) => {
      await dispatchDrop(slot, payload);
    });
  });

  cardDropTargets.forEach((card) => {
    bindDropTarget(card, async (payload) => {
      await dispatchDrop(card, payload);
    });
  });

  const resolveDropTarget = (node) => {
    if (!node || !node.closest) return null;
    return node.closest("[data-column-slot], [data-stack-drop], .unit-card[data-pos-id], [data-drop-zone]");
  };

  if (!window.__pgGlobalDropBound) {
    window.__pgGlobalDropBound = true;
    document.addEventListener("dragover", (evt) => {
      const target = resolveDropTarget(evt.target);
      if (!target) return;
      evt.preventDefault();
      const payload = getPayload(evt);
      if (evt.dataTransfer) {
        evt.dataTransfer.dropEffect = payload && payload.type === "sku" ? "copy" : "move";
      }
    });

    document.addEventListener("drop", async (evt) => {
      if (evt.defaultPrevented) return;
      const target = resolveDropTarget(evt.target);
      if (!target) return;
      evt.preventDefault();
      const payload = getPayload(evt);
      if (!payload) return;
      try {
        await dispatchDrop(target, payload);
      } catch (e) {
        showToast(`Drop failed: ${e.message || e}`, "error");
      }
    });
  }
}

function initSchematicHoverTooltips() {
  if (window.__pgTooltipBound) return;
  window.__pgTooltipBound = true;

  const tooltip = document.createElement("div");
  tooltip.className = "pg-hover-tooltip hidden";
  tooltip.setAttribute("aria-hidden", "true");
  document.body.appendChild(tooltip);

  let activeTarget = null;

  const escapeHtml = (value) =>
    String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");

  function setTooltipContent(text) {
    const rows = String(text || "")
      .split("|")
      .map((part) => part.trim())
      .filter(Boolean);
    tooltip.innerHTML = rows
      .map((row) => `<div class="pg-hover-tooltip-row">${escapeHtml(row)}</div>`)
      .join("");
  }

  function positionTooltip(evt) {
    if (!activeTarget) return;
    const pad = 14;
    const rect = tooltip.getBoundingClientRect();
    let left = evt.clientX + pad;
    let top = evt.clientY + pad;
    if (left + rect.width > window.innerWidth - 10) {
      left = Math.max(10, evt.clientX - rect.width - pad);
    }
    if (top + rect.height > window.innerHeight - 10) {
      top = Math.max(10, evt.clientY - rect.height - pad);
    }
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  }

  function hideTooltip() {
    activeTarget = null;
    tooltip.classList.add("hidden");
    tooltip.innerHTML = "";
  }

  document.addEventListener("mouseover", (evt) => {
    const target = evt.target && evt.target.closest ? evt.target.closest("[data-tooltip]") : null;
    if (!target) return;
    const text = target.getAttribute("data-tooltip");
    if (!text) return;
    activeTarget = target;
    setTooltipContent(text);
    tooltip.classList.remove("hidden");
  });

  document.addEventListener("mousemove", (evt) => {
    if (!activeTarget) return;
    positionTooltip(evt);
  });

  document.addEventListener("mouseout", (evt) => {
    if (!activeTarget) return;
    const leaving = evt.target && evt.target.closest ? evt.target.closest("[data-tooltip]") : null;
    if (!leaving || leaving !== activeTarget) return;
    const entering = evt.relatedTarget && evt.relatedTarget.closest ? evt.relatedTarget.closest("[data-tooltip]") : null;
    if (entering === activeTarget) return;
    hideTooltip();
  });

  document.addEventListener("scroll", hideTooltip, true);
  window.addEventListener("blur", hideTooltip);
  window.addEventListener("resize", hideTooltip);
}

document.getElementById("zone-modal").addEventListener("click", function (e) {
  if (e.target === this) closeModal();
});
initSkuHierarchyControls();
applySkuFilter(SKU_FILTER ? SKU_FILTER.value : "");
initRenderModeToggle();
initDeckScale();
initSchematicHoverTooltips();
initInventoryUploadControls();
