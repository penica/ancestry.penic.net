"use strict";

const worklistButton = document.querySelector("#pedigree-worklist");
const worklistDialog = document.querySelector("#research-worklist");
const worklistCategory = document.querySelector("#worklist-category");
const worklistGeneration = document.querySelector("#worklist-generation");
const worklistStatus = document.querySelector("#worklist-status");
const worklistResults = document.querySelector("#worklist-results");
const worklistRetry = document.querySelector("#worklist-retry");
const worklistMore = document.querySelector("#worklist-more");
const WORKLIST_PAGE_SIZE = 50;

let worklistRequest;
let worklistCards = [];
let worklistPeople = new Map();
let worklistLimit = WORKLIST_PAGE_SIZE;
let worklistReturnFocus;

function worklistGenerationName(generation) {
  return generation === 0 ? "Children" : `${generation} · ${generationName(generation - 1)}`;
}

function worklistMissing(gaps) {
  switch (worklistCategory.value) {
    case "parents":
      return gaps.missing.filter((key) => key === "father" || key === "mother");
    case "dates":
      return gaps.missing.filter((key) => key.endsWith("_date"));
    case "places":
      return gaps.missing.filter((key) => key.endsWith("_place"));
    case "citations":
      return [];
    default:
      return gaps.missing;
  }
}

function renderResearchWorklist() {
  const showCitations = ["all", "citations"].includes(worklistCategory.value);
  const generation = worklistGeneration.value;
  const matches = worklistCards.flatMap((entry) => {
    if (generation !== "all" && String(entry.generation) !== generation) return [];
    const gaps = worklistPeople.get(entry.id);
    if (!gaps) return [];
    const missing = worklistMissing(gaps);
    const citations = showCitations ? gaps.uncited_events : [];
    return missing.length || citations.length ? [{ ...entry, gaps, missing, citations }] : [];
  });
  worklistResults.replaceChildren();
  for (const { card, generation: level, gaps, missing, citations } of matches.slice(0, worklistLimit)) {
    const row = element("li");
    const jump = element("button", "reset relationship-choice");
    jump.type = "button";
    jump.textContent = `${gaps.person.name} · #${gaps.person.id} · ${worklistGenerationName(level)}`;
    jump.setAttribute("aria-label", `Show ${gaps.person.name}, #${gaps.person.id}, ${worklistGenerationName(level)}, in the chart`);
    jump.addEventListener("click", () => {
      worklistReturnFocus = card;
      worklistDialog.close();
    });
    row.append(jump);
    if (missing.length) row.append(element("p", "hint", missing.map((key) => missingLabels[key]).join(" · ")));
    if (citations.length) {
      const disclosure = element("details", "details-event");
      disclosure.append(element("summary", "", `${citations.length} ${citations.length === 1 ? "event" : "events"} without citations`));
      const events = element("ul");
      for (const event of citations) events.append(element("li", "", [event.kind, event.date].filter(Boolean).join(" · ")));
      disclosure.append(events);
      row.append(disclosure);
    }
    worklistResults.append(row);
  }
  worklistMore.hidden = matches.length <= worklistLimit;
  const shown = Math.min(matches.length, worklistLimit);
  worklistStatus.textContent = matches.length
    ? `${shown} of ${matches.length} matching ${matches.length === 1 ? "card" : "cards"}. Select a name to find that card in the chart.`
    : "No matching research gaps in the displayed cards. Try another filter or expand a branch.";
}

async function loadResearchWorklist() {
  worklistRequest?.abort();
  const request = new AbortController();
  worklistRequest = request;
  worklistCards = [...viewport.querySelectorAll(".person-card")].map((card) => ({
    card,
    id: Number(card.dataset.personId),
    generation: Number(card.dataset.generation),
  }));
  const selectedGeneration = worklistGeneration.value;
  const generations = [...new Set(worklistCards.map(({ generation }) => generation))].sort((left, right) => left - right);
  worklistGeneration.replaceChildren(element("option", "", "All generations"));
  worklistGeneration.firstElementChild.value = "all";
  for (const generation of generations) {
    const option = element("option", "", worklistGenerationName(generation));
    option.value = String(generation);
    worklistGeneration.append(option);
  }
  worklistGeneration.value = generations.includes(Number(selectedGeneration)) && selectedGeneration !== "all"
    ? selectedGeneration : "all";
  worklistCategory.disabled = true;
  worklistGeneration.disabled = true;
  worklistRetry.hidden = true;
  worklistMore.hidden = true;
  worklistResults.replaceChildren();
  worklistResults.setAttribute("aria-busy", "true");
  worklistStatus.textContent = "Checking the displayed people for research gaps…";
  worklistLimit = WORKLIST_PAGE_SIZE;
  worklistPeople = new Map();
  const ids = [...new Set(worklistCards.map(({ id }) => id))];
  const people = new Map();
  try {
    for (let index = 0; index < ids.length; index += 100) {
      const result = await getJSON(`${API}/research-gaps`, request.signal, { person_ids: ids.slice(index, index + 100) });
      if (request.signal.aborted) return;
      for (const gaps of result.people) people.set(gaps.person.id, gaps);
    }
    worklistPeople = people;
    worklistCategory.disabled = false;
    worklistGeneration.disabled = false;
    renderResearchWorklist();
  } catch (error) {
    if (!request.signal.aborted) {
      worklistStatus.textContent = error instanceof Error
        ? `Could not load research gaps. ${error.message}`
        : "Could not load research gaps. Try again.";
      worklistRetry.hidden = false;
    }
  } finally {
    if (worklistRequest === request) {
      worklistResults.removeAttribute("aria-busy");
      worklistRequest = null;
    }
  }
}

function updateResearchWorklist() {
  worklistButton.disabled = !chartData;
  if (worklistDialog.open) void loadResearchWorklist();
}

function closeResearchWorklist() {
  worklistRequest?.abort();
  worklistButton.disabled = true;
  if (worklistDialog.open) worklistDialog.close();
}

worklistButton.addEventListener("click", () => {
  closeSearch();
  worklistReturnFocus = worklistButton;
  void loadResearchWorklist();
  worklistDialog.showModal();
  worklistDialog.scrollTop = 0;
  worklistDialog.querySelector(".details-scroll").scrollTop = 0;
});
worklistDialog.addEventListener("close", () => {
  worklistRequest?.abort();
  if (!worklistReturnFocus?.isConnected) return;
  worklistReturnFocus.focus({ preventScroll: true });
  if (worklistReturnFocus.classList.contains("person-card")) {
    worklistReturnFocus.scrollIntoView({ block: "center", inline: "center" });
  }
});
for (const filter of [worklistCategory, worklistGeneration]) {
  filter.addEventListener("change", () => {
    worklistLimit = WORKLIST_PAGE_SIZE;
    renderResearchWorklist();
  });
}
worklistRetry.addEventListener("click", () => void loadResearchWorklist());
worklistMore.addEventListener("click", () => {
  worklistLimit += WORKLIST_PAGE_SIZE;
  renderResearchWorklist();
});
