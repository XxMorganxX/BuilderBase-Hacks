import textwrap
import pytest

from agenda_kb.store import MarkdownAgendaStore


def write_doc(directory, doc_id, frontmatter, body="Body text."):
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f'  - "{item}"' for item in value)
        else:
            lines.append(f"{key}: {value}")
    lines += ["---", "", body]
    (directory / f"{doc_id}.md").write_text("\n".join(lines))


@pytest.fixture
def kb_dir(tmp_path):
    """A small KB with one product doc and one department doc."""
    write_doc(
        tmp_path,
        "atlas-checkout",
        {
            "id": "atlas-checkout",
            "title": "Atlas Checkout",
            "project": "Atlas",
            "department": "Payments",
            "team": "Checkout Squad",
            "owner": "Dana Okafor",
            "status": "active",
            "period": "2026-H2",
            "aliases": ["product 1", "product #1", "checkout"],
            "tags": ["payments", "conversion"],
            "updated": "2026-08-14",
        },
        body=textwrap.dedent(
            """
            # Atlas Checkout

            Ship one-tap checkout for returning buyers and reduce fraud declines.
            """
        ).strip(),
    )
    write_doc(
        tmp_path,
        "platform-infrastructure",
        {
            "id": "platform-infrastructure",
            "title": "Platform Infrastructure",
            "project": "Platform",
            "department": "Infrastructure",
            "team": "Core Platform",
            "owner": "Marcus Bell",
            "status": "active",
            "period": "2026-H2",
            "aliases": ["infra"],
            "tags": ["reliability"],
            "updated": "2026-08-21",
        },
        body="Stand up EU regional infrastructure and streaming ingestion.",
    )
    return tmp_path


@pytest.fixture
def store(kb_dir):
    return MarkdownAgendaStore(kb_dir)
