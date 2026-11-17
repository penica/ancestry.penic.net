"use strict";

(() => {
  const data = JSON.parse(document.querySelector("#pedigree-data").textContent);
  const viewport = document.querySelector(".viewport");
  const graph = document.querySelector(".graph");
  const surface = document.querySelector(".surface");
  const lines = document.querySelector(".lines");
  const dialog = document.querySelector("dialog");
  const facts = dialog.querySelector(".facts");
  const zoomLevel = document.querySelector("#zoom-level");
  const svg = "http://www.w3.org/2000/svg";
  const marriages = new Map();
  const connections = [];
  let scale = 1;
  let opener;

  for (const family of Object.values(data.families)) {
    if (!family.facts.length) continue;
    for (const { id } of family.members) {
      const unions = marriages.get(id) ?? [];
      const names = family.members.filter((member) => member.id !== id)
        .map((member) => data.people[member.id].name);
      unions.push({ names, facts: family.facts });
      marriages.set(id, unions);
    }
  }

  function element(tag, className = "", text = "") {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  }

  function button(className, text = "") {
    const node = element("button", className, text);
    node.type = "button";
    return node;
  }

  function factRow({ label, date, place, links }, partners = []) {
    const row = element("div", "fact");
    row.append(element("dt", "", label));
    const value = element("dd");
    const detail = [date, place, ...partners].filter(Boolean).join(" · ");
    if (detail) value.append(element("p", "", detail));
    links.forEach((url, index) => {
      const link = element("a", "", links.length === 1 ? "Registers entry" : `Registers entry ${index + 1}`);
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      value.append(link);
    });
    row.append(value);
    return row;
  }

  function openFacts(id, source) {
    const person = data.people[id];
    const rows = person.facts.map((fact) => factRow(fact));
    for (const union of marriages.get(id) ?? []) {
      rows.push(...union.facts.map((fact) => factRow(fact, union.names)));
    }
    dialog.querySelector("#facts-title").textContent = person.name;
    dialog.querySelector("#facts-id").textContent = `#${id}`;
    if (rows.length) {
      const list = element("dl");
      list.replaceChildren(...rows);
      facts.replaceChildren(list);
    } else {
      facts.replaceChildren(element("p", "", "No facts recorded."));
    }
    opener = source;
    dialog.showModal();
    facts.scrollTop = 0;
  }

  dialog.querySelector("#close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => opener?.focus({ preventScroll: true }));

  function personCard(id, role, starting = false) {
    const person = data.people[id];
    const card = button(starting ? "card start" : "card");
    card.dataset.personId = id;
    if (person.sex) card.dataset.sex = person.sex;
    card.setAttribute("aria-haspopup", "dialog");
    if (role) card.append(element("span", "role", role));
    const identity = element("span", "identity");
    const sex = person.sex === "male" ? " M" : person.sex === "female" ? " F" : "";
    identity.append(element("span", "name", person.name), element("span", "id", `#${id}${sex}`));
    card.append(identity);
    for (const text of [person.birth, person.death]) {
      if (text) card.append(element("span", "vital", text));
    }
    card.addEventListener("click", () => openFacts(id, card));
    return card;
  }

  function parentLinks(node) {
    const lineage = new Set();
    for (let member = node; member; member = member.child) lineage.add(member.id);
    return (data.ancestry[node.id] ?? []).map((id) => ({
      id, members: data.families[id].members.filter((member) => !lineage.has(member.id)),
    })).filter((family) => family.members.length);
  }

  function makeBranch(id, role, child = null) {
    const branch = element("div", "branch");
    const column = element("div", "column");
    const occurrence = element("div", "occurrence");
    const card = personCard(id, role, child === null);
    occurrence.append(card);
    column.append(occurrence);
    const ancestors = element("div", "ancestors");
    ancestors.hidden = true;
    branch.append(column, ancestors);
    const node = { id, child, branch, column, card, ancestors, parents: [] };
    node.families = parentLinks(node);
    if (node.families.length) {
      const toggle = button("expand", "Expand branch");
      toggle.setAttribute("aria-label", `Expand branch for ${data.people[id].name}`);
      toggle.setAttribute("aria-expanded", "false");
      toggle.addEventListener("click", () => {
        const before = toggle.getBoundingClientRect();
        setOpen(node, node.ancestors.hidden);
        redraw();
        const after = toggle.getBoundingClientRect();
        viewport.scrollBy(after.x - before.x, after.y - before.y);
      });
      occurrence.append(toggle);
      node.toggle = toggle;
    }
    if (child) connections.push({ child: child.card, parent: card });
    return node;
  }

  function setOpen(node, open) {
    if (!node.toggle) return;
    if (open && !node.parents.length) {
      for (const family of node.families) {
        const group = element("div", "parent-family");
        if (node.families.length > 1) group.append(element("p", "role", `Parent family #${family.id}`));
        for (const { id, role } of family.members) {
          const parent = makeBranch(id, role, node);
          node.parents.push(parent);
          group.append(parent.branch);
        }
        node.ancestors.append(group);
      }
    }
    node.ancestors.hidden = !open;
    node.toggle.textContent = open ? "Collapse branch" : "Expand branch";
    node.toggle.setAttribute("aria-label", `${node.toggle.textContent} for ${data.people[node.id].name}`);
    node.toggle.setAttribute("aria-expanded", String(open));
  }

  const root = makeBranch(data.root, "Starting person");
  for (const { id, role } of data.relatives) root.column.append(personCard(id, role));
  graph.append(root.branch);
  setOpen(root, true);
  root.parents.forEach((parent) => setOpen(parent, true));

  function redraw() {
    const width = graph.offsetWidth;
    const height = graph.offsetHeight;
    surface.style.width = `${Math.ceil(width * scale)}px`;
    surface.style.height = `${Math.ceil(height * scale)}px`;
    graph.style.transform = `scale(${scale})`;
    lines.setAttribute("width", width);
    lines.setAttribute("height", height);
    const origin = graph.getBoundingClientRect();
    const segments = [];
    for (const { child, parent } of connections) {
      if (!parent.getClientRects().length) continue;
      const from = child.getBoundingClientRect();
      const to = parent.getBoundingClientRect();
      const x1 = (from.right - origin.left) / scale;
      const x2 = (to.left - origin.left) / scale;
      const y1 = (from.top + from.height / 2 - origin.top) / scale;
      const y2 = (to.top + to.height / 2 - origin.top) / scale;
      const joint = (x1 + x2) / 2;
      segments.push(`M ${x1} ${y1} C ${joint} ${y1}, ${joint} ${y2}, ${x2} ${y2}`);
    }
    const path = document.createElementNS(svg, "path");
    path.setAttribute("d", segments.join(" "));
    lines.replaceChildren(path);
    zoomLevel.value = `${Math.round(scale * 100)}%`;
  }

  function center() {
    const card = root.card.getBoundingClientRect();
    const pane = viewport.getBoundingClientRect();
    viewport.scrollBy(card.left + card.width / 2 - pane.left - viewport.clientWidth / 2,
      card.top + card.height / 2 - pane.top - viewport.clientHeight / 2);
  }

  function zoom(next) {
    const halfWidth = viewport.clientWidth / 2;
    const halfHeight = viewport.clientHeight / 2;
    const x = (viewport.scrollLeft + halfWidth) / scale;
    const y = (viewport.scrollTop + halfHeight) / scale;
    scale = next;
    redraw();
    viewport.scrollTo(x * scale - halfWidth, y * scale - halfHeight);
  }

  document.querySelector("#center").addEventListener("click", center);
  document.querySelector("#zoom-out").addEventListener("click", () => {
    if (scale > 0.2) zoom(Math.max(0.2, scale / 1.25));
  });
  document.querySelector("#zoom-in").addEventListener("click", () => zoom(Math.min(2, scale * 1.25)));
  document.querySelector("#fit").addEventListener("click", () => {
    zoom(Math.min(1, viewport.clientWidth / graph.offsetWidth, viewport.clientHeight / graph.offsetHeight));
  });

  let pan;
  viewport.addEventListener("pointerdown", (event) => {
    if (event.pointerType !== "mouse" || event.button !== 0 || event.target.closest("button")) return;
    pan = { x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop };
    viewport.setPointerCapture(event.pointerId);
    viewport.classList.add("dragging");
  });
  viewport.addEventListener("pointermove", (event) => {
    if (pan) viewport.scrollTo(pan.left + pan.x - event.clientX, pan.top + pan.y - event.clientY);
  });
  viewport.addEventListener("lostpointercapture", () => {
    pan = null;
    viewport.classList.remove("dragging");
  });
  window.addEventListener("resize", redraw);
  redraw();
  center();
})();
