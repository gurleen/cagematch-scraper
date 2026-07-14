"""Small HTML-fragment helpers shared across spiders.

Regex tag-stripping, not a nested `Selector`: parsel's type auto-detection treats a
fragment that happens to parse as valid JSON (e.g. a quoted string like `"Foo's Bar"`)
as JSON, not HTML/text, even when an explicit `type="html"` is passed to `Selector()`.
"""

from __future__ import annotations

import html
import re


def strip_tags(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def br_list(inner_html: str) -> list[str]:
    """Split a `<br>`-separated HTML fragment (e.g. an InformationBoxContents div's
    outer HTML) into plain-text lines."""
    inner_html = re.sub(r"^<div[^>]*>|</div>$", "", inner_html.strip())
    items: list[str] = []
    for fragment in re.split(r"<br\s*/?>", inner_html):
        text = strip_tags(fragment)
        if text:
            items.append(text)
    return items
