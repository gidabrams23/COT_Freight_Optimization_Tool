
const SESSION_ID = "fe9cdae4-1d5a-4c36-ad73-987a2e9d5d0d";
const ZONES = ["stack_1", "stack_2", "stack_3"];
const ZONE_LABELS = {"lower_deck": "Lower Deck (41\u0027)", "stack_1": "Stack 1 \u2014 Rear", "stack_2": "Stack 2 \u2014 Middle", "stack_3": "Stack 3 \u2014 Front", "upper_deck": "Upper Deck (12\u0027)"};
const SESSION_API_BASE = "/prograde/api/session/__SESSION_ID__/check"
  .replace("__SESSION_ID__", SESSION_ID)
  .replace(/\/check$/, "");

let pendingItem = null;
let pendingZone = null;
let pendingSeq = null;
let pendingTarget = null;
let draggedSkuItem = null;
let activeDrag = null;

document.getElementById("sku-filter").addEventListener("input", function () {
  const q = this.value.toLowerCase();
  document.querySelectorAll(".sku-pick-item").forEach((el) => {
    el.style.display = el.dataset.search.toLowerCase().includes(q) ? "" : "none";
  });
});

function openPicker(zone, seq, targetPosId, preselectedItem) {
  pendingZone = zone;
  pendingSeq = seq;
  pendingTarget = targetPosId;
  pendingItem = preselectedItem || null;

  const modal = document.getElementById("zone-modal");
  const opts = document.getElementById("modal-options");
  const title = document.getElementById("modal-title");
  opts.innerHTML = "";

  if (pendingItem && pendingZone) {
    const item = pendingItem;
    const zoneSelected = pendingZone;
    const target = pendingTarget;
    const insertIndex = pendingSeq;
    closeModal();
    addUnit(item, zoneSelected, target, insertIndex);
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
        addUnit(item, z, null, null);
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
      row.innerHTML = `<div><div style="font-family:var(--font-mono);font-size:12px;font-weight:600">${el.dataset.item}</div></div>`;
      row.onclick = () => {
        const insertIndex = pendingSeq;
        closeModal();
        addUnit(el.dataset.item, zoneSelected, target, insertIndex);
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
  pendingZone = null;
  pendingSeq = null;
  pendingTarget = null;
}

async function postSession(path, body) {
  const resp = await fetch(SESSION_API_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  let data = {};
  try {
    data = await resp.json();
  } catch (e) {
    data = {};
  }
  if (!resp.ok || !data.ok) {
    throw new Error(data.error || "Request failed");
  }
  return data;
}

async function addUnit(itemNumber, zone, stackOnPosId, insertIndex) {
  try {
    const body = { item_number: itemNumber, deck_zone: zone };
    if (stackOnPosId) body.stack_on = stackOnPosId;
    if (insertIndex !== undefined && insertIndex !== null && insertIndex !== "") {
      body.insert_index = Number(insertIndex);
    }
    await postSession("/add", body);
    location.reload();
  } catch (e) {
    showToast(`Failed to add unit: ${e.message}`, "error");
  }
}

async function moveUnit(positionId, toZone, toSequence, insertIndex) {
  try {
    const body = { position_id: positionId, to_zone: toZone };
    if (toSequence !== undefined && toSequence !== null && toSequence !== "") {
      body.to_sequence = Number(toSequence);
    }
    if (insertIndex !== undefined && insertIndex !== null && insertIndex !== "") {
      body.insert_index = Number(insertIndex);
    }
    await postSession("/position/move", body);
    location.reload();
  } catch (e) {
    showToast(`Failed to move unit: ${e.message}`, "error");
  }
}

async function moveStack(fromZone, sequence, toZone, insertIndex) {
  try {
    const body = {
      from_zone: fromZone,
      sequence: Number(sequence),
      to_zone: toZone,
    };
    if (insertIndex !== undefined && insertIndex !== null && insertIndex !== "") {
      body.insert_index = Number(insertIndex);
    }
    await postSession("/column/move", body);
    location.reload();
  } catch (e) {
    showToast(`Failed to move stack: ${e.message}`, "error");
  }
}

async function removeUnit(positionId) {
  try {
    await postSession("/remove", { position_id: positionId });
    location.reload();
  } catch (e) {
    showToast(`Failed to remove unit: ${e.message}`, "error");
  }
}

async function toggleAxleDrop(positionId, checkbox) {
  try {
    const data = await postSession("/toggle_axle_drop", { position_id: positionId });
    const lbl = checkbox.nextElementSibling;
    if (data.gn_axle_dropped) {
      lbl.classList.add("axle-drop-active");
      lbl.textContent = "axle drop on";
    } else {
      lbl.classList.remove("axle-drop-active");
      lbl.textContent = "axle drop";
    }
    await refreshViolations();
  } catch (e) {
    checkbox.checked = !checkbox.checked;
    showToast(`Toggle failed: ${e.message}`, "error");
  }
}

async function acknowledgeViolation(ruleCode, action, btn) {
  try {
    await postSession("/acknowledge", { rule_code: ruleCode, action });
    const item = document.getElementById("viol-" + ruleCode.replace(/_/g, "-"));
    if (!item) {
      location.reload();
      return;
    }
    if (action === "add") {
      item.classList.add("acknowledged");
      btn.className = "violation-unack-btn";
      btn.textContent = "Un-acknowledge";
      btn.onclick = () => acknowledgeViolation(ruleCode, "remove", btn);
    } else {
      item.classList.remove("acknowledged");
      btn.className = "violation-ack-btn";
      btn.textContent = "Acknowledge";
      btn.onclick = () => acknowledgeViolation(ruleCode, "add", btn);
    }
    await refreshViolations();
  } catch (e) {
    showToast(`Acknowledge failed: ${e.message}`, "error");
  }
}

async function resetSession() {
  if (!window.confirm("Clear all positions and acknowledgements in this session?")) {
    return;
  }
  try {
    await postSession("/reset", {});
    location.reload();
  } catch (e) {
    showToast(`Reset failed: ${e.message}`, "error");
  }
}

async function refreshViolations() {
  const resp = await fetch(SESSION_API_BASE + "/check");
  if (!resp.ok) return;
  showToast("Constraint check updated", "success");
}

function initSkuDragAndDrop() {
  const skuItems = document.querySelectorAll(".sku-pick-item");
  const unitCards = document.querySelectorAll(".unit-card[data-pos-id]");
  const stackHandles = document.querySelectorAll("[data-stack-handle]");
  const deckTargets = document.querySelectorAll("[data-drop-zone]");
  const stackTargets = document.querySelectorAll("[data-stack-drop]");
  const columnSlots = document.querySelectorAll("[data-column-slot]");
  const cardDropTargets = document.querySelectorAll(".unit-card[data-pos-id]");
  let draggedUnitId = null;
  let draggedStack = null;

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
    if (activeDrag && activeDrag.type) {
      return activeDrag;
    }
    const transfer = evt.dataTransfer || null;
    const stackRaw = transfer ? transfer.getData("application/x-prograde-stack") : "";
    const unitRaw = transfer ? transfer.getData("application/x-prograde-unit") : "";
    const skuRaw = transfer ? transfer.getData("application/x-prograde-sku") : "";
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
      return { type: "sku", itemNumber: skuItem };
    }
    return null;
  };

  skuItems.forEach((item) => {
    item.addEventListener("dragstart", (evt) => {
      draggedSkuItem = item.dataset.item || null;
      activeDrag = draggedSkuItem ? { type: "sku", itemNumber: draggedSkuItem } : null;
      item.classList.add("dragging");
      if (evt.dataTransfer) {
        evt.dataTransfer.effectAllowed = "copy";
        evt.dataTransfer.setData("application/x-prograde-sku", draggedSkuItem || "");
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
    target.addEventListener("dragenter", (evt) => {
      evt.preventDefault();
      target.classList.add("drop-target-active");
    });
    target.addEventListener("dragover", (evt) => {
      evt.preventDefault();
      if (evt.dataTransfer) evt.dataTransfer.dropEffect = "move";
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
        await addUnit(payload.itemNumber, zone, null, insertIndex);
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
        await addUnit(payload.itemNumber, zone, stackTarget || null, null);
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
        await addUnit(payload.itemNumber, zone, posId, null);
      } else if (payload.type === "unit") {
        await moveUnit(payload.positionId, zone, targetSeq > 0 ? targetSeq : null, null);
      }
      return;
    }

    if (target.matches("[data-drop-zone]")) {
      const zone = target.dataset.dropZone;
      if (!zone) return;
      if (payload.type === "sku") {
        await addUnit(payload.itemNumber, zone, null, null);
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

  // Allow dropping directly on an existing unit card to append/move into that stack.
  cardDropTargets.forEach((card) => {
    bindDropTarget(card, async (payload) => {
      await dispatchDrop(card, payload);
    });
  });

  // Global fallback: capture drops on child nodes nested inside schematic targets.
  const resolveDropTarget = (node) => {
    if (!node || !node.closest) return null;
    return node.closest("[data-column-slot], [data-stack-drop], .unit-card[data-pos-id], [data-drop-zone]");
  };

  document.addEventListener("dragover", (evt) => {
    const target = resolveDropTarget(evt.target);
    if (!target) return;
    evt.preventDefault();
    if (evt.dataTransfer) evt.dataTransfer.dropEffect = "move";
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
initSchematicHoverTooltips();
initSkuDragAndDrop();
