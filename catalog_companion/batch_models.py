"""Typed, closed inputs: clients select catalog IDs, never engine file paths."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bugbounty_catalog.query import ProgramQuery

TARGET_KINDS = ("domains", "ips", "cidrs", "discovery-roots")
RESERVED_PARAMS = {"target", "targets", "target_file", "org_uuid", "workspace", "workspace_name",
                   "authorization", "password", "token", "api_key", "secret"}


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProgramSelection(ClosedModel):
    mode: Literal["ids", "filtered"]
    program_ids: list[int] = Field(default_factory=list, max_length=10000)
    filters: dict = Field(default_factory=dict)
    asset_ids: list[int] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def validate_selection(self):
        if self.mode == "ids" and not self.program_ids:
            raise ValueError("Select at least one program")
        if self.mode == "filtered" and (self.program_ids or self.asset_ids):
            raise ValueError("All-filtered mode cannot also carry selected IDs")
        if any(type(value) is not int or value < 1 for value in self.program_ids + self.asset_ids):
            raise ValueError("IDs must be positive integers")
        if set(self.filters) - {"q", "source", "platform", "scope", "scope_text", "kind", "active", "scannable_only"}:
            raise ValueError("Unknown program filter")
        if any(value is not None and not isinstance(value, (str, bool)) for value in self.filters.values()):
            raise ValueError("Filters must be text or boolean")
        ProgramQuery.from_mapping(self.filters)
        return self


class WorkflowMapping(ClosedModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    kind: Literal["flow", "module"]
    params: dict[str, str] = Field(default_factory=dict, max_length=100)

    @field_validator("params")
    @classmethod
    def controlled_params(cls, value):
        if any(key.strip().lower() in RESERVED_PARAMS for key in value):
            raise ValueError("Targets, organization and credentials cannot be workflow parameters")
        if any(len(key) > 128 or len(item) > 4096 for key, item in value.items()):
            raise ValueError("Workflow parameter too large")
        return value


class BatchPreview(ClosedModel):
    selection: ProgramSelection
    workflows: dict[str, WorkflowMapping | None] | None = None
    scan_mode: Literal["automatic", "full"] | None = None
    org_uuid: UUID
    parallelism: int = Field(default=1, ge=1, le=3)
    target_concurrency: int = Field(default=1, ge=1, le=10)
    convert_wildcards: bool = False
    acknowledge_wildcards: bool = False

    @model_validator(mode="after")
    def mappings(self):
        if bool(self.scan_mode) == (self.workflows is not None):
            raise ValueError("Supply either scan_mode or manual workflows, not both")
        if set(self.workflows or {}) - set(TARGET_KINDS):
            raise ValueError("Unknown input category")
        if self.scan_mode and "convert_wildcards" not in self.model_fields_set:
            self.convert_wildcards = True
        if not self.scan_mode and self.convert_wildcards and not self.acknowledge_wildcards:
            raise ValueError("Acknowledge wildcard discovery roots; apex scope is not implied")
        return self


class BatchAction(ClosedModel):
    confirmed: bool = False
    retry_delivery: bool = False
    acknowledge_uncertain: bool = False
