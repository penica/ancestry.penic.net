"use strict";

const publishButton = document.querySelector("#pedigree-publish");
const publishStatus = document.querySelector("#pedigree-publish-status");
const publishDialog = document.querySelector("#publish-preview");
const previewStatus = document.querySelector("#publish-preview-status");
const previewContent = document.querySelector("#publish-preview-plan");
const previewConfirm = document.querySelector("#publish-confirm");
const previewSnapshot = document.querySelector("#publish-preview-snapshot");
const previewRecords = document.querySelector("#publish-preview-records");
const previewIdentities = document.querySelector("#publish-preview-identities");
const previewCounts = document.querySelector("#publish-preview-counts");
const previewMedia = document.querySelector("#publish-preview-media");
const publishRollbackPanel = document.querySelector("#publish-rollback-plan");
const publishRollbackConfirm = document.querySelector("#publish-rollback-confirm");
const publishUpdatePreview = document.querySelector("#publish-update-preview");
const publishRollbackPreview = document.querySelector("#publish-rollback-preview");
let previewRequest;
let publishPlan;
let rollbackPlan;

function publicationBusy(busy) {
  publishButton.disabled = busy;
  publishUpdatePreview.disabled = busy;
  publishRollbackPreview.disabled = busy;
}

function publishHeaders() {
  return {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-RootsMagic-Publish-Token": publishButton.dataset.publishToken,
  };
}

function mediaList(label, entries, expanded = false) {
  const disclosure = element("details");
  disclosure.open = expanded;
  disclosure.append(element("summary", "", `${label} (${entries.length})`));
  const list = element("ul");
  for (const item of entries) list.append(element("li", "", item));
  disclosure.append(list);
  return disclosure;
}

function showPublishPreview(preview) {
  const snapshotStatus = { new: "First publication", changed: "Updated snapshot", same: "Already on the VM" };
  previewSnapshot.textContent = `${preview.snapshot} · ${snapshotStatus[preview.status]}`;
  const records = preview.records.map(({ record, added, updated, removed }) => (
    element("li", "", `${record} · ${added} added · ${updated} updated · ${removed} removed`)
  ));
  previewRecords.replaceChildren(...records);
  const reused = preview.identities.some(change => change.kind === "reused");
  previewIdentities.replaceChildren();
  if (preview.identities.length) {
    const entries = preview.identities.map(({ person, kind, replacement, links }) => {
      const description = { reused: "would identify a different person", removed: "was removed", renumbered: "has a new number" }[kind];
      const counts = Object.entries(links).map(([label, count]) => `${label} ${count}`);
      return [`#${person} ${description}`, replacement ? `Now #${replacement}` : "", ...counts].filter(Boolean).join(" · ");
    });
    previewIdentities.append(mediaList("Person IDs needing review", entries, true));
    previewIdentities.append(element("p", "hint", "Registers verifies each saved person link against its stable identity when opened."));
  }
  previewCounts.textContent = `${preview.new_media.length} new · ${preview.updated_media.length} updated · ${preview.reused} reusable · ${preview.unavailable.length} unavailable`;
  previewMedia.replaceChildren();
  if (preview.new_media.length) previewMedia.append(mediaList("New media", preview.new_media));
  if (preview.updated_media.length) previewMedia.append(mediaList("Updated media", preview.updated_media));
  if (preview.unavailable.length) previewMedia.append(mediaList("Unavailable media", preview.unavailable, true));
  previewContent.hidden = false;
  previewConfirm.disabled = reused || Boolean(preview.unavailable.length);
  previewStatus.textContent = reused ? "Resolve the reassigned person numbers in RootsMagic, then open a new preview."
    : preview.unavailable.length ? "Resolve the unavailable media, then open a new preview." : "";
  publishPlan = preview;
}

async function loadPublishPreview() {
  previewRequest?.abort();
  const request = new AbortController();
  previewRequest = request;
  publishPlan = undefined;
  previewContent.hidden = true;
  publishRollbackPanel.hidden = true;
  previewConfirm.disabled = true;
  previewStatus.textContent = "Comparing the saved tree and media with the VM…";
  try {
    const preview = await requestJSON(`${API}/publish/preview`, {
      method: "POST", signal: request.signal, headers: publishHeaders(),
    });
    if (!request.signal.aborted) showPublishPreview(preview);
  } catch (error) {
    if (!request.signal.aborted) previewStatus.textContent = error instanceof PedigreeRequestError
      ? error.message : "Could not compare the tree and media. Close this preview and try again.";
  }
}

publishButton.addEventListener("click", () => {
  publishDialog.showModal();
  loadPublishPreview();
});
publishUpdatePreview.addEventListener("click", loadPublishPreview);

publishRollbackPreview.addEventListener("click", async () => {
  previewRequest?.abort();
  const request = new AbortController();
  previewRequest = request;
  rollbackPlan = undefined;
  previewContent.hidden = true;
  publishRollbackPanel.hidden = true;
  publishRollbackConfirm.disabled = true;
  previewStatus.textContent = "Checking the previous tree and its NAS media…";
  try {
    const preview = await requestJSON(`${API}/publish/rollback-preview`, {
      method: "POST", signal: request.signal, headers: publishHeaders(),
    });
    if (request.signal.aborted) return;
    rollbackPlan = preview.plan;
    document.querySelector("#publish-rollback-snapshot").textContent = `${preview.snapshot} · before ${preview.publication} · SHA-256 ${preview.restore}`;
    const entries = preview.media.map(media => `${media.path} · ${media.after ? "Restore previous file" : "Remove published copy"}`);
    const result = entries.length ? mediaList("NAS media changes", entries, true) : element("p", "hint", "No NAS media changes.");
    document.querySelector("#publish-rollback-media").replaceChildren(result);
    publishRollbackPanel.hidden = false;
    publishRollbackConfirm.disabled = false;
    previewStatus.textContent = "";
  } catch (error) {
    if (!request.signal.aborted) previewStatus.textContent = error.message;
  }
});

publishRollbackConfirm.addEventListener("click", async () => {
  if (!rollbackPlan || publishButton.disabled) return;
  publicationBusy(true);
  publishRollbackConfirm.disabled = true;
  previewStatus.textContent = "Restoring the reviewed snapshot and NAS media…";
  publishStatus.textContent = previewStatus.textContent;
  try {
    const result = await requestJSON(`${API}/publish/rollback`, {
      method: "POST", headers: publishHeaders(), body: JSON.stringify({ plan: rollbackPlan }),
    });
    publishStatus.textContent = `Restored the version before ${result.publication} with ${result.media.length} NAS media changes.`;
    if (publishDialog.open) publishDialog.close();
  } catch (error) {
    previewStatus.textContent = publishStatus.textContent = error.message;
  } finally {
    publicationBusy(false);
    if (!publishDialog.open) publishButton.focus();
  }
});

previewConfirm.addEventListener("click", async () => {
  if (!publishPlan || publishPlan.unavailable.length || publishButton.disabled) return;
  previewConfirm.disabled = true;
  publicationBusy(true);
  previewStatus.textContent = "Publishing the reviewed snapshot and linked media…";
  publishStatus.textContent = previewStatus.textContent;
  try {
    const result = await requestJSON(`${API}/publish`, {
      method: "POST", headers: publishHeaders(), body: JSON.stringify({ plan: publishPlan.plan }),
    });
    const outcome = result.status === "already_published" ? "The VM already has" : "The VM accepted";
    const backup = result.backup ? ` Previous tree saved as ${result.backup}.` : "";
    publishStatus.textContent = `${outcome} ${result.snapshot}. Copied ${result.copied}, updated ${result.replaced}, and reused ${result.reused} media files.${backup}`;
    if (publishDialog.open) publishDialog.close();
  } catch (error) {
    const message = error instanceof PedigreeRequestError ? error.message : "Publishing could not be completed.";
    publishStatus.textContent = message;
    previewStatus.textContent = message;
  } finally {
    publicationBusy(false);
    if (!publishDialog.open) publishButton.focus();
  }
});

publishDialog.addEventListener("close", () => {
  previewRequest?.abort();
  if (!publishButton.disabled) publishButton.focus();
});
