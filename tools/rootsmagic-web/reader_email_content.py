"""Render readable HTML mail with a plain-text alternative."""

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from jinja2 import Environment, FileSystemLoader, select_autoescape

EMAIL_TEMPLATES: Final = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent),
    autoescape=select_autoescape(("html",)),
)


@dataclass(frozen=True, slots=True)
class EmailBody:
    text: str
    html: str


def action_email(
    title: str, description: str, label: str, url: str, footer: str
) -> EmailBody:
    """Render an email with one labeled action leading back to the reader."""
    plain_text = f"{description}\n\n{label}: {url}\n\n{footer}"
    html = EMAIL_TEMPLATES.get_template("reader-mail-action.html").render(
        title=title, description=description, label=label, url=url, footer=footer
    )
    return EmailBody(plain_text, html)
