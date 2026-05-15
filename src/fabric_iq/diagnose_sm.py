"""Diagnose a Semantic Model for Fabric-UI ontology auto-binding compatibility.

The Fabric portal's "Generate ontology from a semantic model" feature only
creates entity-type ``DataBinding`` and relationship ``Contextualization``
parts under specific conditions documented at:

    https://learn.microsoft.com/en-us/fabric/iq/ontology/concepts-generate#support-for-semantic-model-modes

In particular, **per-table** auto-binding only fires when the table is in
**Direct Lake** storage mode, the backing lakehouse workspace has inbound
public access enabled, and a single primary key is detected.  Tables in
*Import* or *DirectQuery* mode are silently skipped (the entity is created
without a binding).

This module statically inspects a Semantic Model's TMDL and predicts, per
table, whether the Fabric UI generator would auto-bind it — without making
any changes.  It also flags column-level blockers (Decimal type, special
characters that auto-enable column mapping).

This tool's own ``fabric-iq create`` is *not* subject to these UI
restrictions: it builds DataBindings and Contextualizations directly
against any lakehouse you point it at, regardless of SM storage mode.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field

from fabric_iq.api_client import FabricClient

logger = logging.getLogger(__name__)


# Characters that auto-enable Delta column mapping (from official docs).
# A column whose name contains any of these triggers column mapping on the
# underlying Delta table, which makes the table ineligible for ontology
# data binding.
_COLUMN_MAPPING_TRIGGERS = re.compile(r"[,;{}()\n\t= ]")


@dataclass
class TableDiagnosis:
    """Diagnosis result for a single semantic-model table."""

    name: str
    storage_mode: str  # "directLake" | "import" | "directQuery" | "calculated" | "unknown"
    has_pk: bool
    decimal_columns: list[str] = field(default_factory=list)
    bad_name_columns: list[str] = field(default_factory=list)

    @property
    def ui_would_auto_bind(self) -> bool:
        """Predict whether the Fabric UI generator would create a DataBinding."""
        return (
            self.storage_mode == "directLake"
            and not self.bad_name_columns
        )

    @property
    def ui_would_auto_contextualize(self) -> bool:
        """Predict whether relationships involving this table would auto-bind."""
        return self.ui_would_auto_bind and self.has_pk

    @property
    def verdict(self) -> str:
        if self.ui_would_auto_bind and self.ui_would_auto_contextualize:
            return "OK"
        if self.ui_would_auto_bind:
            return "BIND-ONLY (no PK → no contextualization)"
        return "SKIPPED by Fabric UI"

    @property
    def reasons(self) -> list[str]:
        out: list[str] = []
        if self.storage_mode != "directLake":
            out.append(f"storage mode = {self.storage_mode!r} (need 'directLake')")
        if self.bad_name_columns:
            out.append(
                f"{len(self.bad_name_columns)} column name(s) trigger Delta column "
                f"mapping: {self.bad_name_columns[:3]}"
            )
        if not self.has_pk:
            out.append("no single primary key detected (blocks contextualizations)")
        if self.decimal_columns:
            out.append(
                f"{len(self.decimal_columns)} Decimal column(s) "
                f"(returns null in Fabric Graph): {self.decimal_columns[:3]}"
            )
        return out


def _detect_storage_mode(tmdl: str) -> str:
    """Return the table's storage mode based on its partition block(s)."""
    # Calculated tables have a `source` line containing a DAX expression and
    # no `mode:` declaration, but most importantly they usually appear as
    # `partition <Name> = calculated` or have `source = ` followed by DAX.
    if re.search(r"(?m)^\s*partition\s+\S+\s*=\s*calculated\b", tmdl):
        return "calculated"

    modes = re.findall(r"(?m)^\s*mode:\s*(\w+)", tmdl)
    if not modes:
        # Default partition kind without mode is import in TMDL
        if re.search(r"(?m)^\s*partition\s+\S+\s*=\s*m\b", tmdl):
            return "import"
        return "unknown"
    # If any partition is directLake, treat the table as directLake.
    if any(m.lower() == "directlake" for m in modes):
        return "directLake"
    if any(m.lower() == "directquery" for m in modes):
        return "directQuery"
    if any(m.lower() == "import" for m in modes):
        return "import"
    return modes[0]


def _diagnose_table(tmdl: str) -> TableDiagnosis | None:
    m = re.search(r"(?m)^table\s+(.+)\s*$", tmdl)
    if not m:
        return None
    table_name = m.group(1).strip()

    # Column blocks
    col_blocks = re.split(r"(?m)(?=^\s+column\s+)", tmdl)
    decimal_cols: list[str] = []
    bad_name_cols: list[str] = []
    own_id = f"{table_name}id".lower()
    has_pk = False

    for block in col_blocks:
        # Column names may be bare (`column Foo`) or quoted (`column 'Foo Bar'`).
        m_col = re.search(r"(?m)^\s+column\s+('([^']+)'|\"([^\"]+)\"|(\S+))", block)
        if not m_col:
            continue
        col_name = m_col.group(2) or m_col.group(3) or m_col.group(4)
        m_dt = re.search(r"(?m)^\s+dataType:\s*(\w+)", block)
        data_type = m_dt.group(1).lower() if m_dt else "string"

        if data_type == "decimal":
            decimal_cols.append(col_name)
        if _COLUMN_MAPPING_TRIGGERS.search(col_name):
            bad_name_cols.append(col_name)
        if re.search(r"(?m)^\s+isKey:\s*true", block, re.IGNORECASE):
            has_pk = True
        elif col_name.lower() == own_id:
            has_pk = True

    return TableDiagnosis(
        name=table_name,
        storage_mode=_detect_storage_mode(tmdl),
        has_pk=has_pk,
        decimal_columns=decimal_cols,
        bad_name_columns=bad_name_cols,
    )


def diagnose_semantic_model(
    client: FabricClient,
    workspace_id: str,
    semantic_model_id: str,
) -> list[TableDiagnosis]:
    """Fetch the SM, diagnose every table, and log a per-table report."""
    sm_def = client.get_semantic_model_definition(workspace_id, semantic_model_id)
    parts = sm_def.get("definition", {}).get("parts", [])
    diagnoses: list[TableDiagnosis] = []
    for part in parts:
        path = part.get("path", "")
        if not path.startswith("definition/tables/") or not path.endswith(".tmdl"):
            continue
        try:
            tmdl = base64.b64decode(part["payload"]).decode("utf-8")
        except Exception:
            continue
        d = _diagnose_table(tmdl)
        if d:
            diagnoses.append(d)
    diagnoses.sort(key=lambda d: d.name.lower())
    _log_report(diagnoses)
    return diagnoses


def _log_report(diagnoses: list[TableDiagnosis]) -> None:
    logger.info("")
    logger.info("Semantic-model auto-bind diagnosis (Fabric UI generator)")
    logger.info("=" * 78)
    logger.info(
        "  %-28s %-12s %-3s  %s",
        "TABLE", "STORAGE", "PK", "FABRIC-UI VERDICT",
    )
    logger.info("  " + "-" * 76)
    for d in diagnoses:
        logger.info(
            "  %-28s %-12s %-3s  %s",
            d.name[:28],
            d.storage_mode,
            "yes" if d.has_pk else "no",
            d.verdict,
        )
        for reason in d.reasons:
            logger.info("    - %s", reason)

    bind_ok = sum(1 for d in diagnoses if d.ui_would_auto_bind)
    ctx_ok = sum(1 for d in diagnoses if d.ui_would_auto_contextualize)
    total = len(diagnoses)
    logger.info("  " + "-" * 76)
    logger.info(
        "  Summary: %d/%d tables would auto-bind, %d/%d would auto-contextualize",
        bind_ok, total, ctx_ok, total,
    )
    if bind_ok < total:
        logger.info(
            "  Note: `fabric-iq create` is NOT subject to this restriction — it binds "
            "directly to any lakehouse you pass with -l, regardless of storage mode."
        )
    logger.info("")
