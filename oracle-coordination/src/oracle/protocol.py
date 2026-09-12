"""Versioned wire contracts. Authority and visibility are enforced by the server."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Visibility(StrEnum):
    ORACLE_ONLY = "ORACLE_ONLY"
    OWNER_ONLY = "OWNER_ONLY"
    PROJECT = "PROJECT"
    TEAM = "TEAM"
    DEPENDENCY_CONSUMERS = "DEPENDENCY_CONSUMERS"
    PUBLIC_CONTRACT = "PUBLIC_CONTRACT"


class Authority(StrEnum):
    HUMAN_DECISION = "human_decision"
    PROJECT_SPEC = "project_spec"
    ARCHITECTURE_DECISION = "architecture_decision"
    PUBLIC_CONTRACT = "public_contract"
    IMPLEMENTED_CONVENTION = "implemented_convention"
    HUMAN_PROMPT = "human_prompt"
    ORACLE_ARBITRATION = "oracle_arbitration"
    AGENT_DECISION = "agent_decision"
    AGENT_ASSUMPTION = "agent_assumption"
    ORACLE_INFERENCE = "oracle_inference"


AUTHORITY_ORDER = list(Authority)
AUTHORITATIVE = set(AUTHORITY_ORDER[:5])


class Lifecycle(StrEnum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    DISPUTED = "DISPUTED"
    SUPERSEDED = "SUPERSEDED"
    DEPRECATED = "DEPRECATED"
    INVALID = "INVALID"


class Classification(StrEnum):
    CONSISTENT = "CONSISTENT"
    UNDERSPECIFIED = "UNDERSPECIFIED"
    CONFLICTING = "CONFLICTING"
    IRRELEVANT = "IRRELEVANT"
    STALE = "STALE"


class EventType(StrEnum):
    SESSION_REGISTER = "SESSION_REGISTER"
    SESSION_CLOSE = "SESSION_CLOSE"
    HEARTBEAT = "HEARTBEAT"
    CHECKPOINT = "CHECKPOINT"
    TASK_UPDATE = "TASK_UPDATE"
    ASSUMPTION_UPDATE = "ASSUMPTION_UPDATE"
    DECISION_UPDATE = "DECISION_UPDATE"
    INTERFACE_UPDATE = "INTERFACE_UPDATE"
    QUESTION_UPDATE = "QUESTION_UPDATE"
    GATE_REQUEST = "GATE_REQUEST"
    GATE_RESPONSE = "GATE_RESPONSE"
    PAUSE = "PAUSE"
    PAUSE_ACK = "PAUSE_ACK"
    CLARIFICATION_REQUEST = "CLARIFICATION_REQUEST"
    CLARIFICATION_RESPONSE = "CLARIFICATION_RESPONSE"
    MEDIATION_PROPOSAL = "MEDIATION_PROPOSAL"
    MEDIATION_RESPONSE = "MEDIATION_RESPONSE"
    NEGOTIATION_MESSAGE = "NEGOTIATION_MESSAGE"
    FINAL_DECISION = "FINAL_DECISION"
    DECISION_ACK = "DECISION_ACK"
    RESUME = "RESUME"
    RESUME_REQUEST = "RESUME_REQUEST"
    ERROR = "ERROR"


class Envelope(Model):
    protocol_version: Literal[1] = 1
    message_id: str = Field(default_factory=lambda: uid("msg"), min_length=1, max_length=160)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sender: str = Field(min_length=1, max_length=160)
    recipient: str = "oracle"
    project_id: str = Field(min_length=1, max_length=160)
    session_id: str | None = None
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def aware_timestamp(cls, value):
        if value.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return value


class Registration(Model):
    project_id: str
    repository: str
    human_id: str
    machine_id: str
    agent_type: str
    agent_session_id: str
    task: str = ""
    interests: list[str] = Field(default_factory=list, max_length=100)
    pause_capability: Literal["cooperative", "supervised"] = "cooperative"


class Session(Model):
    session_id: str
    project_id: str
    human_id: str
    repository: str
    machine_id: str
    agent_type: str
    agent_session_id: str
    task: str = ""
    interests: list[str] = Field(default_factory=list)
    connectivity: Literal["ONLINE", "STALE", "DISCONNECTED", "CLOSED"] = "ONLINE"
    work_state: Literal["RUNNING", "PAUSE_REQUESTED", "PAUSED", "RESOLUTION_PENDING"] = "RUNNING"
    pause_capability: Literal["cooperative", "supervised"] = "cooperative"
    last_seen: str = Field(default_factory=now)
    last_checkpoint_at: str | None = None


class FactInput(Model):
    subject: str = Field(min_length=1, max_length=240)
    predicate: str = Field(min_length=1, max_length=160)
    value: Any
    source: Authority = Authority.AGENT_ASSUMPTION
    source_id: str = Field(default="", max_length=240)
    confidence: float = Field(default=0.6, ge=0, le=1)
    visibility: Visibility = Visibility.PROJECT
    consumer_projects: list[str] = Field(default_factory=list, max_length=100)
    evidence: list[str] = Field(default_factory=list, max_length=30)
    lifecycle: Lifecycle = Lifecycle.ACTIVE


class KnowledgeFact(FactInput):
    fact_id: str
    project_id: str
    owner_id: str
    session_id: str | None = None
    message_id: str
    timestamp: str = Field(default_factory=now)
    supersedes: list[str] = Field(default_factory=list)


class InterfaceReport(Model):
    name: str = Field(min_length=1, max_length=240)
    role: Literal["PRODUCES", "CONSUMES", "IMPLEMENTS"]
    contract: dict[str, Any] = Field(default_factory=dict)
    visibility: Visibility = Visibility.PROJECT
    consumer_projects: list[str] = Field(default_factory=list, max_length=100)
    evidence: list[str] = Field(default_factory=list, max_length=30)


class GitState(Model):
    branch: str | None = None
    base: str | None = None
    head: str | None = None
    dirty: bool = False


class FileChanges(Model):
    read: list[str] = Field(default_factory=list, max_length=200)
    modified: list[str] = Field(default_factory=list, max_length=200)
    planned: list[str] = Field(default_factory=list, max_length=200)


class SymbolChanges(Model):
    read: list[str] = Field(default_factory=list)
    created: list[str] = Field(default_factory=list)
    modified: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)


class ContextPacket(Model):
    session_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    task: str = Field(default="", max_length=4000)
    human_intent: str = Field(default="", max_length=4000)
    agent_interpretation: str = Field(default="", max_length=4000)
    implementation_plan: str = Field(default="", max_length=4000)
    current_implementation: str = Field(default="", max_length=4000)
    progress: dict[str, list[str]] = Field(default_factory=dict)
    projects_touched: list[str] = Field(default_factory=list)
    files: FileChanges = Field(default_factory=FileChanges)
    symbols: SymbolChanges = Field(default_factory=SymbolChanges)
    interfaces: list[InterfaceReport] = Field(default_factory=list, max_length=50)
    schemas: list[FactInput] = Field(default_factory=list, max_length=100)
    dependencies: list[str] = Field(default_factory=list, max_length=100)
    assumptions: list[FactInput] = Field(default_factory=list, max_length=100)
    decisions: list[FactInput] = Field(default_factory=list, max_length=100)
    questions: list[str] = Field(default_factory=list, max_length=30)
    tests: dict[str, list[str]] = Field(default_factory=dict)
    git: GitState = Field(default_factory=GitState)
    summary: str = Field(default="", max_length=4000)


class Question(Model):
    question_id: str
    project_id: str
    subject: str
    predicate: str
    question: str
    status: Literal["OPEN", "ANSWERED", "CANCELED"] = "OPEN"
    blocking: bool = True
    authority_required: str = "project_owner"
    affected_sessions: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    decision_id: str | None = None
    # Validated model output for the authorized human; never shown to coding sessions.
    suggestion: dict[str, Any] | None = None
    assessment: dict[str, Any] | None = None
    created_at: str = Field(default_factory=now)


class ConflictType(StrEnum):
    INTERFACE_MISMATCH = "INTERFACE_MISMATCH"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    BEHAVIOR_MISMATCH = "BEHAVIOR_MISMATCH"
    ASSUMPTION_CONFLICT = "ASSUMPTION_CONFLICT"
    DEPENDENCY_CONFLICT = "DEPENDENCY_CONFLICT"
    ARCHITECTURAL_CONFLICT = "ARCHITECTURAL_CONFLICT"
    SPEC_VIOLATION = "SPEC_VIOLATION"
    OWNERSHIP_CONFLICT = "OWNERSHIP_CONFLICT"
    IMPLEMENTATION_CONFLICT = "IMPLEMENTATION_CONFLICT"
    SECURITY_CONFLICT = "SECURITY_CONFLICT"
    SEMANTIC_CONFLICT = "SEMANTIC_CONFLICT"


class Conflict(Model):
    conflict_id: str
    project_id: str
    subject: str
    predicate: str
    type: ConflictType = ConflictType.SPEC_VIOLATION
    state: str = "PAUSE_REQUESTED"
    summary: str
    fact_ids: list[str]
    affected_sessions: list[str]
    pause_acks: list[str] = Field(default_factory=list)
    round: int = 0
    responses: dict[str, dict[str, Any]] = Field(default_factory=dict)
    proposal: dict[str, Any] | None = None
    # Validated model output awaiting an authorized actor; it has no authority of its own.
    suggested_proposal: dict[str, Any] | None = None
    assessment: dict[str, Any] | None = None
    decision_id: str | None = None
    created_at: str = Field(default_factory=now)


class Decision(Model):
    decision_id: str
    project_id: str
    subject: str
    predicate: str
    value: Any
    authority: Authority
    author_id: str
    reason: str
    visibility: Visibility
    consumer_projects: list[str] = Field(default_factory=list)
    affected_sessions: list[str] = Field(default_factory=list)
    acknowledgements: list[str] = Field(default_factory=list)
    fact_id: str = ""
    question_id: str | None = None
    conflict_id: str | None = None
    timestamp: str = Field(default_factory=now)


class OracleCommand(Model):
    command_id: str = Field(default_factory=lambda: uid("cmd"))
    type: EventType
    session_id: str
    payload: dict[str, Any]
    timestamp: str = Field(default_factory=now)


class ConflictAssessment(Model):
    classification: Classification
    confidence: float = Field(ge=0, le=1)
    severity: Literal["low", "medium", "high"]
    subject: str
    summary: str = Field(max_length=2000)
    evidence_fact_ids: list[str]
    reason: str = Field(max_length=3000)
    recommended_action: Literal["none", "clarify", "mediate", "review"]


class KnowledgeUpdate(Model):
    facts: list[FactInput] = Field(default_factory=list, max_length=50)
    questions: list[str] = Field(default_factory=list, max_length=20)
    relationships: list[dict[str, str]] = Field(default_factory=list, max_length=30)


class MediationProposal(Model):
    subject: str
    predicate: str
    value: Any
    reason: str = Field(max_length=3000)
    evidence_fact_ids: list[str]
    requires_human: bool = True
    participant_changes: dict[str, str] = Field(default_factory=dict)


class NegotiationResponse(Model):
    conflict_id: str
    position: Literal[
        "ACCEPT", "ACCEPT_WITH_CONCERNS", "PROPOSE_MODIFICATION", "REJECT", "NEED_MORE_CONTEXT", "NO_CONFLICT"
    ]
    reason: str = Field(max_length=2800)
    risks: list[str] = Field(default_factory=list, max_length=10)
    alternative: Any = None


class ClarificationRequest(Model):
    subject: str
    question: str = Field(max_length=1500)
    why_it_matters: str = Field(default="", max_length=1500)
    authority_required: str = "project_owner"
    options: list[str] = Field(default_factory=list, max_length=5)
    recommendation: str | None = None
    recommendation_reason: str = Field(default="", max_length=1500)
    evidence_fact_ids: list[str] = Field(default_factory=list)
    blocking: bool = True


class PropagationInstruction(Model):
    session_id: str
    decision_id: str
    instruction: str = Field(max_length=2000)
    evidence_fact_ids: list[str] = Field(default_factory=list)


class PropagationPlan(Model):
    instructions: list[PropagationInstruction] = Field(default_factory=list, max_length=100)
