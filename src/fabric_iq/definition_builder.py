"""Build ontology definition parts from parsed TMDL metadata."""

from __future__ import annotations

import base64
import json
import logging

from fabric_iq.config import (
    SCHEMA_ENTITY_TYPE,
    SCHEMA_DATA_BINDING,
    SCHEMA_RELATIONSHIP_TYPE,
    SCHEMA_CONTEXTUALIZATION,
    SCHEMA_PLATFORM,
)
from fabric_iq.models import (
    Column,
    DefinitionPart,
    Table,
    Relationship,
    new_guid,
    new_unique_id,
)

logger = logging.getLogger(__name__)


def _b64(obj: dict | str) -> str:
    """Base64-encode a JSON-serialisable object or raw string."""
    text = json.dumps(obj, indent=2, ensure_ascii=False) if isinstance(obj, dict) else obj
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------------------
# PK detection and relationship normalisation
# ---------------------------------------------------------------------------

def _detect_pk_column(table: Table) -> str | None:
    """Heuristically detect the PK column of *table*.

    Looks for a column whose name matches ``<TableName>ID``.  Returns
    the column name or *None* if no match.
    """
    expected = f"{table.name}ID"
    for col in table.columns:
        if col.name.lower() == expected.lower():
            return col.name
    return None


def normalize_relationships(
    tables: dict[str, Table],
    relationships: list[Relationship],
) -> list[Relationship]:
    """Return a copy of *relationships* with from=many, to=one guaranteed.

    Detection heuristic:
      1. If ``to_col`` matches the ``to_table``'s PK → already correct
         (to = one/PK side).
      2. Else if ``from_col`` matches the ``from_table``'s PK → swap
         (from is actually the one/PK side).
      3. Else keep default direction.
    """
    pks: dict[str, str | None] = {
        name: _detect_pk_column(t) for name, t in tables.items()
    }

    normalised: list[Relationship] = []
    for rel in relationships:
        to_pk = pks.get(rel.to_table)
        from_pk = pks.get(rel.from_table)

        need_swap = False
        if to_pk and rel.to_col.lower() == to_pk.lower():
            need_swap = False  # to side is already one/PK
        elif from_pk and rel.from_col.lower() == from_pk.lower():
            need_swap = True   # from side is actually one/PK

        if need_swap:
            normalised.append(
                Relationship(
                    rel_id=rel.rel_id,
                    from_table=rel.to_table,
                    from_col=rel.to_col,
                    to_table=rel.from_table,
                    to_col=rel.from_col,
                )
            )
            logger.debug(
                "  Swapped direction: %s.%s → %s.%s",
                rel.to_table, rel.to_col, rel.from_table, rel.from_col,
            )
        else:
            normalised.append(rel)

    return normalised


# ---------------------------------------------------------------------------
# Entity-ID-parts computation
# ---------------------------------------------------------------------------

def compute_entity_id_parts(
    tables: dict[str, Table],
    relationships: list[Relationship],
) -> dict[str, list[str]]:
    """Determine ``entityIdParts`` for each table.

    Resolution order (after relationship normalisation):

      1. **Explicit PK** — if ``table.pk_column_names`` was populated by the
         TMDL parser (via ``isKey`` or leading-ID heuristic), use those.
      2. **One/PK tables** (appear as ``to_table``): entityIdParts = the
         referenced ``to_col`` property IDs.
      3. **Many/FK-only tables** (never a ``to_table``): entityIdParts = all
         ``from_col`` property IDs (composite key from FKs).
      4. **Isolated tables** (no relationships): first column.

    Returns a mapping of table-name → list of ontology property IDs.
    """
    to_col_names: dict[str, set[str]] = {name: set() for name in tables}
    from_col_names: dict[str, set[str]] = {name: set() for name in tables}

    for rel in relationships:
        if rel.to_table in to_col_names:
            to_col_names[rel.to_table].add(rel.to_col)
        if rel.from_table in from_col_names:
            from_col_names[rel.from_table].add(rel.from_col)

    result: dict[str, list[str]] = {}
    for name, table in tables.items():
        # 1. Explicit PK from TMDL parsing
        if table.pk_column_names:
            pk_set = set(table.pk_column_names)
            id_parts = [
                col.ontology_id for col in table.columns if col.name in pk_set
            ]
            if id_parts:
                logger.info(
                    "  %s: entityIdParts from detected PK columns %s",
                    name, table.pk_column_names,
                )
                result[name] = id_parts
                continue

        # 2/3. Infer from relationship participation
        to_cols = to_col_names[name]
        from_cols = from_col_names[name]

        if to_cols:
            # One/PK side: entityIdParts = the PK column(s) referenced by FKs
            id_parts = [
                col.ontology_id for col in table.columns if col.name in to_cols
            ]
        elif from_cols:
            # Many/FK side: composite key from FK columns
            id_parts = [
                col.ontology_id for col in table.columns if col.name in from_cols
            ]
        else:
            id_parts = []

        # 4. Fallback to first column
        result[name] = id_parts if id_parts else [table.columns[0].ontology_id]

    return result


# ---------------------------------------------------------------------------
# Individual part builders
# ---------------------------------------------------------------------------

_VALID_KEY_VALUE_TYPES = {"String", "BigInt"}


def build_entity_type(
    table: Table,
    entity_id_parts: list[str] | None = None,
) -> dict:
    """Build an EntityType definition payload for a single table.

    Parameters
    ----------
    entity_id_parts:
        List of ontology property IDs to use as key parts.  When *None*
        the first column is used (legacy fallback).
    """
    if entity_id_parts is None:
        entity_id_parts = [table.columns[0].ontology_id]

    id_parts_set = set(entity_id_parts)

    properties = []
    for col in table.columns:
        vtype = col.value_type
        # Entity keys only accept String or BigInt; coerce others to String
        if col.ontology_id in id_parts_set and vtype not in _VALID_KEY_VALUE_TYPES:
            logger.warning(
                "  %s.%s: coercing entityIdParts valueType '%s' → 'String' "
                "(only String/BigInt allowed for keys)",
                table.name, col.name, vtype,
            )
            vtype = "String"
        properties.append({
            "id": col.ontology_id,
            "name": col.name,
            "redefines": None,
            "baseTypeNamespaceType": None,
            "valueType": vtype,
        })

    return {
        "$schema": SCHEMA_ENTITY_TYPE,
        "id": table.entity_type_id,
        "namespace": "usertypes",
        "baseEntityTypeId": None,
        "name": table.name,
        "entityIdParts": entity_id_parts,
        "displayNamePropertyId": None,
        "namespaceType": "Imported",
        "visibility": "Visible",
        "properties": properties,
        "timeseriesProperties": [],
    }


def build_data_binding(
    table: Table,
    workspace_id: str,
    lakehouse_id: str,
    *,
    lakehouse_table: str = "",
    lakehouse_schema: str = "",
    column_mappings: dict[str, str] | None = None,
) -> tuple[str, dict]:
    """Build a DataBinding payload.  Returns ``(binding_id, payload_dict)``.

    Parameters
    ----------
    lakehouse_table:
        Override binding table name.  Defaults to the SM table name.
    lakehouse_schema:
        Override binding schema.  Defaults to the SM schema.
    column_mappings:
        SM column name → lakehouse column name.  Unmapped columns keep
        their SM name.
    """
    binding_id = new_guid()
    col_map = column_mappings or {}

    property_bindings = [
        {
            "sourceColumnName": col_map.get(col.name, col.name),
            "targetPropertyId": col.ontology_id,
        }
        for col in table.columns
    ]

    payload = {
        "$schema": SCHEMA_DATA_BINDING,
        "id": binding_id,
        "dataBindingConfiguration": {
            "dataBindingType": "NonTimeSeries",
            "propertyBindings": property_bindings,
            "sourceTableProperties": {
                "sourceType": "LakehouseTable",
                "workspaceId": workspace_id,
                "itemId": lakehouse_id,
                "sourceTableName": lakehouse_table or table.name,
                "sourceSchema": lakehouse_schema or table.schema,
            },
        },
    }
    return binding_id, payload


def build_relationship_type(
    rel: Relationship,
    tables: dict[str, Table],
) -> tuple[str, dict]:
    """Build a RelationshipType definition.  Returns ``(reltype_id, payload_dict)``.

    In TMDL:    from = many/FK side,  to = one/PK side.
    In Ontology (this generator): source = many/FK side (= TMDL ``from``),
    target = one/PK side (= TMDL ``to``).

    This convention keeps the relationship name (``{from}_has_{to}``)
    semantically aligned with the graph edge direction
    (origin/source = subject = FK-holder).
    """
    reltype_id = new_unique_id()
    from_entity_id = tables[rel.from_table].entity_type_id  # many / FK
    to_entity_id = tables[rel.to_table].entity_type_id  # one / PK

    payload = {
        "$schema": SCHEMA_RELATIONSHIP_TYPE,
        "namespace": "usertypes",
        "id": reltype_id,
        "name": f"{rel.from_table}_has_{rel.to_table}",
        "namespaceType": "Imported",
        "source": {"entityTypeId": from_entity_id},  # many/FK = ontology source (subject)
        "target": {"entityTypeId": to_entity_id},  # one/PK = ontology target (object)
    }
    return reltype_id, payload


def build_contextualization(
    rel: Relationship,
    reltype_id: str,
    tables: dict[str, Table],
    workspace_id: str,
    lakehouse_id: str,
    entity_id_parts_map: dict[str, list[str]] | None = None,
    *,
    lakehouse_table: str = "",
    lakehouse_schema: str = "",
    column_mappings: dict[str, str] | None = None,
) -> tuple[str, dict] | None:
    """Build a Contextualization payload if possible.

    Returns ``(ctx_id, payload_dict)`` or *None* if the required columns
    cannot be resolved.

    Parameters
    ----------
    lakehouse_table:
        Override binding table name for the from-table.  Defaults to SM name.
    lakehouse_schema:
        Override binding schema for the from-table.  Defaults to SM schema.
    column_mappings:
        SM column name → lakehouse column name for the from-table.

    Rules (aligned with ``source = from / FK`` convention in
    :func:`build_relationship_type`):
      - ``dataBindingTable`` = the *from* table (many / FK side = ontology source)
      - ``sourceKeyRefBindings``: **ALL** entityIdParts columns of the source
        entity (from-table / FK side) — direct columns on the binding table
      - ``targetKeyRefBindings``: For each entityIdPart of the target entity
        (to-table / PK side), the matching FK column in the binding table →
        target entity's PK property
    """
    from_table = tables[rel.from_table]
    to_table = tables[rel.to_table]

    # Target entity's PK property (the column named in toCol)
    to_col_prop = next(
        (c for c in to_table.columns if c.name == rel.to_col),
        None,
    )
    if to_col_prop is None:
        logger.warning(
            "Cannot resolve toCol '%s' in table '%s' – skipping contextualization",
            rel.to_col,
            rel.to_table,
        )
        return None

    ctx_id = new_guid()
    col_map = column_mappings or {}

    # --- sourceKeyRefBindings ---
    # Bind ALL entityIdParts of the source entity (from-table / FK side).
    # These columns live directly on the binding table, so the mapping is
    # 1:1 (column name → its own ontology property id).
    if entity_id_parts_map:
        from_id_parts = set(entity_id_parts_map.get(rel.from_table, []))
    else:
        from_id_parts = {from_table.columns[0].ontology_id}

    source_key_ref_bindings = [
        {
            "sourceColumnName": col_map.get(col.name, col.name),
            "targetPropertyId": col.ontology_id,
        }
        for col in from_table.columns
        if col.ontology_id in from_id_parts
    ]

    # --- targetKeyRefBindings ---
    # Bind ALL entityIdParts of the target entity (to-table / PK side).
    # For each PK column in the target entity, find the matching FK column
    # in the from-table (by name).  The explicit rel column is always included.
    target_key_ref_bindings = []
    if entity_id_parts_map:
        to_id_parts = set(entity_id_parts_map.get(rel.to_table, []))
    else:
        to_id_parts = {to_col_prop.ontology_id}

    to_col_by_id = {c.ontology_id: c for c in to_table.columns}
    from_col_by_name = {c.name: c for c in from_table.columns}

    for pk_id in (c.ontology_id for c in to_table.columns if c.ontology_id in to_id_parts):
        pk_col = to_col_by_id[pk_id]
        # Try to find matching FK column in the from-table by name
        fk_col_name = pk_col.name if pk_col.name in from_col_by_name else None
        # If this is the explicit relationship column, use rel.from_col
        if pk_col.name == rel.to_col:
            fk_col_name = rel.from_col
        if fk_col_name is None:
            logger.warning(
                "Cannot find FK column for target PK '%s' in table '%s' – "
                "skipping contextualization for %s → %s",
                pk_col.name, rel.from_table, rel.from_table, rel.to_table,
            )
            return None
        target_key_ref_bindings.append({
            "sourceColumnName": col_map.get(fk_col_name, fk_col_name),
            "targetPropertyId": pk_id,
        })

    payload = {
        "$schema": SCHEMA_CONTEXTUALIZATION,
        "id": ctx_id,
        "dataBindingTable": {
            "workspaceId": workspace_id,
            "itemId": lakehouse_id,
            "sourceTableName": lakehouse_table or rel.from_table,
            "sourceSchema": lakehouse_schema or from_table.schema,
            "sourceType": "LakehouseTable",
        },
        "sourceKeyRefBindings": source_key_ref_bindings,
        "targetKeyRefBindings": target_key_ref_bindings,
    }
    return ctx_id, payload


def build_platform(display_name: str) -> dict:
    """Build the ``.platform`` metadata part."""
    return {
        "$schema": SCHEMA_PLATFORM,
        "metadata": {
            "type": "Ontology",
            "displayName": display_name,
        },
        "config": {
            "version": "2.0",
            "logicalId": "00000000-0000-0000-0000-000000000000",
        },
    }


# ---------------------------------------------------------------------------
# Top-level assembler
# ---------------------------------------------------------------------------

def build_definition_parts(
    tables: dict[str, Table],
    relationships: list[Relationship],
    workspace_id: str,
    lakehouse_id: str,
    display_name: str,
    entity_configs: dict[str, "EntityConfig"] | None = None,
) -> list[dict]:
    """Assemble a full ontology definition parts list.

    Parameters
    ----------
    entity_configs:
        Per-entity config overrides (lakehouse_table, lakehouse_schema,
        column_mappings).  Imported from :mod:`ontology_config`.

    Returns a list of dicts suitable for the ``updateDefinition`` API payload.
    """
    # Avoid circular import at module level
    from fabric_iq.ontology_config import EntityConfig as _EC

    parts: list[dict] = []
    cfgs = entity_configs or {}

    # Normalise relationship directions so from=many, to=one always
    rels = normalize_relationships(tables, relationships)

    # Compute entityIdParts for every table based on relationship participation
    id_parts_map = compute_entity_id_parts(tables, rels)

    # Root definition.json (empty)
    parts.append(
        {"path": "definition.json", "payload": _b64("{}"), "payloadType": "InlineBase64"}
    )

    # EntityTypes + DataBindings
    for table in tables.values():
        ec = cfgs.get(table.name, _EC())

        # EntityType
        entity_payload = build_entity_type(table, id_parts_map.get(table.name))
        parts.append(
            {
                "path": f"EntityTypes/{table.entity_type_id}/definition.json",
                "payload": _b64(entity_payload),
                "payloadType": "InlineBase64",
            }
        )

        # DataBinding (with optional lakehouse overrides)
        binding_id, binding_payload = build_data_binding(
            table, workspace_id, lakehouse_id,
            lakehouse_table=ec.lakehouse_table,
            lakehouse_schema=ec.lakehouse_schema,
            column_mappings=ec.column_mappings or None,
        )
        parts.append(
            {
                "path": f"EntityTypes/{table.entity_type_id}/DataBindings/{binding_id}.json",
                "payload": _b64(binding_payload),
                "payloadType": "InlineBase64",
            }
        )

    # RelationshipTypes + Contextualizations
    ctx_count = 0
    for rel in rels:
        ec_from = cfgs.get(rel.from_table, _EC())

        reltype_id, rel_payload = build_relationship_type(rel, tables)
        parts.append(
            {
                "path": f"RelationshipTypes/{reltype_id}/definition.json",
                "payload": _b64(rel_payload),
                "payloadType": "InlineBase64",
            }
        )

        ctx_result = build_contextualization(
            rel, reltype_id, tables, workspace_id, lakehouse_id,
            entity_id_parts_map=id_parts_map,
            lakehouse_table=ec_from.lakehouse_table,
            lakehouse_schema=ec_from.lakehouse_schema,
            column_mappings=ec_from.column_mappings or None,
        )
        if ctx_result:
            ctx_id, ctx_payload = ctx_result
            parts.append(
                {
                    "path": f"RelationshipTypes/{reltype_id}/Contextualizations/{ctx_id}.json",
                    "payload": _b64(ctx_payload),
                    "payloadType": "InlineBase64",
                }
            )
            ctx_count += 1

    # .platform
    platform_payload = build_platform(display_name)
    parts.append(
        {"path": ".platform", "payload": _b64(platform_payload), "payloadType": "InlineBase64"}
    )

    logger.info("  Total definition parts: %d", len(parts))
    logger.info("    EntityTypes:        %d", len(tables))
    logger.info("    DataBindings:       %d", len(tables))
    logger.info("    RelationshipTypes:  %d", len(rels))
    logger.info("    Contextualizations: %d", ctx_count)

    return parts
