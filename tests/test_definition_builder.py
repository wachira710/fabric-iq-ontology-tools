"""Tests for definition builder."""

import base64
import json

from fabric_iq.models import Column, Table, Relationship
from fabric_iq.definition_builder import (
    build_entity_type,
    build_data_binding,
    build_relationship_type,
    build_contextualization,
    build_definition_parts,
    compute_entity_id_parts,
    normalize_relationships,
)


def _make_table(name: str = "TestTable") -> Table:
    return Table(
        name=name,
        schema="dbo",
        columns=[
            Column(name="ID", data_type="int64", value_type="BigInt", ontology_id="100"),
            Column(name="Name", data_type="string", value_type="String", ontology_id="101"),
        ],
        entity_type_id="999",
    )


class TestBuildEntityType:
    def test_structure(self):
        table = _make_table()
        result = build_entity_type(table)
        assert result["id"] == "999"
        assert result["name"] == "TestTable"
        assert result["namespace"] == "usertypes"
        assert result["entityIdParts"] == ["100"]
        assert len(result["properties"]) == 2

    def test_property_fields(self):
        table = _make_table()
        result = build_entity_type(table)
        prop = result["properties"][0]
        assert prop["id"] == "100"
        assert prop["name"] == "ID"
        assert prop["valueType"] == "BigInt"
        assert prop["redefines"] is None


class TestBuildDataBinding:
    def test_structure(self):
        table = _make_table()
        bid, payload = build_data_binding(table, "ws-1", "lh-1")
        assert bid  # GUID string
        cfg = payload["dataBindingConfiguration"]
        assert cfg["dataBindingType"] == "NonTimeSeries"
        assert cfg["sourceTableProperties"]["sourceTableName"] == "TestTable"
        assert cfg["sourceTableProperties"]["workspaceId"] == "ws-1"
        assert cfg["sourceTableProperties"]["itemId"] == "lh-1"
        assert len(cfg["propertyBindings"]) == 2

    def test_lakehouse_table_override(self):
        table = _make_table()
        _, payload = build_data_binding(
            table, "ws", "lh",
            lakehouse_table="test_table_raw",
            lakehouse_schema="staging",
        )
        props = payload["dataBindingConfiguration"]["sourceTableProperties"]
        assert props["sourceTableName"] == "test_table_raw"
        assert props["sourceSchema"] == "staging"

    def test_column_mappings(self):
        table = _make_table()
        _, payload = build_data_binding(
            table, "ws", "lh",
            column_mappings={"ID": "id_col", "Name": "name_col"},
        )
        bindings = payload["dataBindingConfiguration"]["propertyBindings"]
        names = {b["sourceColumnName"] for b in bindings}
        assert names == {"id_col", "name_col"}
        # targetPropertyId still uses ontology IDs
        ids = {b["targetPropertyId"] for b in bindings}
        assert ids == {"100", "101"}

    def test_partial_column_mappings(self):
        """Unmapped columns keep their original name."""
        table = _make_table()
        _, payload = build_data_binding(
            table, "ws", "lh",
            column_mappings={"ID": "id_col"},  # Name not mapped
        )
        bindings = payload["dataBindingConfiguration"]["propertyBindings"]
        names = {b["sourceColumnName"] for b in bindings}
        assert names == {"id_col", "Name"}


class TestBuildRelationshipType:
    def test_source_target_direction(self):
        """Ontology source = FK/many side (fromTable), target = PK/one side (toTable).

        Keeps the relationship name ``{from}_has_{to}`` semantically aligned
        with the edge direction (origin = subject = FK-holder).
        """
        tables = {
            "Orders": Table(
                name="Orders", schema="dbo",
                columns=[Column("OrderID", "int64", "BigInt", ontology_id="200")],
                entity_type_id="E1",
            ),
            "Customer": Table(
                name="Customer", schema="dbo",
                columns=[Column("CustomerID", "int64", "BigInt", ontology_id="300")],
                entity_type_id="E2",
            ),
        }
        rel = Relationship("r1", from_table="Orders", from_col="CustomerID",
                           to_table="Customer", to_col="CustomerID")
        rid, payload = build_relationship_type(rel, tables)
        assert payload["source"]["entityTypeId"] == "E1"  # Orders = FK/many = source
        assert payload["target"]["entityTypeId"] == "E2"  # Customer = PK/one = target

    def test_name_matches_source_entity(self):
        """For name ``X_has_Y``, source MUST be entity X (subject))."""
        tables = {
            "Orders": Table(
                name="Orders", schema="dbo",
                columns=[Column("OrderID", "int64", "BigInt", ontology_id="200")],
                entity_type_id="E_ORDERS",
            ),
            "Customer": Table(
                name="Customer", schema="dbo",
                columns=[Column("CustomerID", "int64", "BigInt", ontology_id="300")],
                entity_type_id="E_CUSTOMER",
            ),
        }
        rel = Relationship("r1", from_table="Orders", from_col="CustomerID",
                           to_table="Customer", to_col="CustomerID")
        _, payload = build_relationship_type(rel, tables)
        subject_name, _, object_name = payload["name"].partition("_has_")
        assert tables[subject_name].entity_type_id == payload["source"]["entityTypeId"]
        assert tables[object_name].entity_type_id == payload["target"]["entityTypeId"]


class TestBuildContextualization:
    def _tables(self):
        return {
            "Orders": Table(
                name="Orders", schema="dbo",
                columns=[Column("OrderID", "int64", "BigInt", ontology_id="200"),
                         Column("CustomerID", "int64", "BigInt", ontology_id="201")],
                entity_type_id="E1",
            ),
            "Customer": Table(
                name="Customer", schema="dbo",
                columns=[Column("CustomerID", "int64", "BigInt", ontology_id="300")],
                entity_type_id="E2",
            ),
        }

    def test_bindings(self):
        tables = self._tables()
        rel = Relationship("r1", from_table="Orders", from_col="CustomerID",
                           to_table="Customer", to_col="CustomerID")
        result = build_contextualization(rel, "RT1", tables, "ws-1", "lh-1")
        assert result is not None
        ctx_id, payload = result

        # dataBindingTable = from (many/FK) table = ontology source
        assert payload["dataBindingTable"]["sourceTableName"] == "Orders"

        # sourceKeyRefBindings: ALL entityIdParts of source entity (Orders / FK)
        # — direct columns on the binding table
        assert len(payload["sourceKeyRefBindings"]) == 1
        assert payload["sourceKeyRefBindings"][0]["sourceColumnName"] == "OrderID"
        assert payload["sourceKeyRefBindings"][0]["targetPropertyId"] == "200"

        # targetKeyRefBindings: FK col → target entity's PK property (Customer)
        assert len(payload["targetKeyRefBindings"]) == 1
        assert payload["targetKeyRefBindings"][0]["sourceColumnName"] == "CustomerID"
        assert payload["targetKeyRefBindings"][0]["targetPropertyId"] == "300"

    def test_lakehouse_overrides(self):
        tables = self._tables()
        rel = Relationship("r1", from_table="Orders", from_col="CustomerID",
                           to_table="Customer", to_col="CustomerID")
        result = build_contextualization(
            rel, "RT1", tables, "ws-1", "lh-1",
            lakehouse_table="orders_raw",
            lakehouse_schema="staging",
            column_mappings={"OrderID": "order_id", "CustomerID": "customer_id"},
        )
        assert result is not None
        _, payload = result

        # dataBindingTable uses override
        assert payload["dataBindingTable"]["sourceTableName"] == "orders_raw"
        assert payload["dataBindingTable"]["sourceSchema"] == "staging"

        # sourceKeyRefBindings: source entity PK col (Orders.OrderID) mapped
        assert payload["sourceKeyRefBindings"][0]["sourceColumnName"] == "order_id"

        # targetKeyRefBindings: FK col mapped (Orders.CustomerID → Customer.CustomerID)
        assert payload["targetKeyRefBindings"][0]["sourceColumnName"] == "customer_id"


class TestBuildDefinitionParts:
    def test_part_count(self):
        tables = {"T1": _make_table("T1")}
        rels = []
        parts = build_definition_parts(tables, rels, "ws", "lh", "Test")
        # root + entity + binding + .platform = 4
        assert len(parts) == 4

    def test_parts_are_base64(self):
        tables = {"T1": _make_table("T1")}
        parts = build_definition_parts(tables, [], "ws", "lh", "Test")
        for p in parts:
            decoded = base64.b64decode(p["payload"]).decode("utf-8")
            assert decoded  # non-empty


class TestNormalizeRelationships:
    def _make_tables(self):
        return {
            "Customer": Table(
                name="Customer", schema="dbo",
                columns=[Column("CustomerID", "int64", "BigInt", ontology_id="10")],
                entity_type_id="E1",
            ),
            "SalesOrderHeader": Table(
                name="SalesOrderHeader", schema="dbo",
                columns=[
                    Column("SalesOrderID", "int64", "BigInt", ontology_id="20"),
                    Column("CustomerID", "int64", "BigInt", ontology_id="21"),
                ],
                entity_type_id="E2",
            ),
            "CustomerAddress": Table(
                name="CustomerAddress", schema="dbo",
                columns=[
                    Column("CustomerID", "int64", "BigInt", ontology_id="30"),
                    Column("AddressID", "int64", "BigInt", ontology_id="31"),
                ],
                entity_type_id="E3",
            ),
        }

    def test_already_correct_not_swapped(self):
        """SalesOrderDetail→SalesOrderHeader: from=FK, to=PK → no swap."""
        tables = self._make_tables()
        rels = [Relationship("r1", "SalesOrderHeader", "CustomerID", "Customer", "CustomerID")]
        normalised = normalize_relationships(tables, rels)
        # to=Customer has CustomerID PK → correct already
        assert normalised[0].from_table == "SalesOrderHeader"
        assert normalised[0].to_table == "Customer"

    def test_swaps_when_from_is_pk_side(self):
        """Customer→CustomerAddress where Customer.CustomerID is the PK → swap."""
        tables = self._make_tables()
        # TMDL says from=Customer, to=CustomerAddress (wrong: Customer is the PK side)
        rels = [Relationship("r1", "Customer", "CustomerID", "CustomerAddress", "CustomerID")]
        normalised = normalize_relationships(tables, rels)
        assert normalised[0].from_table == "CustomerAddress"
        assert normalised[0].to_table == "Customer"


class TestComputeEntityIdParts:
    def test_uses_explicit_pk(self):
        """When table has pk_column_names, use them regardless of relationships."""
        tables = {
            "SalesOrderDetail": Table(
                name="SalesOrderDetail", schema="dbo",
                columns=[
                    Column("SalesOrderID", "int64", "BigInt", ontology_id="50"),
                    Column("SalesOrderDetailID", "int64", "BigInt", ontology_id="51"),
                    Column("ProductID", "int64", "BigInt", ontology_id="52"),
                ],
                pk_column_names=["SalesOrderID", "SalesOrderDetailID"],
                entity_type_id="E1",
            ),
            "SalesOrderHeader": Table(
                name="SalesOrderHeader", schema="dbo",
                columns=[Column("SalesOrderID", "int64", "BigInt", ontology_id="60")],
                pk_column_names=["SalesOrderID"],
                entity_type_id="E2",
            ),
        }
        rels = [Relationship("r1", "SalesOrderDetail", "SalesOrderID",
                             "SalesOrderHeader", "SalesOrderID")]
        result = compute_entity_id_parts(tables, rels)
        assert result["SalesOrderDetail"] == ["50", "51"]  # Both PK cols
        assert result["SalesOrderHeader"] == ["60"]

    def test_falls_back_to_relationship_cols(self):
        """Without pk_column_names, falls back to relationship-based detection."""
        tables = {
            "Orders": Table(
                name="Orders", schema="dbo",
                columns=[
                    Column("OrderID", "int64", "BigInt", ontology_id="70"),
                    Column("CustID", "int64", "BigInt", ontology_id="71"),
                ],
                entity_type_id="E1",
            ),
            "Customers": Table(
                name="Customers", schema="dbo",
                columns=[Column("CustID", "int64", "BigInt", ontology_id="80")],
                entity_type_id="E2",
            ),
        }
        rels = [Relationship("r1", "Orders", "CustID", "Customers", "CustID")]
        result = compute_entity_id_parts(tables, rels)
        assert result["Customers"] == ["80"]  # to_col (PK side)
        assert result["Orders"] == ["71"]  # from_col (FK side)
