"use strict";

const gapsByPerson = new Map();
const gapToggle = document.querySelector("#pedigree-show-gaps");
const gapStatus = document.querySelector("#pedigree-gap-status");
const relationshipButton = document.querySelector("#pedigree-relationship");
const relationshipDialog = document.querySelector("#relationship-dialog");
const relationshipQuery = document.querySelector("#relationship-query");
const relationshipStatus = document.querySelector("#relationship-status");
const relationshipPeople = document.querySelector("#relationship-people");
const relationshipResults = document.querySelector("#relationship-results");
const printButton = document.querySelector("#pedigree-print");

let gapRequest;
let relationshipRequest;
let printing = false;

const missingLabels = {
  father: "Father not recorded",
  mother: "Mother not recorded",
  birth_date: "Birth date missing",
  birth_place: "Birth place missing",
  death_date: "Death date missing",
  death_place: "Death place missing",
};

function appendGapFlags(card, personId) {
  const gaps = gapToggle.checked && gapsByPerson.get(personId);
  if (!gaps) return;
  const flags = element("span", "gap-flags");
  for (const missing of gaps.missing) {
    flags.append(element("span", "", missingLabels[missing]));
  }
  if (gaps.uncited_events.length) {
    const count = gaps.uncited_events.length;
    const label = element("span", "", `${count} ${count === 1 ? "event" : "events"} without citations`);
    label.title = gaps.uncited_events.map((event) => [event.kind, event.date].filter(Boolean).join(" · ")).join("\n");
    flags.append(label);
  }
  if (flags.childElementCount) card.append(flags);
}

function invalidateGaps() {
  gapRequest?.abort();
  gapRequest = null;
  gapsByPerson.clear();
  gapStatus.textContent = "";
}

function redrawWithScroll() {
  const { scrollLeft, scrollTop } = viewport;
  displayGraph(chartData, false);
  viewport.scrollTo(scrollLeft, scrollTop);
}

async function loadVisibleGaps() {
  if (!gapToggle.checked || gapRequest) return;
  const ids = [...new Set([...viewport.querySelectorAll(".person-card")]
    .map((card) => Number(card.dataset.personId)))].filter((id) => !gapsByPerson.has(id));
  if (!ids.length) return;
  const request = new AbortController();
  gapRequest = request;
  gapStatus.textContent = "Checking research gaps…";
  try {
    for (let index = 0; index < ids.length; index += 100) {
      const personIds = ids.slice(index, index + 100);
      const data = await getJSON(`${API}/research-gaps`, request.signal, { person_ids: personIds });
      if (request.signal.aborted) return;
      for (const id of personIds) gapsByPerson.set(id, null);
      for (const person of data.people) gapsByPerson.set(person.person.id, person);
    }
    gapStatus.textContent = "";
    redrawWithScroll();
    gapRequest = null;
    void loadVisibleGaps();
  } catch (error) {
    if (!request.signal.aborted) gapStatus.textContent = "Could not check gaps. Toggle Show research gaps to retry.";
  } finally {
    if (gapRequest === request) gapRequest = null;
  }
}

function closeRelationship() {
  relationshipRequest?.abort();
  if (relationshipDialog.open) relationshipDialog.close();
}

function resetPedigreeTools() {
  closeRelationship();
  invalidateGaps();
  relationshipButton.disabled = true;
  gapToggle.disabled = true;
  printButton.disabled = true;
}

function updatePedigreeTools() {
  relationshipButton.disabled = false;
  gapToggle.disabled = false;
  printButton.disabled = printing;
  void loadVisibleGaps();
}

async function searchRelationshipPeople(event) {
  event.preventDefault();
  const query = relationshipQuery.value.trim().replace(/^#(?=\d+$)/, "");
  if (!query) return;
  relationshipRequest?.abort();
  const request = new AbortController();
  relationshipRequest = request;
  relationshipPeople.replaceChildren();
  relationshipResults.replaceChildren();
  relationshipStatus.textContent = "Searching…";
  try {
    const params = new URLSearchParams({ q: query, limit: "30" });
    const result = await getJSON(`${API}/people?${params}`, request.signal);
    if (request.signal.aborted) return;
    relationshipStatus.textContent = result.people.length
      ? result.total > result.people.length ? "Showing the first 30 matches. Enter a more specific name or ID." : "Choose a starting person."
      : "No matches. Try another name or RootsMagic ID.";
    for (const person of result.people) {
      const item = element("li");
      const button = element("button", "reset relationship-choice");
      button.type = "button";
      const years = searchYears(person);
      button.textContent = [person.name, `#${person.id}`, years].filter(Boolean).join(" · ");
      button.addEventListener("click", () => void showRelationship(person.id));
      item.append(button);
      relationshipPeople.append(item);
    }
    relationshipPeople.querySelector("button")?.focus();
  } catch (error) {
    if (!request.signal.aborted) relationshipStatus.textContent = "Could not search for people. Try again.";
  }
}

async function showRelationship(fromId) {
  relationshipRequest?.abort();
  const request = new AbortController();
  relationshipRequest = request;
  relationshipResults.replaceChildren();
  relationshipStatus.textContent = "Finding recorded paths…";
  try {
    const result = await getJSON(`${API}/${selectedPerson.id}/relationship?from_id=${fromId}`, request.signal);
    if (request.signal.aborted) return;
    const count = result.paths.length;
    relationshipStatus.textContent = !count
      ? result.truncated ? "No path found within the search limit." : "No recorded path connects these people."
      : result.strategy === "same" ? "Both selections refer to the same person."
      : `${count} recorded ${count === 1 ? "path" : "paths"}${result.truncated ? " shown. The search limit was reached." : "."}`;
    result.paths.forEach((path, index) => {
      const section = element("section", "details-section");
      section.append(element("h3", "", `Path ${index + 1}`));
      const people = element("ol", "relative-list");
      path.people.forEach((person, step) => {
        const item = element("li");
        const link = personLink(person);
        const direction = path.steps[step - 1];
        link.append(element("span", "", person.name), element("span", "person-id", `#${person.id}`));
        const stepLabel = { parent: "Parent of preceding person", child: "Child of preceding person", partner: "Partner of preceding person" }[direction];
        if (stepLabel) link.append(element("span", "relative-role", stepLabel));
        item.append(link);
        people.append(item);
      });
      section.append(people);
      relationshipResults.append(section);
    });
  } catch (error) {
    if (!request.signal.aborted) relationshipStatus.textContent = "Could not load the relationship. Choose a person to retry.";
  }
}

function externalLink(url, label) {
  const link = element("a", "", label);
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

function appendEvidence(event, evidence) {
  const sources = new Map(evidence.sources.map((source) => [source.url, source]));
  const media = new Map(evidence.media.map((item) => [item.id, item]));
  if (!sources.size && !media.size) return;
  const group = element("div", "event-evidence");
  if (sources.size) {
    group.append(element("h5", "", "Source links"));
    const list = element("ul");
    for (const source of sources.values()) {
      const item = element("li");
      item.append(externalLink(source.url, source.label || "Open source"));
      list.append(item);
    }
    group.append(list);
  }
  if (media.size) {
    group.append(element("h5", "", "Linked media"));
    const list = element("ul");
    for (const file of media.values()) {
      const item = element("li");
      const label = file.caption || file.filename || "Linked file";
      if (file.available) {
        const href = treeURL(`${API}/media/${file.id}`);
        if (file.is_image) {
          const link = element("a", "", label);
          link.href = href;
          link.dataset.imagePreview = "";
          link.setAttribute("aria-haspopup", "dialog");
          item.append(link);
        } else {
          item.append(externalLink(href, label));
        }
      } else if (file.url) item.append(externalLink(file.url, label));
      else item.textContent = `${label} (file unavailable)`;
      list.append(item);
    }
    group.append(list);
  }
  event.append(group);
}

async function loadPersonEvidence(personId, signal) {
  try {
    const result = await getJSON(`${API}/${personId}/evidence`, signal);
    if (signal.aborted) return;
    for (const event of detailsBody.querySelectorAll(".details-event")) {
      const evidence = result.events[event.dataset.eventId];
      if (evidence) appendEvidence(event, evidence);
    }
  } catch (error) {
    if (!signal.aborted) {
      detailsBody.querySelector("#details-timeline")?.after(
        element("p", "details-empty", "Could not load source links and linked media. Refresh to try again."),
      );
    }
  }
}

async function downloadPedigree() {
  if (!chartData || printing) return;
  printing = true;
  printButton.disabled = true;
  treeStatus.textContent = "Preparing the A0 roll PDF…";
  const snapshot = chartData;
  const id = snapshot.root.person.id;
  try {
    const response = await fetch(treeURL(`${API}/print`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chart: { ...snapshot, root: visibleRoot(snapshot.root), siblings: siblingsToggle.checked ? snapshot.siblings : [] },
        show_children: document.querySelector("#pedigree-show-children").checked,
        show_partners: branchExpansions.has(String(id)),
        partner_roots: visiblePartnerRoots(snapshot).filter((root) => !collapsedPartners.has(root.key)),
      }),
    });
    if (!response.ok) {
      const body = await response.json();
      throw new Error(typeof body.detail === "string" ? body.detail : "Could not create the PDF.");
    }
    const url = URL.createObjectURL(await response.blob());
    const link = element("a");
    link.href = url;
    link.download = `${TREE ? `${TREE}-` : ""}pedigree-${id}-a0-roll.pdf`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
    if (chartData === snapshot) treeStatus.textContent = "A0 roll PDF downloaded.";
  } catch (error) {
    if (chartData === snapshot) {
      treeStatus.textContent = error instanceof Error ? error.message : "Could not create the PDF.";
    }
  } finally {
    printing = false;
    printButton.disabled = !chartData;
  }
}

function initPedigreeTools() {
  gapToggle.addEventListener("change", () => {
    invalidateGaps();
    if (chartData) redrawWithScroll();
    saveChartView();
  });
  relationshipButton.addEventListener("click", () => {
    if (!selectedPerson) return;
    relationshipQuery.value = "";
    relationshipPeople.replaceChildren();
    relationshipResults.replaceChildren();
    relationshipStatus.textContent = "";
    document.querySelector("#relationship-target").textContent = `Find a recorded path to ${selectedPerson.name} (#${selectedPerson.id}).`;
    relationshipDialog.showModal();
    relationshipQuery.focus();
  });
  relationshipDialog.addEventListener("close", () => relationshipRequest?.abort());
  document.querySelector("#relationship-search").addEventListener("submit", (event) => void searchRelationshipPeople(event));
  printButton.addEventListener("click", () => void downloadPedigree());
}
