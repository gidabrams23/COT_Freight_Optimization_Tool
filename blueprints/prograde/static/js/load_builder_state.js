function getRenderModeInputs() {
  return Array.from(document.querySelectorAll('input[name="render-mode"]'));
}

function getSchematicRoot() {
  return document.querySelector(".prograde-schematic");
}

function initRenderModeToggle() {
  const schematicRoot = getSchematicRoot();
  const renderModeInputs = getRenderModeInputs();
  if (SESSION_BRAND !== "pj" || !schematicRoot || !renderModeInputs.length) {
    return;
  }

  const applyRenderMode = (modeRaw) => {
    const mode = String(modeRaw || "").toLowerCase() === "standard" ? "standard" : "advanced";
    schematicRoot.classList.remove("render-mode-standard", "render-mode-advanced");
    schematicRoot.classList.add(mode === "standard" ? "render-mode-standard" : "render-mode-advanced");
    renderModeInputs.forEach((input) => {
      input.checked = input.value === mode;
    });
    try {
      localStorage.setItem(RENDER_MODE_STORAGE_KEY, mode);
    } catch (e) {}
    window.requestAnimationFrame(initStepdeckMeasureRow);
  };

  let initialMode = "advanced";
  try {
    const savedMode = localStorage.getItem(RENDER_MODE_STORAGE_KEY);
    if (savedMode === "standard" || savedMode === "advanced") {
      initialMode = savedMode;
    }
  } catch (e) {}
  applyRenderMode(initialMode);

  renderModeInputs.forEach((input) => {
    input.addEventListener("change", () => {
      if (!input.checked) return;
      applyRenderMode(input.value);
    });
  });
}

function initDeckScale() {
  const schematicRoot = getSchematicRoot();
  if (!schematicRoot) return;

  const applyScale = () => {
    const zonePanels = Array.from(schematicRoot.querySelectorAll(".trailer-deck[data-drop-zone]"));
    if (!zonePanels.length) return;

    const pxCandidates = [];
    const heightPxCandidates = [];
    zonePanels.forEach((zonePanel) => {
      const track = zonePanel.querySelector(".zone-columns-scroll");
      if (!track) return;
      const trackWidth = track.getBoundingClientRect().width;
      if (!Number.isFinite(trackWidth) || trackWidth <= 0) return;
      const slotWidthTotal = Array.from(track.querySelectorAll(".column-drop-slot")).reduce((sum, slot) => {
        const slotWidth = slot.getBoundingClientRect().width;
        return sum + (Number.isFinite(slotWidth) ? slotWidth : 0);
      }, 0);
      const usableTrackWidth = Math.max(trackWidth - slotWidthTotal, trackWidth * 0.55);

      const capRaw = Number(zonePanel.dataset.zoneCap || 0);
      const usedRaw = Number(zonePanel.dataset.zoneUsed || 0);
      const capFeet = Number.isFinite(capRaw) && capRaw > 0 ? capRaw : 0;
      const usedFeet = Number.isFinite(usedRaw) && usedRaw > 0 ? usedRaw : 0;
      const neededFeet = capFeet > 0 ? capFeet : Math.max(usedFeet, 1);
      pxCandidates.push(usableTrackWidth / neededFeet);

      const stage = zonePanel.querySelector(".deck-stage");
      if (!stage) return;
      const stageHeight = stage.getBoundingClientRect().height;
      if (!Number.isFinite(stageHeight) || stageHeight <= 0) return;
      const heightCapRaw = Number(zonePanel.dataset.zoneHeightCap || 0);
      const heightCapFeet = Number.isFinite(heightCapRaw) && heightCapRaw > 0 ? heightCapRaw : 0;
      const neededHeightFeet = heightCapFeet > 0 ? heightCapFeet : 4;
      const usableStageHeight = Math.max(stageHeight - 6, 40);
      heightPxCandidates.push(usableStageHeight / neededHeightFeet);
    });

    if (pxCandidates.length) {
      const pxPerFootRaw = Math.min(...pxCandidates);
      const pxPerFoot = Math.max(1.4, pxPerFootRaw);
      schematicRoot.style.setProperty("--deck-ft-px", `${pxPerFoot}px`);
    }

    if (heightPxCandidates.length) {
      const heightPxPerFootRaw = Math.min(...heightPxCandidates);
      const heightPxPerFoot = Math.max(12.0, Math.min(heightPxPerFootRaw, 22.0));
      schematicRoot.style.setProperty("--height-ft-px", `${heightPxPerFoot}px`);
    }

    window.requestAnimationFrame(initStepdeckMeasureRow);
  };

  applyScale();
  if (window.__pgDeckScaleHandler) {
    window.removeEventListener("resize", window.__pgDeckScaleHandler);
  }
  window.__pgDeckScaleHandler = applyScale;
  window.addEventListener("resize", window.__pgDeckScaleHandler);
}

function initStepdeckMeasureRow() {
  const schematicRoot = getSchematicRoot();
  if (!schematicRoot) return;
  const deckRow = schematicRoot.querySelector(".trailer-deck-row.step-deck-frame");
  const measureTrack = schematicRoot.querySelector(".stepdeck-measure-track-live");
  if (!deckRow || !measureTrack) return;

  const totalCapRaw = Number(measureTrack.dataset.totalCap || 0);
  const totalCap = Number.isFinite(totalCapRaw) && totalCapRaw > 0 ? totalCapRaw : 53.0;
  const deckRect = deckRow.getBoundingClientRect();
  const trackRect = measureTrack.getBoundingClientRect();
  const trackWidth = trackRect.width;
  if (!Number.isFinite(trackWidth) || trackWidth <= 0) return;

  const cols = Array.from(deckRow.querySelectorAll(".position-column[data-stack-col]"));
  const stackEntries = cols.map((col) => {
    const rect = col.getBoundingClientRect();
    const leftPx = rect.left - deckRect.left;
    const widthPx = rect.width;
    const feetRaw = parseFloat(getComputedStyle(col).getPropertyValue("--deck-span"));
    const feet = Number.isFinite(feetRaw) && feetRaw > 0 ? feetRaw : 0;
    return { leftPx, widthPx, feet };
  }).filter((entry) => entry.widthPx > 1 && entry.feet > 0.02)
    .sort((a, b) => a.leftPx - b.leftPx);

  measureTrack.innerHTML = "";
  if (!stackEntries.length) {
    const empty = document.createElement("div");
    empty.className = "stepdeck-live-segment is-remaining";
    empty.style.left = "0px";
    empty.style.width = `${trackWidth}px`;
    empty.innerHTML = `<span class="stepdeck-live-segment-label">${totalCap.toFixed(1)} ft remaining</span>`;
    measureTrack.appendChild(empty);
    return;
  }

  let totalStackFeet = 0;
  stackEntries.forEach((entry) => { totalStackFeet += entry.feet; });
  const remainingFeet = Math.max(totalCap - totalStackFeet, 0);

  stackEntries.forEach((entry, idx) => {
    const seg = document.createElement("div");
    seg.className = "stepdeck-live-segment is-stack";
    const left = Math.max(0, Math.min(entry.leftPx, trackWidth));
    const width = Math.max(1, Math.min(entry.widthPx, trackWidth - left));
    seg.style.left = `${left}px`;
    seg.style.width = `${width}px`;
    seg.innerHTML = `<span class="stepdeck-live-segment-label">Stack ${idx + 1}: ${entry.feet.toFixed(1)} ft</span>`;
    measureTrack.appendChild(seg);
  });

  if (remainingFeet <= 0.02) return;

  let bestGap = null;
  for (let i = 0; i < stackEntries.length - 1; i += 1) {
    const gapLeft = stackEntries[i].leftPx + stackEntries[i].widthPx;
    const gapRight = stackEntries[i + 1].leftPx;
    const gapWidth = gapRight - gapLeft;
    if (gapWidth <= 1) continue;
    if (!bestGap || gapWidth > bestGap.widthPx) {
      bestGap = { leftPx: gapLeft, widthPx: gapWidth };
    }
  }

  if (!bestGap) {
    const leftGapWidth = stackEntries[0].leftPx;
    const rightGapLeft = stackEntries[stackEntries.length - 1].leftPx + stackEntries[stackEntries.length - 1].widthPx;
    const rightGapWidth = trackWidth - rightGapLeft;
    if (rightGapWidth >= leftGapWidth && rightGapWidth > 1) {
      bestGap = { leftPx: rightGapLeft, widthPx: rightGapWidth };
    } else if (leftGapWidth > 1) {
      bestGap = { leftPx: 0, widthPx: leftGapWidth };
    }
  }

  if (!bestGap) return;
  const rem = document.createElement("div");
  rem.className = "stepdeck-live-segment is-remaining";
  rem.style.left = `${Math.max(0, bestGap.leftPx)}px`;
  rem.style.width = `${Math.max(1, bestGap.widthPx)}px`;
  rem.innerHTML = `<span class="stepdeck-live-segment-label">${remainingFeet.toFixed(1)} ft remaining</span>`;
  measureTrack.appendChild(rem);
}

function _feetNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
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

function replaceNodeByIdFromDoc(id, sourceDoc) {
  const current = document.getElementById(id);
  const incoming = sourceDoc.getElementById(id);
  if (current && incoming) {
    current.replaceWith(incoming);
  }
}

async function refreshBuilderFragments() {
  const resp = await fetch(window.location.pathname + window.location.search, {
    headers: {
      "X-Requested-With": "XMLHttpRequest",
      "Cache-Control": "no-store",
    },
  });
  if (!resp.ok) {
    throw new Error("Failed to refresh load layout");
  }

  const html = await resp.text();
  const sourceDoc = new DOMParser().parseFromString(html, "text/html");

  const currentTopBar = document.getElementById("pg-top-bar");
  const incomingStale = sourceDoc.getElementById("pg-stale-banner");
  const currentStale = document.getElementById("pg-stale-banner");
  if (incomingStale && currentTopBar) {
    if (currentStale) {
      currentStale.replaceWith(incomingStale);
    } else {
      currentTopBar.parentNode.insertBefore(incomingStale, currentTopBar);
    }
  } else if (currentStale) {
    currentStale.remove();
  }

  replaceNodeByIdFromDoc("pg-top-bar", sourceDoc);
  replaceNodeByIdFromDoc("pg-canvas-column", sourceDoc);
  replaceNodeByIdFromDoc("violation-panel", sourceDoc);
  replaceNodeByIdFromDoc("pg-manifest-card", sourceDoc);
  replaceNodeByIdFromDoc("pg-inventory-card", sourceDoc);

  initRenderModeToggle();
  initDeckScale();
  initSchematicHoverTooltips();
  initSkuDragAndDrop();
  initInventoryUploadControls();
}

async function addUnit(itemNumber, zone, stackOnPosId, insertIndex, tongueMode) {
  try {
    const body = { item_number: itemNumber, deck_zone: zone };
    if (stackOnPosId) body.stack_on = stackOnPosId;
    if (insertIndex !== undefined && insertIndex !== null && insertIndex !== "") {
      body.insert_index = Number(insertIndex);
    }
    if (SESSION_BRAND === "pj") {
      body[PJ_TONGUE_PROFILE_FIELD] = _pjTongueMode(tongueMode || _getSkuTongueSelection(itemNumber, "standard"));
    }
    await postSession("/add", body);
    await refreshBuilderFragments();
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
    await refreshBuilderFragments();
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
    await refreshBuilderFragments();
  } catch (e) {
    showToast(`Failed to move stack: ${e.message}`, "error");
  }
}

async function removeUnit(positionId) {
  try {
    await postSession("/remove", { position_id: positionId });
    await refreshBuilderFragments();
  } catch (e) {
    showToast(`Failed to remove unit: ${e.message}`, "error");
  }
}

async function rotateUnit(positionId) {
  try {
    await postSession("/rotate", { position_id: positionId });
    await refreshBuilderFragments();
  } catch (e) {
    showToast(`Failed to rotate unit: ${e.message}`, "error");
  }
}

async function toggleDumpDoor(positionId) {
  try {
    await postSession("/toggle_dump_door", { position_id: positionId });
    await refreshBuilderFragments();
  } catch (e) {
    showToast(`Failed to toggle dump door: ${e.message}`, "error");
  }
}

async function toggleAxleDrop(positionId, checkbox) {
  try {
    await postSession("/toggle_axle_drop", { position_id: positionId });
    await refreshBuilderFragments();
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
      await refreshBuilderFragments();
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
    await refreshBuilderFragments();
  } catch (e) {
    showToast(`Acknowledge failed: ${e.message}`, "error");
  }
}

async function saveSession(buttonEl) {
  const btn = buttonEl && buttonEl.tagName ? buttonEl : document.getElementById("save-session-btn");
  if (btn && btn.dataset.saving === "1") {
    return;
  }
  const originalLabel = btn ? btn.textContent : "Save Session";
  try {
    if (btn) {
      btn.dataset.saving = "1";
      btn.disabled = true;
      btn.textContent = "Saving...";
    }
    await postSession("/save", {});
    await refreshBuilderFragments();
    showToast("Session saved and added to All Sessions", "success");
  } catch (e) {
    showToast(`Save failed: ${e.message}`, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.dataset.saving = "0";
      btn.textContent = originalLabel;
    }
  }
}

async function resetSession() {
  if (!window.confirm("Clear all positions and acknowledgements in this session?")) {
    return;
  }
  try {
    await postSession("/reset", {});
    await refreshBuilderFragments();
  } catch (e) {
    showToast(`Reset failed: ${e.message}`, "error");
  }
}

async function refreshViolations() {
  await refreshBuilderFragments();
}
