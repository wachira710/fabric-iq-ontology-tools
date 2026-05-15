"""Tests for diagnose_sm._diagnose_table and _detect_storage_mode."""

from __future__ import annotations

from fabric_iq.diagnose_sm import _detect_storage_mode, _diagnose_table


DIRECT_LAKE_TABLE = """\
table Taxi_Trip
    sourceLineageTag: [dbo].[Taxi_Trip]

    column TripId
        dataType: int64
        summarizeBy: none
        isKey: true

    column Fare
        dataType: double

    partition Taxi_Trip = entity
        mode: directLake
        source
            entityName: Taxi_Trip
            schemaName: dbo
"""

IMPORT_TABLE = """\
table Date

    column DateID
        dataType: int64

    column Date
        dataType: dateTime

    partition Date = m
        mode: import
        source = ...
"""

CALCULATED_TABLE = """\
table TopCustomers

    column Name
        dataType: string

    partition TopCustomers = calculated
        mode: import
        source = TOPN(10, Customers)
"""


class TestDetectStorageMode:
    def test_direct_lake(self):
        assert _detect_storage_mode(DIRECT_LAKE_TABLE) == "directLake"

    def test_import(self):
        assert _detect_storage_mode(IMPORT_TABLE) == "import"

    def test_calculated(self):
        assert _detect_storage_mode(CALCULATED_TABLE) == "calculated"


class TestDiagnoseTable:
    def test_direct_lake_table_with_pk_passes(self):
        d = _diagnose_table(DIRECT_LAKE_TABLE)
        assert d is not None
        assert d.name == "Taxi_Trip"
        assert d.storage_mode == "directLake"
        assert d.has_pk is True
        assert d.ui_would_auto_bind is True
        assert d.ui_would_auto_contextualize is True
        assert d.verdict == "OK"

    def test_import_table_is_skipped(self):
        d = _diagnose_table(IMPORT_TABLE)
        assert d is not None
        assert d.storage_mode == "import"
        assert d.ui_would_auto_bind is False
        assert "storage mode" in d.reasons[0]

    def test_decimal_column_is_flagged(self):
        tmdl = (
            "table Sales\n"
            "    column Amount\n"
            "        dataType: decimal\n"
            "    column SalesID\n"
            "        dataType: int64\n"
            "    partition Sales = entity\n"
            "        mode: directLake\n"
        )
        d = _diagnose_table(tmdl)
        assert d is not None
        assert d.decimal_columns == ["Amount"]
        assert d.has_pk is True  # SalesID detected as own-id

    def test_bad_column_name_blocks_binding(self):
        tmdl = (
            "table Bad\n"
            "    column 'My Col'\n"
            "        dataType: string\n"
            "    partition Bad = entity\n"
            "        mode: directLake\n"
        )
        d = _diagnose_table(tmdl)
        assert d is not None
        assert d.bad_name_columns == ["My Col"]
        assert d.ui_would_auto_bind is False
