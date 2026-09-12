"""A simple queryable knowledge base of company agenda docs."""

from agenda_kb.store import AgendaDoc, AgendaDocNotFound, AgendaStore, SearchResult

__all__ = ["AgendaDoc", "AgendaDocNotFound", "AgendaStore", "SearchResult"]
