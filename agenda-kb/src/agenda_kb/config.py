"""Single source of truth for every tuneable value and every agent-facing string.

Nothing else in this package defines a path, a weight, a limit, or a tool description
inline. To change what the agent sees or how retrieval behaves, change it here.
"""

import os
from pathlib import Path

# --- Storage -----------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Where agenda docs live. A manager "submits" a doc by adding a Markdown file here.
KB_DIR = PROJECT_ROOT / "kb" / "agenda"

DOC_GLOB = "*.md"

#: Frontmatter a doc must have to be loadable. Docs missing any of these are skipped
#: with a warning rather than crashing the server — one malformed submission must not
#: take the whole knowledge base offline.
REQUIRED_FIELDS = (
    "id",
    "title",
    "project",
    "department",
    "team",
    "owner",
    "status",
    "period",
    "updated",
)

# --- MongoDB backend ---------------------------------------------------------------

#: Which backend ``create_store()`` builds. "markdown" reads ``kb/agenda/`` directly;
#: "mongo" reads the projection that ``agenda-kb-ingest`` writes. Environment-driven so
#: that switching backends is a deployment decision, never a code change.
STORE_BACKEND = os.environ.get("AGENDA_STORE_BACKEND", "markdown").strip().lower()

#: Connection string. Defaults to the local ``docker-compose.yml`` instance. Point this at
#: a real cluster — Atlas included — and nothing else in the package changes.
MONGO_URI = os.environ.get("AGENDA_MONGO_URI", "mongodb://127.0.0.1:27018")
MONGO_DB = os.environ.get("AGENDA_MONGO_DB", "agenda_kb")
MONGO_COLLECTION = os.environ.get("AGENDA_MONGO_COLLECTION", "agenda_docs")

#: Deliberately short. A KB whose database is not running should say so in a second, not
#: hang for thirty while pymongo hunts for a primary.
MONGO_TIMEOUT_MS = 3_000

#: Index names, pinned so ingest is idempotent instead of accumulating indexes.
MONGO_LOOKUP_INDEX = "agenda_lookup_keys"
MONGO_TEXT_INDEX = "agenda_text"

#: Field weights for MongoDB's own $text index. This index is **not** used by this
#: package — retrieval goes through ``search.py`` so that ranking cannot change with the
#: backend. It exists for the RAG system that queries the collection directly and should
#: not have to reimplement scoring. Mongo requires positive integers, so these are
#: FIELD_WEIGHTS rounded, and ``_id`` is absent because Mongo forbids indexing it as text
#: — which is why each document carries its id in a plain ``id`` field as well.
MONGO_TEXT_WEIGHTS = {
    "id": 10,
    "aliases": 10,
    "project": 8,
    "title": 8,
    "team": 6,
    "department": 6,
    "owner": 5,
    "tags": 4,
    "body": 1,
}

# --- Retrieval ---------------------------------------------------------------------

#: How much a query-token match in each field contributes to a doc's score.
#: Frontmatter identifies a doc; the body merely mentions things. That gap is the
#: whole reason "checkout" finds the Checkout team rather than everyone who depends
#: on them.
FIELD_WEIGHTS = {
    "id": 10.0,
    "aliases": 10.0,
    "project": 8.0,
    "title": 8.0,
    "team": 6.0,
    "department": 6.0,
    "owner": 5.0,
    "tags": 4.0,
    "period": 2.0,
    "status": 1.0,
    "body": 1.0,
}

#: Awarded when an exact identifying phrase ("product 1", "atlas checkout") appears
#: verbatim in the query. Multi-word names are the strongest signal available.
PHRASE_MATCH_BONUS = 12.0

#: Applied last, so a current agenda outranks a superseded one that is otherwise
#: an equally good textual match.
STATUS_MULTIPLIERS = {
    "active": 1.0,
    "draft": 0.85,
    "archived": 0.6,
}
DEFAULT_STATUS_MULTIPLIER = 0.8

#: Re-read the KB before answering. The knowledge base is a handful of small files, so
#: the read costs microseconds — and it means a manager who submits an agenda doc sees it
#: served immediately, with no server restart. Set False if the KB ever grows large enough
#: for that to matter.
RELOAD_BEFORE_EVERY_QUERY = True

#: When the runner-up scores at least this fraction of the winner, the query did not
#: actually identify one doc. Saying so beats handing over a winner the agent will trust.
CLOSE_MATCH_RATIO = 0.85

DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 25

#: Length of the one-line gist shown in listings and search results.
SUMMARY_MAX_CHARS = 220

#: Words carried by almost every question, so they say nothing about which doc is wanted.
STOPWORDS = frozenset(
    """
    a an and are as at be by can do does for from has have how i in is it its me
    of on or our that the their them there they this to us was we what when where
    which who whose why will with you your about tell show give need want get
    """.split()
)

# --- Embeddings and semantic retrieval ---------------------------------------------

#: bge-m3. Chosen for a corpus of prose agenda docs: multilingual, 8k context so a whole
#: section fits in one vector, and it runs locally with no API key and no per-call cost.
EMBEDDING_MODEL = os.environ.get("AGENDA_EMBEDDING_MODEL", "BAAI/bge-m3")

#: bge-m3's dense head. Pinned here because a collection of vectors is only comparable
#: within one model and one dimensionality — stored rows carry both, and rows that
#: disagree are skipped rather than silently ranked against the wrong geometry.
EMBEDDING_DIMENSIONS = 1024

#: bge-m3 needs **no** instruction prefix on queries, unlike bge-v1.5, which wanted
#: "Represent this sentence for searching relevant passages:". Left configurable because
#: it is the first thing to change if the model is swapped.
EMBEDDING_QUERY_PREFIX = ""

#: Normalized vectors mean cosine similarity is a plain dot product.
EMBEDDING_NORMALIZE = True

EMBEDDING_BATCH_SIZE = 8

#: Loading the model makes sentence-transformers, transformers, and huggingface_hub emit
#: dozens of INFO lines, including one per HTTP cache check. This server speaks JSON-RPC
#: over stdio, so its stderr is the only channel a human reads — drowning it in cache
#: chatter on the first query is a real cost. Raise these loggers to WARNING unless
#: something is being debugged.
EMBEDDING_QUIET_DEPENDENCY_LOGS = True

EMBEDDING_NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "huggingface_hub",
    "sentence_transformers",
    "transformers",
    "filelock",
    "urllib3",
)

#: None lets sentence-transformers choose (MPS on Apple silicon, else CPU).
EMBEDDING_DEVICE = os.environ.get("AGENDA_EMBEDDING_DEVICE") or None

#: Section-level chunks live in their own collection, one document per section. A whole-doc
#: vector averages five objectives into one centroid and loses "Explicitly not doing"
#: entirely — the section a reader most needs when asking what a team is *not* doing.
VECTOR_COLLECTION = os.environ.get("AGENDA_VECTOR_COLLECTION", "agenda_chunks")
VECTOR_INDEX = "agenda_vector"
VECTOR_SIMILARITY = "cosine"

#: How many candidates $vectorSearch scans per requested result. Atlas wants this well
#: above `limit`; 10x is their guidance for small collections.
VECTOR_CANDIDATE_FACTOR = 10

#: Docs are found via their best-matching section, so ask for several sections per doc.
SEMANTIC_DOC_FANOUT = 4

DEFAULT_SEMANTIC_LIMIT = 5

#: Reciprocal Rank Fusion. Each retriever contributes weight/(RRF_K + rank), so fusion
#: uses only the *order* each retriever produced — never its scores, which are on
#: incomparable scales (weighted token counts in the tens against cosine in [0, 1]).
#: Smaller k sharpens the gap between rank 1 and rank 2; 60 is the value from the original
#: RRF paper and the one Atlas documents.
RRF_K = 60

#: Lexical owns identity — an owner's name or a doc alias is a lookup, not a search.
#: Semantic owns paraphrase. Equal weights measured 29/32 hit@1 on the eval set against
#: 28/32 lexical-only and 27/32 semantic-only, so fusion earns its place.
#:
#: A 1.0 / 0.7 tilt toward lexical measured 30/32 — but that is a single query on a 32-query
#: set, which is noise, and tuning weights on the only eval set available is how you end up
#: with numbers that do not survive contact with the next twenty queries. Equal weights is
#: the principled default; the tilt is one config change away if a larger eval set backs it.
RRF_LEXICAL_WEIGHT = 1.0
RRF_SEMANTIC_WEIGHT = 1.0

#: Which retriever ``create_store()`` puts behind the MCP search tool.
#: "lexical" needs nothing. "hybrid" needs the embeddings extra, a Mongo chunk collection,
#: and an `agenda-kb-ingest --embed` run.
RETRIEVAL_MODE = os.environ.get("AGENDA_RETRIEVAL", "lexical").strip().lower()

#: Split on second-level headings: in an agenda doc those are Mission, Agenda for the
#: period, Success metrics, Dependencies, and Explicitly not doing — each a coherent unit.
CHUNK_HEADING_PREFIX = "## "

#: A section shorter than this is a title line or a stub, not something worth a vector.
CHUNK_MIN_CHARS = 40

#: Above this length a section is split further, on its own list items. "Agenda for the
#: period" runs ~1600 characters against ~600 for every other section, so a single vector
#: for it averages five unrelated objectives — and measurably made that one chunk the
#: nearest neighbour for almost every query.
CHUNK_LONG_SECTION_CHARS = 700

#: A top-level list item: "1. ", "- ", "* ". Continuation lines are indented, so they stay
#: with the item they belong to.
CHUNK_ITEM_PATTERN = r"^ {0,3}(?:\d+\.|[-*])\s+"

#: What actually gets embedded. A section lifted out of its doc must still say which
#: product it belongs to, or the vector for "Dependencies" is about nothing at all.
CHUNK_CONTEXT_TEMPLATE = "{title} — {heading}\n\n{text}"

# --- MCP surface -------------------------------------------------------------------

SERVER_NAME = "agenda-kb"

SERVER_INSTRUCTIONS = """
This server is the company's knowledge base of agenda docs — the documents in which
each department and product team states what it is aiming for in the current period.

Use it whenever a question concerns a specific company project, product, department, or
team: what they are trying to achieve, what they are measured on, what they depend on, or
what they have explicitly ruled out.

Typical flow: call find_agenda_doc with the project or team the conversation is about,
then call get_agenda_doc on the top match to read it in full before answering. Answer from
the doc's own words; if no doc matches, say so rather than inferring an agenda.
""".strip()

LIST_TOOL_DESCRIPTION = """
List every agenda doc in the knowledge base as a compact catalogue: id, title, project,
department, team, owner, status, period, and a one-line summary.

Use this to see what the company has agenda docs for, or when you need to choose between
projects yourself. If you already know which project the conversation is about, prefer
find_agenda_doc.
""".strip()

FIND_TOOL_DESCRIPTION = """
Find the agenda docs most relevant to a project, product, department, team, manager, or
topic, ranked best-first. This is the tool to reach for when you need "what is <project>
aiming for".

Pass the words a person would actually use — "product 1", "the checkout team", "Atlas",
"infrastructure", "Dana's org". Matching covers doc ids, project names, aliases,
departments, teams, owners, tags, and doc bodies.

Returns matches with a relevance score and the fields that matched, but not the full
document text. Call get_agenda_doc on the id you want in order to read it.
""".strip()

GET_TOOL_DESCRIPTION = """
Retrieve one agenda doc in full, by its id or by any alias it declares.

Returns the complete document — mission, objectives, success metrics, dependencies, and
what the team has explicitly ruled out — so answer from this rather than from the summary
in a search result.
""".strip()

#: Shown instead of the usual "read the top match" line when scores are too close to
#: call. The agent is told to choose deliberately or ask, never to guess between teams.
AMBIGUOUS_MATCH_NOTICE = """
These matches scored too closely to separate — the query is **ambiguous** and did not
identify a single doc. Do not assume the first one. Pick the doc whose project, team, or
department fits the conversation, or ask which one is meant, then call get_agenda_doc.
""".strip()

RESOURCE_URI_TEMPLATE = "agenda://{doc_id}"
