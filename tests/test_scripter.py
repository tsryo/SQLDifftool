from sqldifftool.model import (CheckDef, ColumnDef, DbObject, DefaultDef, ForeignKeyDef, IndexColumn, IndexDef,
                               KeyConstraintDef, TableDef)
from sqldifftool.scripter import format_type, script_module, script_object, script_sequence, script_table

DB_COLLATION = "SQL_Latin1_General_CP1_CI_AS"


def orders_table(default_name="DF__Orders__Statu__3B75D760", system_named=True):
    return DbObject("Table", "dbo", "Orders", table=TableDef(
        columns=[
            ColumnDef("OrderId", "INT", False, identity="1, 1"),
            ColumnDef("Code", "VARCHAR(20)", False, collation="Latin1_General_CS_AS"),
            ColumnDef("Name", "NVARCHAR(100)", True, collation=DB_COLLATION),
            ColumnDef("Status", "TINYINT", False, default=DefaultDef(default_name, "((0))", system_named)),
            ColumnDef("Total", "", computed="([Qty]*[Price])", persisted=True),
        ],
        keys=[KeyConstraintDef("UQ_Orders_Code", False, "NONCLUSTERED", [IndexColumn("Code")]),
              KeyConstraintDef("PK_Orders", True, "CLUSTERED", [IndexColumn("OrderId")])],
        checks=[CheckDef("CK_Orders_Status", "([Status]<(10))", disabled=True, not_trusted=True)],
        foreign_keys=[ForeignKeyDef("FK_Orders_Customers", ["CustomerId"], "[dbo].[Customers]", ["CustomerId"],
                                    on_delete="SET_NULL", not_trusted=True)],
        indexes=[IndexDef("IX_Orders_Status", columns=[IndexColumn("Status", True)], included=["Total", "Name"],
                          filter="([Status]>(0))", disabled=True)],
    ))


EXPECTED_ORDERS = """CREATE TABLE [dbo].[Orders] (
    [OrderId] INT IDENTITY(1, 1) NOT NULL,
    [Code] VARCHAR(20) COLLATE Latin1_General_CS_AS NOT NULL,
    [Name] NVARCHAR(100) NULL,
    [Status] TINYINT NOT NULL DEFAULT ((0)),
    [Total] AS ([Qty]*[Price]) PERSISTED,
    CONSTRAINT [PK_Orders] PRIMARY KEY CLUSTERED ([OrderId] ASC),
    CONSTRAINT [UQ_Orders_Code] UNIQUE NONCLUSTERED ([Code] ASC),
    CONSTRAINT [CK_Orders_Status] CHECK ([Status]<(10)),
    CONSTRAINT [FK_Orders_Customers] FOREIGN KEY ([CustomerId]) REFERENCES [dbo].[Customers] ([CustomerId]) ON DELETE SET NULL
);
ALTER TABLE [dbo].[Orders] NOCHECK CONSTRAINT [CK_Orders_Status];
ALTER TABLE [dbo].[Orders] WITH NOCHECK CHECK CONSTRAINT [FK_Orders_Customers];
CREATE NONCLUSTERED INDEX [IX_Orders_Status] ON [dbo].[Orders] ([Status] DESC) INCLUDE ([Name], [Total]) WHERE ([Status]>(0));
ALTER INDEX [IX_Orders_Status] ON [dbo].[Orders] DISABLE;"""


def test_script_table_full_example():
    assert script_table(orders_table(), DB_COLLATION) == EXPECTED_ORDERS


def test_system_named_default_is_hidden_only_when_option_is_on():
    a = script_table(orders_table("DF__Orders__Statu__3B75D760"), DB_COLLATION, ignore_system_names=True)
    b = script_table(orders_table("DF__Orders__Statu__7A8B9C0D"), DB_COLLATION, ignore_system_names=True)
    assert a == b
    shown = script_table(orders_table("DF__Orders__Statu__3B75D760"), DB_COLLATION, ignore_system_names=False)
    assert "CONSTRAINT [DF__Orders__Statu__3B75D760] DEFAULT ((0))" in shown


def test_user_named_default_is_always_shown():
    text = script_table(orders_table("DF_Orders_Status", system_named=False), DB_COLLATION)
    assert "CONSTRAINT [DF_Orders_Status] DEFAULT ((0))" in text


def test_format_type():
    assert format_type("nvarchar", None, False, -1, 0, 0) == "NVARCHAR(MAX)"
    assert format_type("nvarchar", None, False, 100, 0, 0) == "NVARCHAR(50)"
    assert format_type("varbinary", None, False, 16, 0, 0) == "VARBINARY(16)"
    assert format_type("decimal", None, False, 9, 18, 2) == "DECIMAL(18, 2)"
    assert format_type("datetime2", None, False, 8, 27, 7) == "DATETIME2(7)"
    assert format_type("int", None, False, 4, 10, 0) == "INT"
    assert format_type("Phone", "dbo", True, 40, 0, 0) == "[dbo].[Phone]"


def test_module_flags_and_disabled_trigger():
    trg = DbObject("Trigger", "dbo", "trg", parent="[dbo].[Orders]", definition="CREATE TRIGGER dbo.trg ...",
                   quoted_identifier=False, disabled=True)
    assert script_module(trg) == ("SET QUOTED_IDENTIFIER OFF;\nGO\nCREATE TRIGGER dbo.trg ...\nGO\n"
                                  "DISABLE TRIGGER [dbo].[trg] ON [dbo].[Orders];")


def test_encrypted_module_shows_note():
    obj = DbObject("Procedure", "dbo", "p", note="Definition is encrypted (WITH ENCRYPTION)")
    assert script_object(obj, DB_COLLATION) == "-- Definition is encrypted (WITH ENCRYPTION)"


def test_table_type_hides_system_names():
    tt = DbObject("Type", "dbo", "IdList", table=TableDef(
        columns=[ColumnDef("Id", "INT", False)],
        keys=[KeyConstraintDef("PK__IdList__3214EC07A1B2C3D4", True, "CLUSTERED", [IndexColumn("Id")], True)]))
    assert script_object(tt, DB_COLLATION) == (
        "CREATE TYPE [dbo].[IdList] AS TABLE (\n    [Id] INT NOT NULL,\n    PRIMARY KEY CLUSTERED ([Id] ASC)\n);")


def test_sequence():
    assert script_sequence("dbo", "Seq", "BIGINT", "1", "1", "1", "100", False, False, None).splitlines()[-1] == "    NO CACHE;"
