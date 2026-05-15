"""Data models used across Fabric IQ modules."""

from __future__ import annotations

import uuid
import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------
_id_counter: int = int(time.time_ns() // 100)  # tick-style seed


def new_unique_id() -> str:
    """Generate a unique numeric string ID (snowflake-style)."""
    global _id_counter
    _id_counter += 1
    return str(_id_counter)


def new_guid() -> str:
    """Return a new lowercase UUID4 string."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Parsed table / column / relationship models
# ---------------------------------------------------------------------------
@dataclass
class Column:
    """A column parsed from TMDL."""

    name: str
    data_type: str  # TMDL type (e.g. "int64", "string")
    value_type: str  # Ontology type (e.g. "BigInt", "String")
    summarize_by: str = "none"  # TMDL summarizeBy (none, sum, count, …)
    ontology_id: str = field(default_factory=new_unique_id)


@dataclass
class Table:
    """A table parsed from TMDL with its columns."""

    name: str
    schema: str
    columns: list[Column]
    pk_column_names: list[str] = field(default_factory=list)  # Detected PK columns
    entity_type_id: str = field(default_factory=new_unique_id)


@dataclass
class Relationship:
    """A relationship parsed from TMDL.

    TMDL convention:
      - ``from`` = many / FK side
      - ``to``   = one / PK side

    Ontology convention (this generator):
      - ``source`` = many / FK side (= TMDL "from")  — subject of ``X_has_Y``
      - ``target`` = one / PK side  (= TMDL "to")    — object of ``X_has_Y``
    """

    rel_id: str
    from_table: str  # many / FK side
    from_col: str
    to_table: str  # one / PK side
    to_col: str


# ---------------------------------------------------------------------------
# Definition part (wire format)
# ---------------------------------------------------------------------------
@dataclass
class DefinitionPart:
    """A single part in a Fabric ontology definition payload."""

    path: str
    payload: str  # Base64-encoded content
    payload_type: str = "InlineBase64"

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "payload": self.payload,
            "payloadType": self.payload_type,
        }


# ---------------------------------------------------------------------------
# Remap configuration
# ---------------------------------------------------------------------------
@dataclass
class RemapConfig:
    """Holds source → target mapping for data-source remapping."""

    source_workspace_id: str
    source_item_id: str
    target_workspace_id: str
    target_item_id: str
