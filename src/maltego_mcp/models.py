"""Pydantic input models for the MCP tools.

All tool inputs are validated by these models so the tool bodies can assume
clean, well-typed data. ``extra='forbid'`` catches typos in argument names.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from maltego_mcp.formatting import ResponseFormat

_BASE_CONFIG = ConfigDict(
    str_strip_whitespace=True,
    validate_assignment=True,
    extra="forbid",
)


class CreateGraphInput(BaseModel):
    model_config = _BASE_CONFIG
    name: str = Field(..., description="Name for the new graph (e.g. 'acme-investigation').", min_length=1, max_length=120)


class OpenGraphInput(BaseModel):
    model_config = _BASE_CONFIG
    path: str = Field(..., description="Absolute or relative path to an existing .mtgx file.", min_length=1)


class SaveGraphInput(BaseModel):
    model_config = _BASE_CONFIG
    path: Optional[str] = Field(
        default=None,
        description="Destination .mtgx path. If omitted, re-saves to the path the graph was opened from.",
    )
    graph_name: Optional[str] = Field(
        default=None,
        description="Name of the graph to save. Defaults to the active graph.",
    )


class SetActiveGraphInput(BaseModel):
    model_config = _BASE_CONFIG
    name: str = Field(..., description="Name of an already-open graph to make active.", min_length=1)


class AddEntityInput(BaseModel):
    model_config = _BASE_CONFIG
    type: str = Field(
        ...,
        description="Maltego entity type id, e.g. 'maltego.Domain', 'maltego.IPv4Address', 'maltego.Person'. Use maltego_list_entity_types to discover options.",
        min_length=1,
    )
    value: str = Field(..., description="Primary value of the entity, e.g. 'example.com' or '8.8.8.8'.", min_length=1)
    properties: Optional[Dict[str, str]] = Field(
        default=None,
        description="Optional extra Maltego properties as a name->value map.",
    )
    notes: Optional[str] = Field(default=None, description="Optional free-text notes for the entity.")
    dedupe: bool = Field(
        default=True,
        description="If true (default), reuse an existing entity with the same type+value instead of creating a duplicate.",
    )


class AddLinkInput(BaseModel):
    model_config = _BASE_CONFIG
    source_id: str = Field(..., description="Id of the source entity (e.g. 'n0').", min_length=1)
    target_id: str = Field(..., description="Id of the target entity (e.g. 'n1').", min_length=1)
    label: Optional[str] = Field(default="", description="Optional label drawn on the link, e.g. 'resolves to'.")

    @field_validator("source_id", "target_id")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Entity id cannot be blank.")
        return v


class ListEntitiesInput(BaseModel):
    model_config = _BASE_CONFIG
    type_filter: Optional[str] = Field(
        default=None,
        description="Only return entities of this Maltego type (e.g. 'maltego.IPv4Address').",
    )
    value_contains: Optional[str] = Field(
        default=None,
        description="Only return entities whose value contains this substring (case-insensitive).",
    )
    limit: int = Field(default=50, description="Maximum entities to return.", ge=1, le=500)
    offset: int = Field(default=0, description="Number of entities to skip (pagination).", ge=0)
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: 'markdown' or 'json'."
    )


class GetEntityInput(BaseModel):
    model_config = _BASE_CONFIG
    entity_id: str = Field(..., description="Id of the entity to fetch (e.g. 'n0').", min_length=1)
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: 'markdown' or 'json'."
    )


class UpdateEntityInput(BaseModel):
    model_config = _BASE_CONFIG
    entity_id: str = Field(..., description="Id of the entity to update.", min_length=1)
    value: Optional[str] = Field(default=None, description="New primary value.")
    properties: Optional[Dict[str, str]] = Field(
        default=None, description="Properties to set/merge (name->value)."
    )
    notes: Optional[str] = Field(default=None, description="Replacement notes text.")
    weight: Optional[int] = Field(default=None, description="Node weight / relevance.", ge=0, le=100)


class DeleteEntityInput(BaseModel):
    model_config = _BASE_CONFIG
    entity_id: str = Field(..., description="Id of the entity to delete (its links are removed too).", min_length=1)


class ListEntityTypesInput(BaseModel):
    model_config = _BASE_CONFIG
    category: Optional[str] = Field(
        default=None,
        description="Filter to one category (e.g. 'infrastructure', 'personal', 'social').",
    )


class ListTransformsInput(BaseModel):
    model_config = _BASE_CONFIG
    input_type: Optional[str] = Field(
        default=None,
        description="Only list transforms that accept this Maltego entity type as input.",
    )


class RunTransformInput(BaseModel):
    model_config = _BASE_CONFIG
    transform_name: str = Field(
        ...,
        description="Name of the transform to run, e.g. 'dns.domain_to_ip'. Use maltego_list_transforms to discover.",
        min_length=1,
    )
    entity_id: str = Field(
        ...,
        description="Id of the input entity in the active graph to run the transform on.",
        min_length=1,
    )
    add_to_graph: bool = Field(
        default=True,
        description="If true (default), add resulting entities and links to the active graph.",
    )


# --- high-level investigation / orchestration --------------------------------
class InvestigateInput(BaseModel):
    """Shared input for the investigate_* orchestration tools."""

    model_config = _BASE_CONFIG
    value: str = Field(..., description="The seed value to investigate (domain, email, or IP).", min_length=1)
    allow_network: bool = Field(
        default=True,
        description="Run transforms that make network calls (DNS, OSINT APIs). Set false for an offline/passive parse-only run.",
    )
    max_rounds: int = Field(
        default=2,
        description="How many expansion rounds to run (breadth-first). Higher = deeper, more tool calls.",
        ge=1,
        le=4,
    )


class LoadGraphInput(BaseModel):
    model_config = _BASE_CONFIG
    path: str = Field(..., description="Path to an existing .mtgx file to load.", min_length=1)


class ImportGraphInput(BaseModel):
    model_config = _BASE_CONFIG
    path: str = Field(..., description="Path to a .mtgx file whose entities/links are merged into the active graph.", min_length=1)
    dedupe: bool = Field(
        default=True,
        description="Merge entities matching an existing (type, value) instead of duplicating them.",
    )


class ExplainEntityInput(BaseModel):
    model_config = _BASE_CONFIG
    entity_id: str = Field(..., description="Id of the entity to explain (e.g. 'n0').", min_length=1)
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: 'markdown' or 'json'."
    )


class AnalysisLimitInput(BaseModel):
    model_config = _BASE_CONFIG
    limit: int = Field(default=10, description="Maximum items to return.", ge=1, le=50)
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: 'markdown' or 'json'."
    )


class SummarizeGraphInput(BaseModel):
    model_config = _BASE_CONFIG
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: 'markdown' or 'json'."
    )


class ApplyLayoutInput(BaseModel):
    model_config = _BASE_CONFIG
    algorithm: str = Field(
        default="hierarchical",
        description="Layout algorithm: 'hierarchical', 'radial', or 'force'.",
    )

    @field_validator("algorithm")
    @classmethod
    def _known(cls, v: str) -> str:
        allowed = {"hierarchical", "radial", "force"}
        if v not in allowed:
            raise ValueError(f"algorithm must be one of {sorted(allowed)}")
        return v


class ImportCsvInput(BaseModel):
    model_config = _BASE_CONFIG
    path: Optional[str] = Field(
        default=None, description="Path to a CSV file with 'type,value' columns (optionally 'notes','link_to')."
    )
    content: Optional[str] = Field(
        default=None, description="Raw CSV text (alternative to 'path')."
    )

    @field_validator("content")
    @classmethod
    def _one_source(cls, v, info):
        # Either path or content must be provided (checked again in the tool).
        return v


class ReportFormat(str, Enum):
    MARKDOWN = "markdown"
    HTML = "html"


class GenerateReportInput(BaseModel):
    model_config = _BASE_CONFIG
    format: ReportFormat = Field(
        default=ReportFormat.MARKDOWN, description="Report format: 'markdown' or 'html'."
    )


class ExportReportInput(BaseModel):
    model_config = _BASE_CONFIG
    path: str = Field(..., description="Destination file path for the report.", min_length=1)
    format: ReportFormat = Field(
        default=ReportFormat.MARKDOWN, description="Report format: 'markdown' or 'html'."
    )


class RunMachineInput(BaseModel):
    model_config = _BASE_CONFIG
    machine_name: str = Field(..., description="Machine to run, e.g. 'passive_domain'. Use maltego_list_machines.", min_length=1)
    seed_value: str = Field(..., description="Seed value for the machine (e.g. the domain or email to investigate).", min_length=1)
    allow_network: Optional[bool] = Field(
        default=None, description="Override the machine's network setting (default: use the machine's own)."
    )
    max_rounds: Optional[int] = Field(
        default=None, description="Override the machine's expansion depth.", ge=1, le=4
    )


# --- investigation memory ----------------------------------------------------
class ExplainTransformInput(BaseModel):
    model_config = _BASE_CONFIG
    transform_execution_id: str = Field(
        ..., description="Execution id of a recorded step (e.g. 'x0'). See maltego_list_investigation_steps.", min_length=1
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: 'markdown' or 'json'."
    )


class ExplainWhyInput(BaseModel):
    model_config = _BASE_CONFIG
    entity_id: str = Field(..., description="Entity id to explain the provenance of (e.g. 'n3').", min_length=1)
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: 'markdown' or 'json'."
    )


class ListStepsInput(BaseModel):
    model_config = _BASE_CONFIG
    limit: int = Field(default=50, description="Maximum steps to return.", ge=1, le=500)
    offset: int = Field(default=0, description="Steps to skip (pagination).", ge=0)
    status: Optional[str] = Field(
        default=None, description="Filter by status: 'success', 'empty', or 'error'."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: 'markdown' or 'json'."
    )


# --- unified investigate entry point -----------------------------------------
class InvestigateQueryInput(BaseModel):
    model_config = _BASE_CONFIG
    query: str = Field(
        ...,
        description="Anything to investigate: a domain, email, IPv4/IPv6, or URL. The type is auto-detected.",
        min_length=1,
    )
    allow_network: bool = Field(
        default=True, description="Run network transforms (default True). Set false for offline/passive."
    )
    max_rounds: Optional[int] = Field(
        default=None, description="Override the chosen machine's expansion depth.", ge=1, le=4
    )
    layout: str = Field(
        default="hierarchical",
        description="Layout to apply after investigating: 'hierarchical', 'radial', or 'force'.",
    )

    @field_validator("layout")
    @classmethod
    def _known_layout(cls, v: str) -> str:
        allowed = {"hierarchical", "radial", "force"}
        if v not in allowed:
            raise ValueError(f"layout must be one of {sorted(allowed)}")
        return v


# --- real-time events --------------------------------------------------------
class GetEventsInput(BaseModel):
    model_config = _BASE_CONFIG
    limit: int = Field(default=50, description="Maximum recent events to return.", ge=1, le=500)
    since_seq: Optional[int] = Field(
        default=None, description="Only return events with sequence number greater than this."
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON, description="Output format: 'json' (default) or 'markdown'."
    )


# --- scoring -----------------------------------------------------------------
class ScoreEntityInput(BaseModel):
    model_config = _BASE_CONFIG
    entity_id: str = Field(..., description="Entity id to score (e.g. 'n0').", min_length=1)


class RankEntitiesInput(BaseModel):
    model_config = _BASE_CONFIG
    limit: int = Field(default=20, description="Maximum entities to return.", ge=1, le=200)
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: 'markdown' or 'json'."
    )


# --- export / link & graph management ----------------------------------------
class ExportPathInput(BaseModel):
    model_config = _BASE_CONFIG
    path: str = Field(..., description="Destination file path for the export.", min_length=1)


class ListLinksInput(BaseModel):
    model_config = _BASE_CONFIG
    limit: int = Field(default=50, description="Maximum links to return.", ge=1, le=500)
    offset: int = Field(default=0, description="Links to skip (pagination).", ge=0)
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="Output format: 'markdown' or 'json'."
    )


class DeleteLinkInput(BaseModel):
    model_config = _BASE_CONFIG
    link_id: str = Field(..., description="Id of the link to delete (e.g. 'e0').", min_length=1)


class RenameGraphInput(BaseModel):
    model_config = _BASE_CONFIG
    new_name: str = Field(..., description="New name for the graph.", min_length=1, max_length=120)
    graph_name: Optional[str] = Field(
        default=None, description="Graph to rename. Defaults to the active graph."
    )


class DeleteGraphInput(BaseModel):
    model_config = _BASE_CONFIG
    name: str = Field(..., description="Name of the open graph to remove from the server.", min_length=1)
