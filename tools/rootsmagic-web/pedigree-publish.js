"use strict";

const publishButton = document.querySelector("#pedigree-publish");
const publishStatus = document.querySelector("#pedigree-publish-status");
const publishDialog = document.querySelector("#publish-preview");
const previewStatus = document.querySelector("#publish-preview-status");
const previewContent = document.querySelector("#publish-preview-plan");
const previewConfirm = document.querySelector("#publish-confirm");
const previewSnapshot = document.querySelector("#publish-preview-snapshot");
const previewRecords = document.querySelector("#publish-preview-records");
const previewCounts = document.querySelector("#publish-preview-counts");
const previewMedia = document.querySelector("#publish-preview-media");
let previewRequest;
let publishPlan;

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
  previewCounts.textContent = `${preview.new_media.length} new · ${preview.updated_media.length} updated · ${preview.reused} reusable · ${preview.unavailable.length} unavailable`;
  previewMedia.replaceChildren();
  if (preview.new_media.length) previewMedia.append(mediaList("New media", preview.new_media));
  if (preview.updated_media.length) previewMedia.append(mediaList("Updated media", preview.updated_media));
  if (preview.unavailable.length) previewMedia.append(mediaList("Unavailable media", preview.unavailable, true));
  previewContent.hidden = false;
  previewConfirm.disabled = Boolean(preview.unavailable.length);
  previewStatus.textContent = preview.unavailable.length ? "Resolve the unavailable media, then open a new preview." : "";
  publishPlan = preview;
}

publishButton.addEventListener("click", async () => {
  const request = new AbortController();
  previewRequest = request;
  publishPlan = undefined;
  previewContent.hidden = true;
  previewConfirm.disabled = true;
  previewStatus.textContent = "Comparing the saved tree and media with the VM…";
  publishDialog.showModal();
  try {
    const preview = await requestJSON(`${API}/publish/preview`, {
      method: "POST", signal: request.signal, headers: publishHeaders(),
    });
    if (!request.signal.aborted) showPublishPreview(preview);
  } catch (error) {
    if (!request.signal.aborted) previewStatus.textContent = error instanceof PedigreeRequestError
      ? error.message : "Could not compare the tree and media. Close this preview and try again.";
  }
});

previewConfirm.addEventListener("click", async () => {
  if (!publishPlan || publishPlan.unavailable.length || publishButton.disabled) return;
  previewConfirm.disabled = true;
  publishButton.disabled = true;
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
    publishButton.disabled = false;
    if (!publishDialog.open) publishButton.focus();
  }
});

publishDialog.addEventListener("close", () => {
  previewRequest?.abort();
  if (!publishButton.disabled) publishButton.focus();
});
