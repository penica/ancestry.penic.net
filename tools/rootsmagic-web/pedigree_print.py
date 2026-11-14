"""Render the selected pedigree as a compact, vector A0-roll PDF."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from functools import cache
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Final

from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont, TTFError
from reportlab.pdfgen.canvas import Canvas

if TYPE_CHECKING:
    from pedigree_store import Fact, Pedigree, PedigreeNode, PersonCard

CARD: Final = 98.18 * mm
STEP: Final = 130.91 * mm
MARGIN: Final = 25 * mm
HEIGHT: Final = 841 * mm
TOP: Final = 65 * mm
BOTTOM: Final = 38 * mm
ROW_SPREAD: Final = 19 * mm
BAND_PADDING: Final = 45 * mm
PARTNER_GUTTER: Final = 48 * mm
INK: Final = HexColor("#182e2a")
MUTED: Final = HexColor("#435852")
LINE: Final = HexColor("#879893")
ACCENT: Final = HexColor("#216453")
SEX_STYLE: Final = {
    "male": ("M", HexColor("#496f98")),
    "female": ("F", HexColor("#8b587d")),
}


class PrintLayoutError(ValueError):
    """The recorded column cannot fit the roll at the requested typography."""

    def __init__(self) -> None:
        """Describe how to reduce the printable view without shrinking its text."""
        super().__init__(
            "This pedigree is too tall for an A0 roll at 8 pt. "
            "Collapse some branches or show fewer generations and retry."
        )


class PrintFontError(RuntimeError):
    """The configured PDF font files could not be loaded."""

    def __init__(self) -> None:
        super().__init__(
            "PDF export is unavailable because its fonts could not be loaded."
        )


@cache
def register_fonts() -> None:
    """Embed configured TrueType fonts, with the original macOS defaults."""
    directory = Path("/System/Library/Fonts/Supplemental")
    for name, variable, filename in (
        ("PedigreeArial", "ROOTSMAGIC_FONT_REGULAR", "Arial.ttf"),
        ("PedigreeBold", "ROOTSMAGIC_FONT_BOLD", "Arial Bold.ttf"),
        ("PedigreeTitle", "ROOTSMAGIC_FONT_TITLE", "Georgia.ttf"),
    ):
        path = Path(os.environ.get(variable) or directory / filename).expanduser()
        try:
            pdfmetrics.registerFont(TTFont(name, str(path)))
        except (OSError, TTFError) as error:
            raise PrintFontError from error


def wrap(text: str, width: float, font: tuple[str, float]) -> tuple[str, ...]:
    """Wrap words and oversized individual tokens without dropping any text."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and pdfmetrics.stringWidth(candidate, *font) > width:
            lines.append(current)
            current = ""
        for character in f" {word}" if current else word:
            if current and pdfmetrics.stringWidth(current + character, *font) > width:
                lines.append(current)
                current = ""
            current += character
    if current:
        lines.append(current)
    return tuple(lines)


def fact_text(fact: Fact | None, fallback: str) -> str:
    """Format the recorded date and place with its actual event label."""
    if fact is None:
        return fallback
    return f"{fact.label}: " + " · ".join(
        part for part in (fact.date, fact.place) if part
    )


def card_id_label(person_id: int, sex: str | None) -> str:
    style = SEX_STYLE.get(sex or "")
    return f"#{person_id}  {style[0]}" if style else f"#{person_id}"


def draw_sex_edge(
    canvas: Canvas, x: float, y: float, height: float, radius: float, sex: str | None
) -> None:
    style = SEX_STYLE.get(sex or "")
    if style:
        canvas.setStrokeColor(style[1])
        canvas.setLineWidth(2.3)
        canvas.line(
            x + 1.15, y - height / 2 + radius, x + 1.15, y + height / 2 - radius
        )


@dataclass(frozen=True, slots=True)
class Card:
    """Measured text for a single occurrence, including repeated ancestors."""

    person: PersonCard
    names: tuple[str, ...]
    facts: tuple[str, ...]
    height: float
    highlighted: bool = False

    @classmethod
    def measure(
        cls,
        person: PersonCard,
        *,
        highlighted: bool = False,
        relationship: str = "",
    ) -> Card:
        """Measure wrapped names and vital facts before placing the card."""
        identity_width = pdfmetrics.stringWidth(
            card_id_label(person.id, person.sex), "PedigreeArial", 8
        )
        names = wrap(
            person.name,
            CARD - max(23 * mm, 11 * mm + identity_width),
            ("PedigreeBold", 12.36),
        )
        fact_width = CARD - 8 * mm
        fact_font = ("PedigreeArial", 8.73)
        facts = wrap(
            fact_text(person.birth, "Birth details unknown"), fact_width, fact_font
        )
        if not person.living:
            age = person.age_at_death
            death = fact_text(person.death, "" if age else "Death details unknown")
            if age and person.death and person.death.label == "Died":
                death += f" · aged {age}"
                age = None
            facts += wrap(death, fact_width, fact_font)
            if age:
                facts += wrap(f"Age at death {age}", fact_width, fact_font)
        if relationship:
            facts += (relationship,)
        return cls(
            person,
            names,
            facts,
            max(16 * mm, 14 * len(names) + 11 * len(facts) + 16),
            highlighted,
        )


@dataclass(slots=True)
class Position:
    """Layout accumulator whose coordinates are assigned during column packing."""

    card: Card
    depth: int
    center: float = 0
    parents: tuple[Position, ...] = ()
    marriage: tuple[str, ...] = ()
    gap: float = 4 * mm
    partners: tuple[Position, ...] = ()
    heading: tuple[str, ...] = ()
    partnership: tuple[str, ...] = ()
    horizontal_shift: float = 0

    @property
    def x(self) -> float:
        """Locate a column after reserving its partnership-label gutter."""
        return MARGIN + self.depth * STEP + self.horizontal_shift

    @property
    def heading_height(self) -> float:
        """Reserve space for the family label above the first child card."""
        return len(self.heading) * 11 + 5 if self.heading else 0

    @property
    def height(self) -> float:
        """Measure the complete card block before column packing."""
        return (
            max(self.card.height, len(self.partnership) * 18 + 12) + self.heading_height
        )

    @property
    def card_center(self) -> float:
        """Locate the card itself below its optional family label."""
        return self.center + self.heading_height / 2


def measure_tree(
    tree: PedigreeNode,
    offset: int,
    *,
    relationship: str = "",
) -> tuple[Position, list[Position], int]:
    """Measure one ancestry and give each of its leaves a preferred row."""
    positions: list[Position] = []
    leaves = 0

    def branch(node: PedigreeNode, depth: int) -> Position:
        nonlocal leaves
        parents = tuple(branch(parent, depth + 1) for parent in node.parents)
        center = (
            sum(parent.center for parent in parents) / len(parents)
            if parents
            else leaves
        )
        if not parents:
            leaves += 1
        position = Position(
            Card.measure(
                node.person,
                highlighted=depth == offset and not relationship,
                relationship=relationship if depth == offset else "",
            ),
            depth,
            center,
            parents,
        )
        if node.marriage and len(parents) == 2:
            position.marriage = wrap(
                fact_text(node.marriage, ""),
                STEP - CARD - 9 * mm,
                ("PedigreeArial", 7.27),
            )
            parents[1].gap = max(parents[1].gap, len(position.marriage) * 9 + 6 * mm)
        positions.append(position)
        return position

    root = branch(tree, offset)
    return root, positions, leaves


def measure_children(chart: Pedigree, root: Position) -> list[Position]:
    """Keep each recorded child family together with a measured parent label."""
    if not chart.child_families:
        return [
            Position(
                Card.measure(child, relationship="Child"),
                0,
                root.center + (index - (len(chart.children) - 1) / 2) * 1.2,
                (root,),
            )
            for index, child in enumerate(chart.children)
        ]
    total = sum(len(group.children) for group in chart.child_families)
    result: list[Position] = []
    for group in chart.child_families:
        parent = f"{root.card.person.name} (#{root.card.person.id})"
        parents = (
            f"{parent} and {group.partner.name} (#{group.partner.id})"
            if group.partner
            else parent
        )
        heading = ("Children of",) + wrap(parents, CARD, ("PedigreeArial", 8.73))
        if group.partner is None:
            heading += ("Other parent not recorded",)
        for index, child in enumerate(group.children):
            result.append(
                Position(
                    Card.measure(child, relationship="Child"),
                    0,
                    root.center + (len(result) - (total - 1) / 2) * 1.2,
                    (root,),
                    gap=8 * mm if index == 0 else 4 * mm,
                    heading=heading if index == 0 else (),
                )
            )
    return result


def layout(
    chart: Pedigree,
    *,
    show_children: bool,
    show_partners: bool = False,
    partner_roots: tuple[PedigreeNode, ...] = (),
) -> list[Position]:
    """Pack the main family and expanded partner branches into separate rows."""
    offset = int(show_children and bool(chart.children))
    root, positions, leaves = measure_tree(chart.root, offset)
    partner_people = chart.partners if show_partners else ()
    partner_ids = {person.id for person in partner_people}
    partner_branches = {
        node.person.id: node
        for node in partner_roots
        if node.person.id in partner_ids and node.parents
    }
    groups = [(positions, leaves)]
    partners: list[Position] = []
    for index, person in enumerate(partner_people):
        marriages = chart.partner_marriages.get(person.id, ())
        partner_root = partner_branches.get(person.id)
        if partner_root is not None:
            partner, branch_positions, branch_leaves = measure_tree(
                partner_root, offset, relationship="Partner"
            )
            groups.append((branch_positions, branch_leaves))
        else:
            partner = Position(
                Card.measure(person, relationship="Partner"),
                offset,
                root.center + (index + 1) * 1.2,
            )
            if partner_branches:
                groups.append(([partner], 1))
            else:
                positions.append(partner)
        partner.partnership = tuple(
            line
            for marriage in marriages
            if marriage.date or marriage.place
            for line in wrap(
                fact_text(marriage, ""),
                PARTNER_GUTTER - 6 * mm,
                ("PedigreeArial", 7.27),
            )
        )
        partners.append(partner)
    root.partners = tuple(partners)
    relatives_before_siblings = 0 if partner_branches else len(partners)
    relatives = [
        Position(
            Card.measure(
                sibling.person, highlighted=True, relationship=sibling.relationship
            ),
            offset,
            root.center + (relatives_before_siblings + index + 1) * 1.2,
            root.parents if sibling.shares_parents else (),
        )
        for index, sibling in enumerate(chart.siblings)
    ]
    positions.extend(relatives)
    if offset:
        positions.extend(measure_children(chart, root))
    pack_groups(groups)
    result = [position for group, _ in groups for position in group]
    if any(partner.partnership for partner in partners):
        for position in result:
            if position.depth >= offset:
                position.horizontal_shift = PARTNER_GUTTER
    return result


def pack_groups(groups: list[tuple[list[Position], int]]) -> None:
    """Reserve each tree enough height that unrelated parent lines stay apart."""
    bands: list[tuple[list[list[Position]], float, int]] = []
    for positions, leaves in groups:
        by_depth: dict[int, list[Position]] = {}
        for position in sorted(positions, key=lambda item: item.center):
            by_depth.setdefault(position.depth, []).append(position)
        columns = list(by_depth.values())
        required = max(
            sum(p.height + p.gap for p in column) - column[0].gap for column in columns
        )
        bands.append((columns, required, leaves))
    gap = 8 * mm
    available = HEIGHT - TOP - BOTTOM
    required_total = sum(required for _, required, _ in bands) + gap * (len(bands) - 1)
    extra = available - required_total
    if extra < 0:
        raise PrintLayoutError
    if len(bands) == 1:
        columns, _, leaves = bands[0]
        pack_columns(columns, TOP, HEIGHT - BOTTOM, leaves)
        return
    preferred = [
        max(0, BAND_PADDING + (leaves - 1) * ROW_SPREAD - required)
        for _, required, leaves in bands
    ]
    preferred_total = sum(preferred)
    scale = min(1, extra / preferred_total) if preferred_total else 0
    top = TOP + (extra - preferred_total * scale) / 2
    for (columns, required, leaves), padding in zip(bands, preferred, strict=True):
        bottom = top + required + padding * scale
        pack_columns(columns, top, bottom, leaves)
        top = bottom + gap


def pack_columns(
    columns: list[list[Position]], top: float, bottom: float, leaves: int
) -> None:
    """Keep generation order and room for the marriage labels in one band."""
    spread = min(ROW_SPREAD, max(0, bottom - top - BAND_PADDING) / max(1, leaves - 1))
    for column in columns:
        cursor = top
        for index, position in enumerate(column):
            desired = top + 20 * mm + position.center * spread
            position.center = max(desired, cursor + position.height / 2)
            cursor = position.center + position.height / 2
            next_index = index + 1
            if next_index < len(column):
                cursor += column[next_index].gap
        ceiling = bottom
        for position in reversed(column):
            position.center = min(position.center, ceiling - position.height / 2)
            ceiling = position.center - position.height / 2 - position.gap
    positions = [position for column in columns for position in column]
    first = min(position.center - position.height / 2 for position in positions)
    last = max(position.center + position.height / 2 for position in positions)
    shift = (top + bottom - first - last) / 2
    for position in positions:
        position.center += shift


def draw_card(canvas: Canvas, position: Position) -> None:
    """Paint one card with fixed 8-point RootsMagic ID typography."""
    card = position.card
    x, y = position.x, HEIGHT - position.card_center
    if position.heading:
        cursor = HEIGHT - position.center + position.height / 2 - 9
        canvas.setFillColor(ACCENT)
        canvas.setFont("PedigreeArial", 8.73)
        for line in position.heading:
            canvas.drawString(x, cursor, line)
            cursor -= 11
    canvas.setFillColor(
        HexColor("#edf6f1") if card.highlighted else HexColor("#ffffff")
    )
    canvas.setStrokeColor(ACCENT if card.highlighted else LINE)
    canvas.setLineWidth(0.8 if card.highlighted else 0.6)
    canvas.roundRect(x, y - card.height / 2, CARD, card.height, 2.18 * mm, fill=1)
    draw_sex_edge(canvas, x, y, card.height, 2.18 * mm, card.person.sex)
    cursor = y + card.height / 2 - 14
    canvas.setFillColor(MUTED)
    canvas.setFont("PedigreeArial", 8)
    canvas.drawRightString(
        x + CARD - 4 * mm, cursor, card_id_label(card.person.id, card.person.sex)
    )
    canvas.setFillColor(INK)
    canvas.setFont("PedigreeBold", 12.36)
    for line in card.names:
        canvas.drawString(x + 4 * mm, cursor, line)
        cursor -= 14
    canvas.setFont("PedigreeArial", 8.73)
    for line in card.facts:
        canvas.drawString(x + 4 * mm, cursor, line)
        cursor -= 11


def draw_partner_links(canvas: Canvas, root: Position) -> None:
    """Draw a square dashed bracket with facts beside its uninterrupted spine."""
    x = root.x
    joint = x - (PARTNER_GUTTER if root.horizontal_shift else 5 * mm)
    y = HEIGHT - root.card_center - root.card.height / 4
    canvas.saveState()
    canvas.setStrokeColor(ACCENT)
    canvas.setLineWidth(0.7)
    canvas.setDash(3, 2)
    last_y = HEIGHT - root.partners[-1].card_center
    bracket = canvas.beginPath()
    bracket.moveTo(x, y)
    bracket.lineTo(joint, y)
    bracket.lineTo(joint, last_y)
    bracket.lineTo(x, last_y)
    canvas.drawPath(bracket)
    for partner in root.partners[:-1]:
        partner_y = HEIGHT - partner.card_center
        canvas.line(joint, partner_y, x, partner_y)
    canvas.restoreState()
    previous_y = y
    for partner in root.partners:
        partner_y = HEIGHT - partner.card_center
        label_y = (previous_y + partner_y) / 2
        previous_y = partner_y
        if not partner.partnership:
            continue
        canvas.setFillColor(MUTED)
        canvas.setFont("PedigreeArial", 7.27)
        cursor = label_y + (len(partner.partnership) - 1) * 4.5 - 2.5
        for line in partner.partnership:
            canvas.drawString(joint + 3 * mm, cursor, line)
            cursor -= 9


def render_pedigree_pdf(
    chart: Pedigree,
    *,
    show_children: bool = False,
    show_partners: bool = False,
    partner_roots: tuple[PedigreeNode, ...] = (),
) -> bytes:
    """Print supplied ancestry, including any visible partner branches."""
    register_fonts()
    positions = layout(
        chart,
        show_children=show_children,
        show_partners=show_partners,
        partner_roots=partner_roots,
    )
    width = max(594 * mm, MARGIN + CARD + max(p.x for p in positions))
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=(width, HEIGHT), pageCompression=1)
    canvas.setTitle(f"{chart.root.person.name} | Ancestral pedigree")
    canvas.setSubject("Read-only RootsMagic pedigree snapshot")
    canvas.setFillColor(ACCENT)
    canvas.setFont("PedigreeBold", 11.64)
    canvas.drawString(
        MARGIN, HEIGHT - 16 * mm, f"FAMILY HISTORY  /  {date.today():%B %Y}".upper()
    )
    canvas.setFillColor(INK)
    canvas.setFont("PedigreeTitle", 37.09)
    canvas.drawString(MARGIN, HEIGHT - 33 * mm, chart.root.person.name)
    canvas.setFillColor(MUTED)
    canvas.setFont("PedigreeArial", 12.36)
    canvas.drawString(
        MARGIN,
        HEIGHT - 43 * mm,
        f"Ancestral pedigree · RootsMagic snapshot of {date.today():%d %B %Y}",
    )
    canvas.setStrokeColor(LINE)
    canvas.line(MARGIN, HEIGHT - 48 * mm, width - MARGIN, HEIGHT - 48 * mm)
    canvas.setFont("PedigreeBold", 9.45)
    child_column = int(show_children and bool(chart.children))
    for depth in range(max(p.depth for p in positions) + 1):
        generation = depth - child_column
        label = (
            "CHILDREN"
            if generation < 0
            else ("START", "PARENTS", "GRANDPARENTS")[generation]
            if generation < 3
            else f"{generation - 2}× GREAT-GRANDPARENTS"
        )
        column_x = next(position.x for position in positions if position.depth == depth)
        canvas.drawString(column_x, HEIGHT - 56 * mm, label)
    for position in positions:
        if position.partners:
            draw_partner_links(canvas, position)
        if not position.parents:
            continue
        x = position.x + CARD
        joint = x + 5 * mm
        y = HEIGHT - position.card_center
        ys = [HEIGHT - parent.card_center for parent in position.parents]
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.7)
        canvas.line(x, y, joint, y)
        canvas.line(joint, min(y, *ys), joint, max(y, *ys))
        for parent, parent_y in zip(position.parents, ys, strict=True):
            canvas.line(joint, parent_y, parent.x, parent_y)
        if position.marriage:
            cursor = sum(ys) / len(ys) + (len(position.marriage) - 1) * 4.5
            canvas.setFillColor(MUTED)
            canvas.setFont("PedigreeArial", 7.27)
            for line in position.marriage:
                canvas.drawString(joint + 2 * mm, cursor, line)
                cursor -= 9
    for position in positions:
        draw_card(canvas, position)
    canvas.setStrokeColor(LINE)
    canvas.line(MARGIN, 24 * mm, width - MARGIN, 24 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("PedigreeArial", 8.73)
    canvas.drawString(
        MARGIN,
        18 * mm,
        "Green cards: starting person and siblings. Numbers: RootsMagic IDs. "
        "Lines show recorded parent links. Repeated ancestors retain each position.",
    )
    canvas.drawString(
        MARGIN,
        12 * mm,
        "Siblings are listed beside the starting person; half-siblings may have a different parent. "
        "Branch endings follow the current view."
        + (
            " Dashed lines connect partners."
            if show_partners and chart.partners
            else ""
        ),
    )
    canvas.drawRightString(
        width - MARGIN,
        7 * mm,
        f"{len(positions)} cards · 841 × {width / mm:.0f} mm · Arial IDs: 8 pt · Print at 100%",
    )
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()
