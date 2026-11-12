"use strict";

const TREE = document.body.dataset.tree;
const DEFAULT_PERSON = Number(document.body.dataset.startingPerson || 0);
const API = "/api/v1/pedigree";
const SVG = "http://www.w3.org/2000/svg";
const GENERATIONS = ["Starting person", "Parents", "Grandparents"];
const viewport = document.querySelector("#pedigree-viewport");
const rootHeading = document.querySelector("#pedigree-root");
const summary = document.querySelector("#pedigree-summary");
const childrenLinks = document.querySelector("#pedigree-children");
const previousLink = document.querySelector("#pedigree-previous");
const treeStatus = document.querySelector("#pedigree-status");
const publishButton = document.querySelector("#pedigree-publish");
const publishStatus = document.querySelector("#pedigree-publish-status");
const searchForm = document.querySelector("#pedigree-search");
const query = document.querySelector("#pedigree-query");
const searchPanel = document.querySelector("#search-panel");
const searchStatus = document.querySelector("#search-status");
const searchResults = document.querySelector("#pedigree-results");
const detailsButton = document.querySelector("#pedigree-details");
const detailsDialog = document.querySelector("#person-details");
const detailsTitle = document.querySelector("#person-details-title");
const detailsBody = document.querySelector("#person-details-body");
const detailsStatus = document.querySelector("#person-details-status");
const detailsPrevious = document.querySelector("#person-details-previous");
const detailsRecord = document.querySelector("#person-details-record");
const detailsRetry = document.querySelector("#person-details-retry");
const detailsRefreshButton = document.querySelector("#person-details-refresh");
const detailsScroll = document.querySelector(".details-scroll");
const commentsToken = document.querySelector('meta[name="reader-comments-token"]')?.content;
const commentsSection = document.querySelector("#person-comments");
const commentsHeading = document.querySelector("#details-comments");
const commentsList = document.querySelector("#person-comments-list");
const commentsStatus = document.querySelector("#person-comments-status");
const commentsMore = document.querySelector("#person-comments-more");
const commentForm = document.querySelector("#person-comment-form");
const commentInput = document.querySelector("#person-comment");
const commentStatus = document.querySelector("#person-comment-status");
const commentSubmit = commentForm?.querySelector("button");
const commentDate = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const initialDepth = document.querySelector("#pedigree-depth");
const childrenToggle = document.querySelector("#pedigree-show-children");
const siblingsToggle = document.querySelector("#pedigree-show-siblings");
const zoomOut = document.querySelector("#pedigree-zoom-out");
const zoomIn = document.querySelector("#pedigree-zoom-in");
const fitView = document.querySelector("#pedigree-fit");
const zoomLevel = document.querySelector("#pedigree-zoom-level");
const fullScreen = document.querySelector("#pedigree-fullscreen");
const branchExpansions = new Map();
const collapsedPartners = new Set();

let treeRequest;
let searchRequest;
let detailsRequest;
let detailsRefresh;
let detailsPerson;
let detailsOpener;
let commentsView;
let selectedPerson;
let chartData;
let branchRequest;
let saveScrollTimer;
let chartScale = 1;
let resizeChart;

function treeURL(path) {
  const url = new URL(path, location.origin);
  if (TREE) url.searchParams.set("tree", TREE);
  return url.pathname + url.search + url.hash;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function personURL(id) {
  return treeURL(`/pedigree?person=${id}`);
}

function currentPerson() {
  const id = Number(new URLSearchParams(window.location.search).get("person"));
  return Number.isSafeInteger(id) && id > 0 ? id : DEFAULT_PERSON;
}

function previousPerson() {
  const id = history.state?.previousPerson;
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

function saveChartView() {
  if (chartData?.root.person.id !== currentPerson() || viewport.getAttribute("aria-busy") === "true") return;
  history.replaceState({ ...history.state, chartView: {
    tree: TREE,
    person: currentPerson(),
    depth: initialDepth.value,
    children: childrenToggle.checked,
    siblings: siblingsToggle.checked,
    gaps: gapToggle.checked,
    scale: chartScale,
    scrollLeft: viewport.scrollLeft,
    scrollTop: viewport.scrollTop,
    branches: [...branchExpansions].map(([key, branches]) => [key, [...branches.keys()]]),
    collapsedPartners: [...collapsedPartners],
  } }, "", personURL(currentPerson()));
}

function historyView() {
  const view = history.state?.chartView;
  return view?.tree === TREE && view?.person === currentPerson() ? view : null;
}

function updatePreviousLink() {
  const id = previousPerson();
  for (const link of [previousLink, detailsPrevious]) {
    link.setAttribute("aria-disabled", String(id === null));
    if (id === null) {
      link.removeAttribute("href");
      link.title = "Select another person to build a path back.";
    } else {
      link.href = personURL(id);
      link.removeAttribute("title");
    }
  }
}

function personLink(person, className) {
  const link = element("a", className);
  link.href = personURL(person.id);
  link.dataset.personId = String(person.id);
  return link;
}

function factText(fact) {
  return [fact.label, fact.date].filter(Boolean).join(" ") + (fact.place ? ` · ${fact.place}` : "");
}

function compactAge(age) {
  if (age === "under 1 year") return "<1 y";
  const match = /^(about )?(\d+) (year|month|day)s?$/.exec(age);
  if (!match) return age;
  const [, approximate, count, unit] = match;
  const units = { year: "y", month: "mo", day: "d" };
  return `${approximate ? "~" : ""}${count} ${units[unit]}`;
}

function personCard(person, role = "") {
  const card = personLink(person, "person-card");
  if (role) card.append(element("span", "person-role", role));
  const title = element("span", "person-card-heading");
  title.append(
    element("span", "person-name", person.name),
    element("span", "person-id", `#${person.id}`),
  );
  card.append(
    title,
    element("span", "person-fact", person.birth ? factText(person.birth) : "Birth details unknown"),
  );
  const death = person.death;
  const age = person.age_at_death;
  if (death || age) {
    const line = element("span", "person-fact");
    if (death) line.append([death.label, death.date].filter(Boolean).join(" "));
    if (age) {
      const shortAge = compactAge(age);
      const fullAge = `Age at death ${age}`;
      const label = element("span", "person-age", death ? `(${shortAge})` : `Age ${shortAge}`);
      label.setAttribute("aria-hidden", "true");
      label.title = fullAge;
      line.append(death ? " " : "", label, element("span", "visually-hidden", ` ${fullAge}`));
    }
    if (death?.place) line.append(` · ${death.place}`);
    card.append(line);
  } else if (!person.living) {
    card.append(element("span", "person-fact", "Death details unknown"));
  }
  appendGapFlags(card, person.id);
  return card;
}

function cardDetailAction(person, key, action, label) {
  const button = element("button", `reset card-${action}`, label);
  button.type = "button";
  button.dataset.cardKey = key;
  button.dataset.cardAction = action;
  const accessibleLabel = action === "details" ? "View details" : "Comments";
  button.setAttribute("aria-label", `${accessibleLabel} for ${person.name} (#${person.id})`);
  button.setAttribute("aria-haspopup", "dialog");
  button.setAttribute("aria-controls", "person-details");
  button.addEventListener("click", () => void openDetails(person, button));
  return button;
}

function stageNode(stage, person, role, branch, generation, key) {
  const card = personCard(person, role);
  card.dataset.generation = String(generation);
  const actions = element("div", "card-actions");
  actions.append(cardDetailAction(person, key, "details", "Details"));
  if (commentsToken) actions.append(cardDetailAction(person, key, "comments", "Comments"));
  stage.append(card, actions);
  return { card, actions, person, branch, parents: [], y: 0 };
}

function visibleRoot(root, prefix = "") {
  const additions = new Map(
    [...branchExpansions.values()].flatMap((branches) => [...branches]),
  );
  function visit(node, prefix, lineage) {
    const id = node.person.id;
    const key = prefix ? `${prefix}/${id}` : String(id);
    const record = node.has_more_parents ? additions.get(key) ?? node : node;
    const ancestors = new Set([...lineage, id]);
    const parents = record.parents
      .filter((parent) => !ancestors.has(parent.person.id))
      .map((parent) => visit(parent, key, ancestors));
    return {
      ...record, key, parents,
      has_more_parents: record.has_more_parents && !parents.length,
    };
  }
  return visit(root, prefix, new Set());
}

function partnerRootKey(id) {
  return `partner/${id}`;
}

function visiblePartnerRoots(data) {
  const branches = branchExpansions.get(String(data.root.person.id));
  if (!branches) return [];
  return (data.partners ?? []).flatMap((person) => {
    const record = branches.get(partnerRootKey(person.id));
    return record ? [visibleRoot({ ...record, person }, "partner")] : [];
  });
}

function branchFrontier(node) {
  return node.has_more_parents ? [node] : node.parents.flatMap(branchFrontier);
}

async function fetchBranches(frontier, signal, allowMissing = false) {
  const branches = new Map();
  for (let index = 0; index < frontier.length; index += 8) {
    const entries = await Promise.all(frontier.slice(index, index + 8).map(async (key) => {
      const id = key.split("/").at(-1);
      const generations = /^partner\/\d+$/.test(key) ? 2 : 3;
      try {
        const data = await getJSON(`${API}/${id}?generations=${generations}`, signal);
        return [key, data.root];
      } catch (error) {
        if (allowMissing && error instanceof PedigreeRequestError && error.status === 404) return [key, null];
        throw error;
      }
    }));
    for (const [key, branch] of entries) if (branch) branches.set(key, branch);
  }
  return branches;
}

function redrawBranch(key, before) {
  const hadPartnerLabels = Boolean(viewport.querySelector(".partner-marriage"));
  displayGraph(chartData, false);
  const button = viewport.querySelector(`[data-branch-key="${key}"]`);
  if (!button) return;
  const after = button.getBoundingClientRect();
  const hasPartnerLabels = Boolean(viewport.querySelector(".partner-marriage"));
  viewport.scrollBy(hadPartnerLabels === hasPartnerLabels ? after.left - before.left : 0, after.top - before.top);
  button.focus({ preventScroll: true });
  saveChartView();
}

async function toggleBranch(record, button, partners) {
  const before = button.getBoundingClientRect();
  if (branchExpansions.delete(record.key)) {
    redrawBranch(record.key, before);
    return;
  }
  branchRequest?.abort();
  const request = new AbortController();
  branchRequest = request;
  button.disabled = true;
  const frontier = [
    ...branchFrontier(record).map((node) => node.key),
    ...partners.map((person) => partnerRootKey(person.id)),
  ];
  if (frontier.length) treeStatus.textContent = "Loading more ancestors…";
  try {
    const branches = await fetchBranches(frontier, request.signal);
    if (request.signal.aborted) return;
    if (!partners.length && ![...branches.values()].some((branch) => branch.parents.length)) {
      treeStatus.textContent = "No additional recorded ancestors in this branch.";
      return;
    }
    branchExpansions.set(record.key, branches);
    treeStatus.textContent = "";
    redrawBranch(record.key, before);
  } catch (error) {
    if (!request.signal.aborted) treeStatus.textContent = "Could not expand this branch. Try again.";
  } finally {
    button.disabled = false;
  }
}

function attachBranchControl(graph, record, expanded, toggle) {
  const button = element("button", "reset branch-control", expanded ? "Collapse branch" : "Expand branch");
  button.type = "button";
  button.dataset.branchKey = record.key;
  button.setAttribute("aria-expanded", String(expanded));
  button.setAttribute("aria-label", `${button.textContent} for ${record.person.name}`);
  button.addEventListener("click", () => {
    if (chartData && viewport.getAttribute("aria-busy") !== "true") void toggle(button);
  });
  graph.actions.prepend(button);
}

function rootGraph(stage, data) {
  const root = stageNode(stage, data.root.person, "Starting person", null, 1, String(data.root.person.id));
  root.card.setAttribute("aria-current", "true");
  const partnerTrees = visiblePartnerRoots(data).map((record) => ({
    graph: stageNode(stage, record.person, "Partner", null, 1, record.key), record,
  }));
  const siblings = siblingsToggle.checked ? data.siblings.map(({ person, relationship }) => (
    stageNode(stage, person, relationship, null, 1, `sibling/${person.id}`)
  )) : [];
  const levels = [];
  let level = [{ graph: root, record: visibleRoot(data.root) }, ...partnerTrees];
  while (level.length) {
    const depth = levels.length;
    levels.push(level.map(({ graph }) => graph));
    const next = [];
    for (const { graph, record } of level) {
      if (depth === 0 && graph !== root) {
        const collapsed = collapsedPartners.has(record.key);
        if (record.parents.length) {
          attachBranchControl(graph, record, !collapsed, (button) => {
            const before = button.getBoundingClientRect();
            if (collapsed) collapsedPartners.delete(record.key);
            else collapsedPartners.add(record.key);
            redrawBranch(record.key, before);
          });
        }
        if (collapsed) continue;
      } else {
        const partners = graph === root ? data.partners ?? [] : [];
        const expanded = branchExpansions.has(record.key);
        if (expanded || partners.length || branchFrontier(record).length) {
          attachBranchControl(graph, record, expanded, (button) => toggleBranch(record, button, partners));
        }
      }
      graph.marriage = record.marriage;
      graph.parents = record.parents.map((parent, index) => {
        const branch = graph === root
          ? index * 2
          : depth === 1 && graph.branch !== null ? graph.branch + index : graph.branch;
        const node = stageNode(stage, parent.person, "", branch, depth + 2, parent.key);
        next.push({ graph: node, record: parent });
        return node;
      });
    }
    level = next;
  }
  return { root, partners: partnerTrees.map(({ graph }) => graph), siblings, levels };
}

function packColumn(column, gap) {
  // Subtract each card's minimum offset, then merge adjacent runs whose
  // ideal positions would overlap. This keeps the parent order intact.
  let offset = 0;
  const runs = [];
  for (const [index, node] of column.entries()) {
    if (index) offset += (column[index - 1].height + node.height) / 2 + gap;
    node.offset = offset;
    runs.push({ start: index, count: 1, sum: node.y - offset });
    while (runs.length > 1) {
      const right = runs.at(-1);
      const left = runs.at(-2);
      if (left.sum / left.count <= right.sum / right.count) break;
      left.sum += right.sum;
      left.count += right.count;
      runs.pop();
    }
  }
  for (const { start, count, sum } of runs) {
    for (let index = start; index < start + count; index += 1) {
      const node = column[index];
      node.y = node.offset + sum / count;
    }
  }
}

function layoutGraph(graph, rowGap, partnerPortOffset) {
  const { root, partners, siblings, levels } = graph;
  const relatives = [...partners, ...siblings];
  for (const node of [...levels.flat(), ...siblings]) {
    node.height = node.card.offsetHeight + node.actions.offsetHeight;
  }
  let relativeTop = root.height / 2 + rowGap;
  let previous = root;
  for (const node of relatives) {
    node.y = relativeTop + node.height / 2;
    if (node.partnerMarriage) {
      const actionOffset = (node.actions.offsetHeight - previous.actions.offsetHeight) / 2;
      const clearance = node.partnerMarriage.offsetHeight + rowGap + 2 * partnerPortOffset;
      node.y = Math.max(node.y, previous.y + actionOffset + clearance);
    }
    relativeTop = node.y + node.height / 2 + rowGap;
    previous = node;
  }
  for (const [index, level] of levels.entries()) {
    const next = levels[index + 1];
    if (!next) break;
    for (const node of level) {
      const heights = node.parents.reduce((sum, parent) => sum + parent.height, 0);
      const gaps = Math.max(0, node.parents.length - 1) * rowGap;
      let top = node.y - (heights + gaps) / 2;
      for (const parent of node.parents) {
        parent.y = top + parent.height / 2;
        top += parent.height + rowGap;
      }
    }
    packColumn(next, rowGap);
  }
}

function connector(fromX, fromY, toX, toY, branch) {
  const path = document.createElementNS(SVG, "path");
  const bend = (toX - fromX) * 0.55;
  path.setAttribute("d", `M ${fromX} ${fromY} C ${fromX + bend} ${fromY}, ${toX - bend} ${toY}, ${toX} ${toY}`);
  if (branch !== null && branch < 4) path.setAttribute("class", `branch-${branch}`);
  return path;
}

function generationName(level) {
  return GENERATIONS[level] ?? (level === 3 ? "Great-grandparents" : `${level - 2}× great-grandparents`);
}

function displayGraph(data, moveFocus, showChildrenColumn = false) {
  viewport.hidden = false;
  const ruler = element("div", "generation-ruler");
  const surface = element("div", "stage-surface");
  const stage = element("div", "stage");
  const paths = document.createElementNS(SVG, "svg");
  paths.setAttribute("class", "connectors");
  paths.setAttribute("aria-hidden", "true");
  stage.append(paths);
  surface.append(stage);
  viewport.replaceChildren(ruler, surface);

  const graph = rootGraph(stage, data);
  const { root, partners, siblings, levels } = graph;
  const childrenStatus = document.querySelector("#pedigree-children-status");
  const families = data.child_families?.length ? data.child_families
    : data.children.length ? [{ id: 0, partner: null, children: data.children }] : [];
  const childGroups = childrenToggle.checked ? families.map((family) => {
    const label = element("p", "child-family",
      `${root.person.name} · ${family.partner?.name ?? "Other parent not recorded"}`);
    stage.append(label);
    const nodes = family.children.map((person) =>
      stageNode(stage, person, "Child", null, 0, `child/${family.id}/${person.id}`));
    return { label, nodes, partner: family.partner };
  }) : [];
  const children = childGroups.flatMap(({ nodes }) => nodes);
  const rootColumn = children.length ? 1 : 0;
  const columns = [
    ...(children.length ? [{ name: "Children", nodes: children }] : []),
    ...levels.map((nodes, level) => ({ name: `${level + 1}: ${generationName(level)}`, nodes })),
  ];
  const metrics = getComputedStyle(stage);
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
  const cssSize = (property) => parseFloat(metrics.getPropertyValue(property)) * rem;
  const inset = cssSize("--chart-inset");
  const gutter = cssSize("--column-gap");
  const cardWidth = root.card.offsetWidth;
  const columnWidth = cardWidth + gutter;
  const rowGap = cssSize("--row-gap");
  const partnerPortOffset = rem * 0.375;
  for (const node of partners) {
    const marriages = data.partner_marriages?.[node.person.id];
    if (!marriages?.length) continue;
    const label = element("div", "marriage partner-marriage");
    label.dataset.partnerId = String(node.person.id);
    label.style.width = `${gutter - rem * 1.5}px`;
    for (const fact of marriages) label.append(element("div", "", factText(fact)));
    stage.append(label);
    node.partnerMarriage = label;
  }
  const partnershipGutter = partners.some((node) => node.partnerMarriage) ? gutter : 0;
  layoutGraph(graph, rowGap, partnerPortOffset);
  for (const child of children) child.height = child.card.offsetHeight + child.actions.offsetHeight;
  for (const group of childGroups) {
    group.height = group.label.offsetHeight + rowGap
      + group.nodes.reduce((height, node) => height + node.height + rowGap, 0);
  }
  let childTop = root.y - childGroups.reduce((height, group) => height + group.height, 0) / 2;
  for (const group of childGroups) {
    group.top = childTop;
    childTop += group.label.offsetHeight + rowGap;
    for (const child of group.nodes) {
      child.y = childTop + child.height / 2;
      childTop += child.height + rowGap;
    }
  }

  const cards = [...levels.flat(), ...siblings, ...children];
  const top = Math.min(...cards.map((node) => node.y - node.height / 2), ...childGroups.map((group) => group.top));
  const bottom = Math.max(...cards.map((node) => node.y + node.height / 2));
  const graphWidth = inset * 2 + cardWidth + (columns.length - 1) * columnWidth + partnershipGutter;
  const graphHeight = bottom - top + inset * 2;
  const positionX = (column) => inset + column * columnWidth + (column >= rootColumn ? partnershipGutter : 0);
  const positionY = (node) => inset + node.y - top;
  const cardY = (node) => positionY(node) - node.actions.offsetHeight / 2;

  stage.style.width = `${graphWidth}px`;
  stage.style.height = `${graphHeight}px`;
  paths.setAttribute("width", String(graphWidth));
  paths.setAttribute("height", String(graphHeight));

  function placeCard(node, column) {
    const x = positionX(column);
    const y = positionY(node) - node.height / 2;
    node.card.style.left = `${x}px`;
    node.card.style.top = `${y}px`;
    node.actions.style.left = `${x}px`;
    node.actions.style.top = `${y + node.card.offsetHeight}px`;
  }
  for (const sibling of siblings) placeCard(sibling, rootColumn);
  if (partners.length) {
    const x = positionX(rootColumn);
    const joint = partnershipGutter ? x - partnershipGutter + rem * 0.5 : x - inset / 2;
    const offset = partnershipGutter ? partnerPortOffset : 0;
    const rootY = cardY(root) - offset;
    let previousY = rootY;
    for (const node of partners) {
      const y = cardY(node) - offset;
      const label = node.partnerMarriage;
      if (label) {
        label.style.left = `${joint + rem * 0.5}px`;
        label.style.top = `${(previousY + y) / 2}px`;
      }
      previousY = y;
    }
    const arms = partners.slice(0, -1).map((node) => `M ${joint} ${cardY(node) - offset} H ${x}`);
    const connection = document.createElementNS(SVG, "path");
    connection.setAttribute("class", "partner-connector");
    connection.setAttribute("d", [`M ${x} ${rootY} H ${joint} V ${previousY} H ${x}`, ...arms].join(" "));
    paths.append(connection);
  }
  for (const [index, group] of childGroups.entries()) {
    group.label.style.left = `${positionX(0)}px`;
    group.label.style.top = `${inset + group.top - top}px`;
    const fromX = positionX(0) + cardWidth;
    const jointX = fromX + gutter * 0.42;
    const jointY = (cardY(group.nodes[0]) + cardY(group.nodes.at(-1))) / 2;
    const branch = childGroups.length > 1 ? index % 4 : null;
    for (const child of group.nodes) {
      placeCard(child, 0);
      paths.append(connector(fromX, cardY(child), jointX, jointY, branch));
    }
    const parents = [root, ...partners.filter((node) => node.person.id === group.partner?.id)];
    const rootX = positionX(rootColumn);
    for (const parent of parents) {
      const line = connector(jointX, jointY, rootX - partnershipGutter, cardY(parent), branch);
      if (partnershipGutter) line.setAttribute("d", `${line.getAttribute("d")} H ${rootX}`);
      paths.append(line);
    }
  }

  for (const [levelIndex, level] of levels.entries()) {
    const column = levelIndex + rootColumn;
    for (const node of level) {
      placeCard(node, column);
      const fromX = positionX(column) + cardWidth;
      const toX = positionX(column + 1);
      const fromY = cardY(node);
      for (const parent of node.parents) {
        paths.append(connector(fromX, fromY, toX, cardY(parent), parent.branch));
      }
      if (node.marriage && node.parents.length > 1) {
        const label = element("span", "marriage", factText(node.marriage));
        const firstY = cardY(node.parents[0]);
        const lastY = cardY(node.parents.at(-1));
        label.style.left = `${fromX + rem * 0.75}px`;
        label.style.width = `${gutter - rem * 1.5}px`;
        label.style.top = `${(firstY + lastY) / 2}px`;
        stage.append(label);
      }
    }
  }
  rootHeading.textContent = root.person.name;
  const ancestorCount = new Set(levels.slice(1).flat()
    .filter((node) => node.branch !== null).map((node) => node.person.id)).size;
  const siblingCount = siblings.length;
  summary.textContent = [
    `${ancestorCount} recorded ${ancestorCount === 1 ? "ancestor" : "ancestors"}`,
    `${levels.length} ${levels.length === 1 ? "generation" : "generations"}`,
    ...(siblingsToggle.checked ? [`${siblingCount} ${siblingCount === 1 ? "sibling" : "siblings"}`] : []),
    ...(partners.length ? [`${partners.length} ${partners.length === 1 ? "partner" : "partners"} shown`] : []),
  ].join(" · ");
  childrenLinks.replaceChildren();
  childrenLinks.hidden = true;
  // Direct links to a leaf have no previous chart entry to return to.
  if (!childrenToggle.checked && !root.parents.length && previousPerson() === null && data.children.length) {
    childrenLinks.append(document.createTextNode(data.children.length === 1 ? "Child: " : "Children: "));
    for (const [index, child] of data.children.entries()) {
      if (index) childrenLinks.append(document.createTextNode(" · "));
      const link = personLink(child);
      link.textContent = child.name;
      childrenLinks.append(link);
    }
    childrenLinks.hidden = false;
  }
  document.title = `${root.person.name} · Family pedigree`;
  const generationSelect = document.querySelector("#pedigree-generation");
  const centerButton = document.querySelector("#pedigree-center");
  generationSelect.replaceChildren(...columns.map(({ name }, column) => {
    const option = element("option", "", name);
    option.value = String(column);
    return option;
  }));
  generationSelect.disabled = false;
  centerButton.disabled = false;
  childrenToggle.disabled = false;
  siblingsToggle.disabled = false;
  childrenStatus.hidden = !childrenToggle.checked || children.length > 0;
  childrenStatus.textContent = childrenStatus.hidden ? "" : "No recorded children";
  childrenToggle.onchange = () => {
    displayGraph(data, false, childrenToggle.checked);
    saveChartView();
  };
  function goToGeneration() {
    const column = Number(generationSelect.value);
    const left = positionX(column) - inset - (column === rootColumn ? partnershipGutter : 0);
    viewport.scrollLeft = left * chartScale;
    const nodes = columns[column].nodes;
    const visibleTop = viewport.scrollTop;
    const visibleBottom = viewport.scrollTop + viewport.clientHeight - ruler.offsetHeight;
    const hasVisibleCard = nodes.some((node) => (
      (positionY(node) + node.height / 2) * chartScale > visibleTop
      && (positionY(node) - node.height / 2) * chartScale < visibleBottom
    ));
    if (!hasVisibleCard) {
      const middle = (visibleTop + visibleBottom) / 2;
      const nearest = nodes.reduce((closest, node) => (
        Math.abs(positionY(node) * chartScale - middle) < Math.abs(positionY(closest) * chartScale - middle) ? node : closest
      ));
      viewport.scrollTop = Math.max(0,
        positionY(nearest) * chartScale - (viewport.clientHeight - ruler.offsetHeight) / 2);
    }
  }
  generationSelect.onchange = goToGeneration;
  function centerStartingPerson() {
    const rootPosition = parseFloat(getComputedStyle(stage).getPropertyValue("--root-position"));
    const centerX = positionX(rootColumn) + (cardWidth - partnershipGutter) / 2;
    viewport.scrollLeft = Math.max(0, centerX * chartScale - viewport.clientWidth / 2);
    viewport.scrollTop = Math.max(
      0,
      Math.min(
        ruler.offsetHeight + positionY(root) * chartScale - viewport.clientHeight * rootPosition,
        (positionY(root) - root.height / 2) * chartScale,
      ),
    );
    generationSelect.value = String(rootColumn);
  }
  const headings = columns.map(({ name }) => {
    const heading = element("span", "generation");
    heading.title = name.replace(/^\d+: /, "");
    return heading;
  });
  ruler.append(...headings);
  let stageOffset = 0;
  function fittingScale() {
    return Math.min(1, viewport.clientWidth / graphWidth,
      Math.max(1, viewport.clientHeight - ruler.offsetHeight) / graphHeight);
  }
  const minimumScale = () => Math.min(0.25, fittingScale());
  function renderScale() {
    const width = Math.ceil(graphWidth * chartScale);
    stageOffset = chartScale < 1 ? Math.max(0, (viewport.clientWidth - width) / 2) : 0;
    stage.style.left = `${stageOffset}px`;
    stage.style.transform = `scale(${chartScale})`;
    surface.style.width = `${width}px`;
    surface.style.height = `${Math.ceil(graphHeight * chartScale)}px`;
    ruler.style.width = surface.style.width;
    for (const [column, heading] of headings.entries()) {
      const scaledColumn = columnWidth * chartScale;
      heading.style.left = `${stageOffset + positionX(column) * chartScale}px`;
      heading.style.width = `${Math.max(0, scaledColumn - 4)}px`;
      const generation = column - rootColumn + 1;
      heading.textContent = scaledColumn < 110 && generation > 0 ? String(generation) : heading.title;
    }
    zoomLevel.textContent = `${Math.max(1, Math.round(chartScale * 100))}%`;
    zoomOut.disabled = chartScale <= minimumScale();
    zoomIn.disabled = chartScale >= 2;
    fitView.disabled = false;
  }
  function zoomTo(scale) {
    const width = viewport.clientWidth / 2;
    const height = (viewport.clientHeight - ruler.offsetHeight) / 2;
    const left = (viewport.scrollLeft + width - stageOffset) / chartScale;
    const top = (viewport.scrollTop + height) / chartScale;
    chartScale = scale;
    renderScale();
    viewport.scrollTo(left * scale + stageOffset - width, top * scale - height);
    saveChartView();
  }
  zoomOut.onclick = () => zoomTo(Math.max(minimumScale(), chartScale / 1.25));
  zoomIn.onclick = () => zoomTo(Math.min(2, chartScale * 1.25));
  fitView.onclick = () => zoomTo(fittingScale());
  resizeChart = renderScale;
  renderScale();
  centerButton.onclick = centerStartingPerson;
  centerStartingPerson();
  if (showChildrenColumn && children.length) {
    generationSelect.value = "0";
    goToGeneration();
  }
  if (moveFocus) rootHeading.focus({ preventScroll: true });
  updatePedigreeTools();
  updateResearchWorklist();
}

class PedigreeRequestError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function requestJSON(url, request) {
  const response = await fetch(treeURL(url), request);
  if (!response.ok) {
    const result = await response.json().catch(() => null);
    const message = typeof result?.detail === "string" ? result.detail : "The RootsMagic reader could not load this request.";
    throw new PedigreeRequestError(message, response.status);
  }
  return response.json();
}

function getJSON(url, signal, data) {
  const request = data === undefined
    ? { signal, headers: { Accept: "application/json" } }
    : { signal, method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) };
  return requestJSON(url, request);
}

async function refreshBranches(data, signal) {
  const rootKey = String(data.root.person.id);
  const partnerKeys = new Set((data.partners ?? []).map((person) => partnerRootKey(person.id)));
  const entries = [...branchExpansions].map(([key, branches]) => {
    const partner = key.match(/^partner\/\d+/)?.[0];
    const removed = partner !== undefined && !partnerKeys.has(partner);
    const keys = [...branches.keys()];
    const refreshedKeys = removed ? [] : key === rootKey
      ? [...keys.filter((path) => !path.startsWith("partner/")), ...partnerKeys] : keys;
    return { key, branches, refreshedKeys, removed };
  });
  const paths = new Set(entries.flatMap(({ refreshedKeys }) => refreshedKeys));
  const updated = await fetchBranches([...paths], signal, true);
  if (signal.aborted) return;
  for (const { key, branches, refreshedKeys, removed } of entries) {
    if (branchExpansions.get(key) !== branches) continue;
    const resolved = refreshedKeys.filter((path) => updated.has(path));
    if (removed || !resolved.length) branchExpansions.delete(key);
    else branchExpansions.set(key, new Map(resolved.map((path) => [path, updated.get(path)])));
  }
  for (const key of collapsedPartners) {
    if (!partnerKeys.has(key)) collapsedPartners.delete(key);
  }
}

async function loadTree(id, moveFocus = false, view = null, refresh = false) {
  branchRequest?.abort();
  if (!refresh) {
    closeDetails();
    closeResearchWorklist();
    resetPedigreeTools();
    branchExpansions.clear();
    collapsedPartners.clear();
    if (view) {
      initialDepth.value = view.depth;
      childrenToggle.checked = view.children;
      siblingsToggle.checked = view.siblings ?? true;
      gapToggle.checked = view.gaps;
      chartScale = view.scale;
      for (const [key, paths] of view.branches) {
        branchExpansions.set(key, new Map(paths.map((path) => [path, null])));
      }
      for (const key of view.collapsedPartners) collapsedPartners.add(key);
    }
    chartData = null;
    resizeChart = null;
    selectedPerson = null;
    detailsButton.disabled = true;
    for (const control of [zoomOut, zoomIn, fitView, childrenToggle, siblingsToggle,
      document.querySelector("#pedigree-generation"), document.querySelector("#pedigree-center")]) {
      control.disabled = true;
    }
    childrenLinks.hidden = true;
    childrenLinks.replaceChildren();
  }
  treeRequest?.abort();
  const request = new AbortController();
  treeRequest = request;
  updatePreviousLink();
  if (!id) {
    viewport.hidden = true;
    viewport.replaceChildren();
    rootHeading.textContent = "This tree has no people";
    treeStatus.textContent = "Choose another tree or add someone in RootsMagic.";
    viewport.removeAttribute("aria-busy");
    return false;
  }
  treeStatus.textContent = refresh ? "Refreshing the pedigree…" : "Loading the pedigree…";
  viewport.setAttribute("aria-busy", "true");
  try {
    const data = await getJSON(`${API}/${id}?generations=${initialDepth.value}`, request.signal);
    if (request.signal.aborted) return;
    if (refresh || view?.branches?.length) {
      await refreshBranches(data, request.signal);
      if (request.signal.aborted) return;
      invalidateGaps();
    }
    treeStatus.textContent = refresh ? "Pedigree refreshed." : "";
    chartData = data;
    displayGraph(data, moveFocus);
    selectedPerson = data.root.person;
    detailsButton.disabled = false;
    if (view && Number.isFinite(view.scrollTop)) {
      viewport.scrollTo(view.scrollLeft, view.scrollTop);
    }
    return true;
  } catch (error) {
    if (request.signal.aborted) return;
    if (!selectedPerson) {
      viewport.hidden = true;
      viewport.replaceChildren();
      rootHeading.textContent = "Pedigree unavailable";
      summary.textContent = "";
    }
    treeStatus.textContent = refresh
      ? "Could not refresh the pedigree. Select Refresh to try again."
      : error instanceof Error && error.name !== "TypeError"
        ? error.message
        : "Could not reach the local RootsMagic reader. Select Refresh to try again.";
    if (moveFocus) rootHeading.focus({ preventScroll: true });
    return false;
  } finally {
    if (treeRequest === request) {
      viewport.removeAttribute("aria-busy");
      saveChartView();
    }
  }
}

function focusDetailsSection(heading) {
  const inset = Number.parseFloat(getComputedStyle(detailsScroll).paddingBlockStart);
  detailsScroll.scrollTop += heading.getBoundingClientRect().top - detailsScroll.getBoundingClientRect().top - inset;
  heading.focus({ preventScroll: true });
}

detailsDialog.querySelectorAll(".details-sections a").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    const heading = detailsScroll.querySelector(link.getAttribute("href"));
    if (heading) focusDetailsSection(heading);
  });
});

function detailSection(title, id) {
  const section = element("section", "details-section");
  const heading = element("h3", "", title);
  heading.id = id;
  heading.tabIndex = -1;
  section.setAttribute("aria-labelledby", id);
  section.append(heading);
  return section;
}

function eventDetails(event) {
  const item = element("li", "details-event");
  item.dataset.eventId = String(event.id);
  item.append(element("h4", "", event.kind));
  if (event.date_display) item.append(element("p", "event-date", event.date_display));
  const place = [event.place_detail, event.place].filter(Boolean).join(" · ");
  if (place) item.append(element("p", "", place));
  if (event.details) item.append(element("p", "record-note", event.details));
  if (event.note || event.citations.length) {
    const disclosure = element("details", "event-sources");
    disclosure.dataset.eventId = String(event.id);
    const labels = [];
    if (event.citations.length) labels.push(`${event.citations.length} ${event.citations.length === 1 ? "source" : "sources"}`);
    if (event.note) labels.push("Notes");
    disclosure.append(element("summary", "", labels.join(" · ")));
    if (event.note) disclosure.append(element("p", "record-note", event.note));
    if (event.citations.length) {
      const citations = element("ul", "citation-list");
      for (const citation of event.citations) {
        const source = element("li");
        source.append(element("p", "citation-name", citation.source || citation.name || "Untitled source"));
        if (citation.name && citation.source) source.append(element("p", "", citation.name));
        if (citation.reference) source.append(element("p", "record-note", citation.reference));
        if (citation.footnote) source.append(element("p", "record-note", citation.footnote));
        citations.append(source);
      }
      disclosure.append(citations);
    }
    item.append(disclosure);
  }
  return item;
}

function familyRows(relatives, classes = "family-rows", marriageDate = null) {
  const list = element("ul", classes);
  for (const relative of relatives) {
    const row = element("li", "family-row");
    const link = personLink(relative, "family-link");
    link.append(
      element("span", "family-role", relative.role),
      element("span", "family-name", relative.name),
      element("small", "family-id", `#${relative.id}`),
    );
    const dates = [
      relative.birth_date && `Born ${relative.birth_date}`,
      relative.death_date && `Died ${relative.death_date}`,
      relative.age_at_death && (relative.death_date
        ? `aged ${relative.age_at_death}` : `Age at death ${relative.age_at_death}`),
    ].filter(Boolean);
    if (dates.length) link.append(element("span", "family-dates", dates.join(" · ")));
    if (marriageDate) link.append(element("span", "family-marriage", `Married ${marriageDate}`));
    row.append(link);
    list.append(row);
  }
  return list;
}

function displayFamily(person) {
  const section = detailSection("Family", "details-family");
  const groups = element("div", "family-groups");
  const appendGroup = (content) => {
    const group = element("div", "family-group");
    group.append(content);
    groups.append(group);
    return group;
  };
  const family = person.family;
  if (family) {
    if (family.parents.length) appendGroup(familyRows(family.parents));
    for (const { partner, marriage_date: marriageDate, children } of family.partnerships) {
      const content = partner ? familyRows([partner], "family-rows", marriageDate)
        : element("p", "family-unrecorded", "Other parent not recorded");
      if (!partner && marriageDate) content.append(element("span", "family-marriage", `Married ${marriageDate}`));
      const group = appendGroup(content);
      if (children.length) group.append(familyRows(children, "family-rows family-children"));
    }
    if (family.siblings.length) appendGroup(familyRows(family.siblings));
  } else if (person.relatives.length) {
    appendGroup(familyRows(person.relatives));
  }
  section.append(groups.childElementCount ? groups
    : element("p", "details-empty", "No family relationships recorded."));
  return section;
}

function displayDetails(person) {
  detailsTitle.textContent = person.name;
  const identity = detailSection("Identity", "details-identity");
  identity.append(element("p", "", `RootsMagic #${person.id}${person.living ? " · Living" : ""}`));
  if (person.age_at_death) identity.append(element("p", "", `Age at death ${person.age_at_death}`));
  const alternateNames = person.names.filter((name) => !name.primary);
  if (alternateNames.length) {
    identity.append(element("h4", "", "Also recorded as"));
    const names = element("ul", "alternate-names");
    for (const name of alternateNames) names.append(element("li", "", name.name));
    identity.append(names);
  }
  const events = detailSection("Timeline", "details-timeline");
  if (person.events.length) {
    const list = element("ol", "event-list");
    for (const event of person.events) list.append(eventDetails(event));
    events.append(list);
  } else {
    events.append(element("p", "details-empty", "No events recorded."));
  }
  const family = displayFamily(person);
  const notes = detailSection("Personal notes", "details-notes");
  notes.append(element("p", person.note ? "record-note" : "details-empty", person.note || "No personal notes recorded."));
  detailsBody.replaceChildren(identity, events, family, notes);
}

function commentItem(comment) {
  const item = element("li");
  const byline = element("div", "comment-byline");
  const date = new Date(comment.created_at);
  const time = element("time", "", commentDate.format(date));
  time.dateTime = comment.created_at;
  byline.append(element("strong", "", comment.author), time);
  item.append(byline, element("p", "comment-text", comment.body));
  return item;
}

function setCommentControls(view) {
  commentSubmit.disabled = view.loading || view.posting;
  commentsMore.disabled = commentSubmit.disabled;
}

async function loadComments(view, before = null) {
  if (!view || view.loading || view.posting) return;
  view.loading = true;
  setCommentControls(view);
  commentsStatus.textContent = before === null ? "Loading comments…" : "Loading older comments…";
  commentsList.setAttribute("aria-busy", "true");
  try {
    const path = `${API}/${view.personId}/comments${before === null ? "" : `?before=${before}`}`;
    const { comments, next_before: nextBefore } = await getJSON(path, view.request.signal);
    if (view !== commentsView) return;
    const items = comments.map(commentItem);
    if (before === null) commentsList.replaceChildren(...items);
    else commentsList.append(...items);
    view.loaded = true;
    view.before = nextBefore;
    commentsMore.textContent = "Show older comments";
    commentsMore.hidden = nextBefore === null;
    commentsStatus.textContent = commentsList.childElementCount
      ? before === null ? "" : "Older comments loaded."
      : "No comments yet.";
  } catch (error) {
    if (view !== commentsView) return;
    commentsStatus.textContent = error instanceof PedigreeRequestError
      ? error.message : "Comments could not be loaded.";
    view.before = before;
    commentsMore.textContent = "Retry loading comments";
    commentsMore.hidden = false;
  } finally {
    if (view === commentsView) {
      view.loading = false;
      commentsList.removeAttribute("aria-busy");
      setCommentControls(view);
    }
  }
}

function openComments(personId) {
  if (!commentsSection) return;
  commentsView?.request.abort();
  commentsView = { personId, request: new AbortController(), before: null, loading: false, posting: false, loaded: false };
  commentsSection.hidden = false;
  commentForm.reset();
  commentStatus.textContent = "";
  commentsList.replaceChildren();
  commentsMore.hidden = true;
  void loadComments(commentsView);
}

commentsMore?.addEventListener("click", () => {
  if (commentsView) void loadComments(commentsView, commentsView.before);
});

commentForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const view = commentsView;
  if (!view || view.loading || view.posting) return;
  const body = commentInput.value.trim();
  if (!body) {
    commentStatus.textContent = "Enter a comment.";
    commentInput.focus();
    return;
  }
  view.posting = true;
  setCommentControls(view);
  commentStatus.textContent = "Adding comment…";
  try {
    const comment = await requestJSON(`${API}/${view.personId}/comments`, {
      method: "POST",
      signal: view.request.signal,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRF-Token": commentsToken,
      },
      body: JSON.stringify({ body }),
    });
    if (view !== commentsView) return;
    if (view.loaded && !commentsList.childElementCount) commentsStatus.textContent = "";
    commentsList.prepend(commentItem(comment));
    if (commentInput.value.trim() === body) commentForm.reset();
    commentStatus.textContent = "Comment added.";
  } catch (error) {
    if (view !== commentsView) return;
    commentStatus.textContent = error instanceof PedigreeRequestError
      ? error.message : "The comment could not be added. Try again.";
  } finally {
    if (view === commentsView) {
      view.posting = false;
      setCommentControls(view);
    }
  }
});

function closeDetails() {
  detailsRefresh = null;
  detailsRequest?.abort();
  commentsView?.request.abort();
  commentsView = undefined;
  if (detailsDialog.open) {
    detailsDialog.close();
    const key = detailsOpener?.dataset.cardKey;
    const action = detailsOpener?.dataset.cardAction;
    const replacement = key && viewport.querySelector(
      `[data-card-key="${CSS.escape(key)}"][data-card-action="${CSS.escape(action)}"]`,
    );
    const opener = detailsOpener?.isConnected ? detailsOpener : replacement || detailsButton;
    opener.focus({ preventScroll: true });
  }
}

async function openDetails(person = selectedPerson, opener = detailsButton, refresh = false) {
  if (!person || !chartData) return;
  closeSearch();
  detailsRequest?.abort();
  const request = new AbortController();
  detailsRequest = request;
  detailsPerson = person;
  detailsOpener = opener;
  detailsTitle.textContent = person.name;
  detailsRecord.href = treeURL(`/pedigree/people/${person.id}/record`);
  if (!refresh) detailsBody.replaceChildren();
  detailsBody.setAttribute("aria-busy", "true");
  detailsRetry.hidden = true;
  detailsStatus.textContent = refresh ? "Refreshing the pedigree and details…" : "Loading person details…";
  if (!detailsDialog.open) detailsDialog.showModal();
  if (!refresh) {
    detailsScroll.scrollTop = 0;
    openComments(person.id);
    if (opener.dataset.cardAction === "comments") focusDetailsSection(commentsHeading);
  }
  try {
    const profile = await getJSON(`${API}/${person.id}/details`, request.signal);
    if (request.signal.aborted || !detailsDialog.open) return;
    const expanded = new Set(
      [...detailsBody.querySelectorAll("details[open]")].map((item) => item.dataset.eventId),
    );
    const commentAnchor = commentsSection?.contains(document.activeElement)
      ? commentsHeading.getBoundingClientRect().top : null;
    displayDetails(profile);
    if (commentAnchor !== null) {
      detailsScroll.scrollTop += commentsHeading.getBoundingClientRect().top - commentAnchor;
    }
    void loadPersonEvidence(person.id, request.signal);
    for (const disclosure of detailsBody.querySelectorAll("details[data-event-id]")) {
      disclosure.open = expanded.has(disclosure.dataset.eventId);
    }
    if (!refresh) detailsStatus.textContent = "";
    return true;
  } catch (error) {
    if (request.signal.aborted) return;
    detailsStatus.textContent = error instanceof Error && error.name !== "TypeError"
      ? error.message
      : "Could not reach the local RootsMagic reader. Try again when it is available.";
    detailsRetry.hidden = false;
    return false;
  } finally {
    if (detailsRequest === request) detailsBody.removeAttribute("aria-busy");
  }
}

detailsButton.addEventListener("click", () => void openDetails());
function refreshTree() {
  const position = { scrollLeft: viewport.scrollLeft, scrollTop: viewport.scrollTop };
  return loadTree(currentPerson(), false, position, true);
}

async function publishTree() {
  if (publishButton.getAttribute("aria-disabled") === "true") return;
  publishButton.setAttribute("aria-disabled", "true");
  publishStatus.textContent = "Publishing linked media and the tree…";
  try {
    const result = await requestJSON(`${API}/publish`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-RootsMagic-Publish-Token": publishButton.dataset.publishToken,
      },
    });
    const outcome = result.status === "already_published"
      ? "The server already has this tree snapshot."
      : "Tree published. Refresh the VM reader to see the update.";
    publishStatus.textContent = `${outcome} Copied ${result.copied}, updated ${result.replaced}, and reused ${result.reused} media files.`;
  } catch (error) {
    const message = error instanceof PedigreeRequestError ? error.message : "Publishing could not be completed.";
    publishStatus.textContent = `${message} Select Publish to try again.`;
  } finally {
    publishButton.removeAttribute("aria-disabled");
  }
}

async function refreshDetails() {
  const attempt = {};
  detailsRefresh = attempt;
  const scrollTop = detailsScroll.scrollTop;
  const [detailsUpdated, chartUpdated] = await Promise.all([
    openDetails(detailsPerson, detailsOpener, true), refreshTree(),
  ]);
  if (detailsRefresh !== attempt || !detailsDialog.open) return;
  const failed = [!chartUpdated && "the pedigree", !detailsUpdated && "person details"].filter(Boolean);
  detailsStatus.textContent = failed.length
    ? `Could not refresh ${failed.join(" and ")}. Try again.`
    : "Pedigree and details refreshed.";
  detailsRetry.hidden = !failed.length;
  detailsScroll.scrollTop = scrollTop;
}

document.querySelector("#pedigree-refresh").addEventListener("click", () => void refreshTree());
publishButton?.addEventListener("click", () => void publishTree());
detailsRefreshButton.addEventListener("click", () => void refreshDetails());
detailsRetry.addEventListener("click", () => {
  detailsRefreshButton.focus({ preventScroll: true });
  void refreshDetails();
});
document.querySelector("#person-details-close").addEventListener("click", closeDetails);
detailsDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeDetails();
});

const searchMore = element("button", "reset search-more", "Show more");
searchMore.id = "search-more";
searchMore.type = "button";
searchMore.hidden = true;
searchPanel.append(searchMore);
let searchTerm = "";

function closeSearch(clear = false) {
  searchRequest?.abort();
  searchTerm = "";
  searchPanel.hidden = true;
  searchResults.replaceChildren();
  searchMore.hidden = true;
  if (clear) query.value = "";
}

function searchYears(person) {
  if (person.birth_year && person.death_year) return `${person.birth_year}–${person.death_year}`;
  if (person.birth_year) return `Born ${person.birth_year}`;
  if (person.death_year) return `Died ${person.death_year}`;
  return "";
}

async function loadSearchPage() {
  searchRequest?.abort();
  const request = new AbortController();
  searchRequest = request;
  const offset = searchResults.childElementCount;
  const term = searchTerm;
  searchPanel.hidden = false;
  searchMore.disabled = true;
  searchStatus.textContent = offset ? "Loading more matches…" : "Searching…";
  try {
    const params = new URLSearchParams({ q: term, limit: "30", offset: String(offset) });
    const { people, total } = await getJSON(`${API}/people?${params}`, request.signal);
    if (request.signal.aborted || searchRequest !== request) return;
    const loaded = offset + people.length;
    searchStatus.textContent = total > loaded
      ? `Showing ${loaded} of ${total} matches.`
      : total ? `${total} ${total === 1 ? "match" : "matches"}` : "No matches. Try another name or RootsMagic ID.";
    for (const person of people) {
      const item = element("li");
      const link = personLink(person);
      const identity = element("span", "search-identity");
      identity.append(element("span", "", person.name));
      const years = searchYears(person);
      if (years) identity.append(element("span", "search-years", years));
      link.append(identity, element("span", "person-id", `#${person.id}`));
      item.append(link);
      searchResults.append(item);
    }
    searchMore.textContent = "Show more";
    searchMore.hidden = loaded >= total || people.length === 0;
    searchResults.children[offset]?.querySelector("a")?.focus();
  } catch (error) {
    if (!(error instanceof Error)) throw error;
    if (!request.signal.aborted && searchRequest === request) {
      searchStatus.textContent = offset ? "More matches could not be loaded. Try again." : "Search is unavailable. Try again.";
      searchMore.textContent = "Retry search";
      searchMore.hidden = false;
    }
  } finally {
    if (searchRequest === request) searchMore.disabled = false;
  }
}

searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  closeSearch();
  searchTerm = query.value.trim().replace(/^#(?=\d+$)/, "");
  if (searchTerm) void loadSearchPage();
});
searchMore.addEventListener("click", () => void loadSearchPage());

query.addEventListener("input", () => closeSearch());
searchForm.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !searchPanel.hidden) {
    event.preventDefault();
    closeSearch();
    query.focus();
  }
});

document.addEventListener("click", (event) => {
  if (!(event.target instanceof Element)) return;
  const link = event.target.closest("[data-person-id], #pedigree-previous, #person-details-previous, #pedigree-reset");
  if (link && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && event.button === 0) {
    event.preventDefault();
    if (link.getAttribute("aria-disabled") === "true") return;
    closeSearch(true);
    closeDetails();
    saveChartView();
    if (link === previousLink || link === detailsPrevious) {
      history.back();
      return;
    }
    const current = currentPerson();
    const id = Number(link.dataset.personId ?? DEFAULT_PERSON);
    if (id !== current) {
      history.pushState({ previousPerson: current }, "", personURL(id));
    }
    void loadTree(id, true);
  } else if (!searchForm.contains(event.target)) {
    closeSearch();
  }
});

window.addEventListener("popstate", () => {
  clearTimeout(saveScrollTimer);
  closeSearch(true);
  void loadTree(currentPerson(), true, historyView());
});

viewport.addEventListener("scroll", () => {
  clearTimeout(saveScrollTimer);
  saveScrollTimer = setTimeout(saveChartView, 350);
}, { passive: true });
window.addEventListener("pagehide", saveChartView);
window.addEventListener("resize", () => resizeChart?.());

function setFullScreen(enabled) {
  document.body.classList.toggle("chart-fullscreen", enabled);
  fullScreen.setAttribute("aria-pressed", String(enabled));
  fullScreen.textContent = enabled ? "Exit full screen" : "Full screen";
  resizeChart?.();
}

fullScreen.addEventListener("click", () => setFullScreen(fullScreen.getAttribute("aria-pressed") !== "true"));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && document.body.classList.contains("chart-fullscreen")
      && !document.querySelector("dialog[open]")) {
    setFullScreen(false);
    fullScreen.focus({ preventScroll: true });
  }
});

initPedigreeTools();
siblingsToggle.addEventListener("change", () => {
  if (chartData) redrawWithScroll();
  saveChartView();
});
void loadTree(currentPerson(), false, historyView());
initialDepth.addEventListener("change", () => void loadTree(currentPerson()));
