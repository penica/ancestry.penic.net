"use strict";

const shareDialog = document.querySelector("#share-chart-dialog");
const shareStatus = document.querySelector("#share-chart-status");
const shareResult = document.querySelector("#share-chart-result");
const shareLink = document.querySelector("#share-chart-link");
const shareCopy = document.querySelector("#share-chart-copy");
let shareRequest;

shareButton.addEventListener("click", async () => {
  if (!chartData || viewport.getAttribute("aria-busy") === "true") return;
  const request = new AbortController();
  shareRequest = request;
  shareResult.hidden = true;
  shareStatus.textContent = "Creating chart link…";
  shareDialog.showModal();
  try {
    const result = await requestJSON(`${API}/share`, {
      method: "POST",
      signal: request.signal,
      headers: { "Content-Type": "application/json", "X-CSRF-Token": commentsToken },
      body: JSON.stringify({ person_id: chartData.root.person.id, view: captureChartView() }),
    });
    if (request.signal.aborted) return;
    shareLink.value = new URL(result.path, location.origin).href;
    shareResult.hidden = false;
    shareStatus.textContent = "";
  } catch (error) {
    if (!request.signal.aborted) shareStatus.textContent = error instanceof PedigreeRequestError
      ? error.message : "The chart link could not be created. Close this dialog and try again.";
  }
});

shareCopy.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(shareLink.value);
    shareStatus.textContent = "Link copied.";
  } catch {
    shareLink.focus();
    shareLink.select();
    shareStatus.textContent = "Copy the selected link.";
  }
});

shareDialog.addEventListener("close", () => {
  shareRequest?.abort();
  shareButton.focus();
});
