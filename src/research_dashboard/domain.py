from datetime import date, datetime, timezone
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class EpistemicStatus(str, Enum):
    OBSERVED = "Observed"
    DERIVED = "Derived"
    INFERRED = "Inferred"


class RiskType(str, Enum):
    RESEARCH = "Research"
    OPERATIONAL = "Operational"


class Confidence(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


Lifecycle = Literal[
    "Active",
    "Waiting",
    "Paused",
    "Completed",
    "Archived",
    "Needs classification",
]
EvidenceAvailability = Literal["available", "unavailable", "unknown"]
ExecutionState = Literal[
    "SUBMITTED",
    "QUEUED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "UNKNOWN",
]
PlanningActor = Literal["user", "agent"]
RoadmapStatus = Literal[
    "Not started", "In progress", "Waiting", "Blocked", "Done", "Skipped"
]
TodoStatus = Literal["Open", "Done"]
TodoPriority = Literal["High", "Normal", "Low"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class RoadmapItemInput(_DomainModel):
    roadmap_item_id: str | None = Field(default=None, min_length=1)
    project_id: str = Field(min_length=1)
    parent_item_id: str | None = Field(default=None, min_length=1)
    title: str = Field(min_length=1)
    status: RoadmapStatus = "Not started"
    note: str | None = None
    position: int | None = Field(default=None, ge=0)
    source_plan_path: str | None = None
    source_key: str | None = None
    actor: PlanningActor = "user"


class TodoInput(_DomainModel):
    todo_id: str | None = Field(default=None, min_length=1)
    project_id: str = Field(min_length=1)
    roadmap_item_id: str | None = Field(default=None, min_length=1)
    title: str = Field(min_length=1)
    status: TodoStatus = "Open"
    priority: TodoPriority = "Normal"
    due_date: date | None = None
    note: str | None = None
    position: int | None = Field(default=None, ge=0)
    actor: PlanningActor = "user"


class RoadmapProposalAdd(_DomainModel):
    operation: Literal["add"]
    title: str = Field(min_length=1)
    status: RoadmapStatus = "Not started"
    note: str | None = None
    parent_item_id: str | None = None


class RoadmapProposalRemove(_DomainModel):
    operation: Literal["remove"]
    roadmap_item_id: str = Field(min_length=1)


class RoadmapProposalReorder(_DomainModel):
    operation: Literal["reorder"]
    parent_item_id: str | None = None
    ordered_item_ids: list[str] = Field(min_length=1)


RoadmapProposalOperation = Annotated[
    RoadmapProposalAdd | RoadmapProposalRemove | RoadmapProposalReorder,
    Field(discriminator="operation"),
]


class RoadmapProposalBatchInput(_DomainModel):
    project_id: str = Field(min_length=1)
    source_plan_path: str = Field(min_length=1)
    source_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposals: list[RoadmapProposalOperation] = Field(min_length=1)


class RoadmapAddOperation(_DomainModel):
    operation: Literal["roadmap_add"]
    title: str = Field(min_length=1)
    status: RoadmapStatus = "Not started"
    note: str | None = None
    parent_item_id: str | None = None


class RoadmapUpdateOperation(_DomainModel):
    operation: Literal["roadmap_update"]
    roadmap_item_id: str = Field(min_length=1)
    title: str | None = None
    status: RoadmapStatus | None = None
    note: str | None = None


class TodoAddOperation(_DomainModel):
    operation: Literal["todo_add"]
    title: str = Field(min_length=1)
    priority: TodoPriority = "Normal"
    due_date: date | None = None
    note: str | None = None
    roadmap_item_id: str | None = None


class TodoUpdateOperation(_DomainModel):
    operation: Literal["todo_update"]
    todo_id: str = Field(min_length=1)
    status: TodoStatus | None = None
    priority: TodoPriority | None = None
    due_date: date | None = None
    note: str | None = None
    roadmap_item_id: str | None = None


class ProposalBatchOperation(_DomainModel):
    operation: Literal["proposal_batch"]
    source_plan_path: str = Field(min_length=1)
    source_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposals: list[RoadmapProposalOperation] = Field(min_length=1)


PlanningMutationOperation = Annotated[
    RoadmapAddOperation
    | RoadmapUpdateOperation
    | TodoAddOperation
    | TodoUpdateOperation
    | ProposalBatchOperation,
    Field(discriminator="operation"),
]


class PlanningMutationBatch(_DomainModel):
    project_id: str = Field(min_length=1)
    operations: list[PlanningMutationOperation] = Field(min_length=1)


class EvidenceInput(_DomainModel):
    evidence_type: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    authority: int = Field(ge=0)
    observed_at: AwareDatetime | None = None
    availability: EvidenceAvailability = "available"


class RiskInput(_DomainModel):
    risk_id: str | None = Field(default=None, min_length=1)
    risk_key: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    workstream: str | None = None
    task_key: str | None = None
    risk_type: RiskType
    severity: str = Field(min_length=1)
    title: str = Field(min_length=1)
    current_status: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)
    originating_event_id: UUID
    created_at: AwareDatetime = Field(default_factory=_utc_now)
    updated_at: AwareDatetime = Field(default_factory=_utc_now)


class SemanticEventInput(_DomainModel):
    event_id: UUID
    project_id: str = Field(min_length=1)
    workstream: str | None = None
    task_key: str | None = None
    event_type: str = Field(min_length=1)
    previous_state: str | None = None
    new_state: str | None = None
    importance: str = Field(min_length=1)
    risk_type: RiskType | None = None
    risk_severity: str | None = Field(default=None, min_length=1)
    epistemic_status: EpistemicStatus
    context: str = Field(min_length=1)
    what_changed: str = Field(min_length=1)
    cause: str | None = None
    impact: str | None = None
    next_action: str | None = None
    confidence: Confidence | None = None
    governing_plan_path: str | None = None
    source_agent: str | None = Field(default=None, min_length=1)
    source_session: str | None = Field(default=None, min_length=1)
    observed_at: AwareDatetime
    ingested_at: AwareDatetime = Field(default_factory=_utc_now)
    corrects_event_id: UUID | None = None
    evidence: list[EvidenceInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_event_semantics(self) -> "SemanticEventInput":
        event_type = self.event_type.casefold().replace("-", "_").replace(" ", "_")
        if event_type == "state_change" and not self.new_state:
            raise ValueError("new_state is required for state_change events")
        if event_type == "risk" and self.risk_type is None:
            raise ValueError("risk_type is required for risk events")
        return self


class ProjectInput(_DomainModel):
    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    context_path: str | None = None
    lifecycle: Lifecycle = "Needs classification"
    update_horizon_minutes: int | None = Field(default=None, ge=0)
    created_at: AwareDatetime = Field(default_factory=_utc_now)
    updated_at: AwareDatetime = Field(default_factory=_utc_now)


class ExecutionInput(_DomainModel):
    execution_id: str = Field(min_length=1)
    backend: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    current_state: ExecutionState = "SUBMITTED"
    project_id: str | None = Field(default=None, min_length=1)
    workstream: str | None = Field(default=None, min_length=1)
    task_key: str | None = Field(default=None, min_length=1)
    created_at: AwareDatetime = Field(default_factory=_utc_now)
    last_observed_at: AwareDatetime | None = None
    active: bool = True


class ExecutionObservationInput(_DomainModel):
    observation_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    state: ExecutionState
    observed_at: AwareDatetime = Field(default_factory=_utc_now)
    raw_record: dict[str, object]
