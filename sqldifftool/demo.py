"""Two built-in sample snapshots (a "Sales" database on DEV and ACC) for --demo mode and tests.

The differences are chosen to exercise every status and normalization option:
added/changed columns, a changed procedure, whitespace-only, comment-only and
case-only changes, differing system-generated constraint names, a disabled
trigger, and objects that exist on one side only.
"""
from __future__ import annotations

from .model import (CheckDef, ColumnDef, DbObject, DefaultDef, ForeignKeyDef, IndexColumn, IndexDef,
                    KeyConstraintDef, Snapshot, TableDef)
from .scripter import script_alias_type, script_schema, script_sequence, script_synonym

COLLATION = "SQL_Latin1_General_CP1_CI_AS"


def _col(name, data_type, nullable=True, **kw) -> ColumnDef:
    return ColumnDef(name, data_type, nullable, **kw)


def _pk(name, *cols, system_named=False) -> KeyConstraintDef:
    return KeyConstraintDef(name, True, "CLUSTERED", [IndexColumn(c) for c in cols], system_named)


def _module(category, schema, name, definition, **kw) -> DbObject:
    return DbObject(category, schema, name, definition=definition.strip("\n"), **kw)


def _customers(dev: bool) -> DbObject:
    created_df = "DF__Customers__Creat__2A4B4B5E" if dev else "DF__Customers__Creat__5CD6CB2B"
    return DbObject("Table", "dbo", "Customers", table=TableDef(
        columns=[
            _col("CustomerId", "INT", False, identity="1, 1"),
            _col("Name", "NVARCHAR(200)", False, collation=COLLATION),
            _col("Email", "NVARCHAR(320)", collation=COLLATION),
            _col("Phone", "[dbo].[Phone]"),
            _col("CreatedAt", "DATETIME2(3)", False, default=DefaultDef(created_df, "(sysutcdatetime())", True)),
            _col("IsActive", "BIT", False, default=DefaultDef("DF_Customers_IsActive", "((1))")),
        ],
        keys=[_pk("PK_Customers", "CustomerId")],
        indexes=[IndexDef("IX_Customers_Email", unique=True, columns=[IndexColumn("Email")],
                          filter="([Email] IS NOT NULL)")],
    ))


def _orders(dev: bool) -> DbObject:
    cols = [
        _col("OrderId", "INT", False, identity="1, 1"),
        _col("CustomerId", "INT", False),
        _col("OrderDate", "DATE", False),
        _col("Status", "TINYINT", False, default=DefaultDef("DF_Orders_Status", "((0))")),
        _col("Total", "DECIMAL(18, 2)", False),
    ]
    if dev:
        cols.append(_col("DiscountPct", "DECIMAL(5, 2)"))
    cols.append(_col("Notes", "NVARCHAR(MAX)" if dev else "NVARCHAR(1000)", collation=COLLATION))
    checks = [CheckDef("CK_Orders_Total", "([Total]>=(0))")]
    if dev:
        checks.append(CheckDef("CK_Orders_Discount", "([DiscountPct]>=(0) AND [DiscountPct]<=(100))"))
    include = ["Status", "Total"] if dev else ["Status"]
    return DbObject("Table", "dbo", "Orders", table=TableDef(
        columns=cols,
        keys=[_pk("PK_Orders", "OrderId")],
        checks=checks,
        foreign_keys=[ForeignKeyDef("FK_Orders_Customers", ["CustomerId"], "[dbo].[Customers]", ["CustomerId"],
                                    not_trusted=not dev)],
        indexes=[
            IndexDef("IX_Orders_CustomerId", columns=[IndexColumn("CustomerId"), IndexColumn("OrderDate", True)],
                     included=include),
            IndexDef("IX_Orders_Status", columns=[IndexColumn("Status")], filter="([Status]<(3))"),
        ],
    ))


def _order_lines(dev: bool) -> DbObject:
    qty_ck = "CK__OrderLine__Qty__3E52440B" if dev else "CK__OrderLine__Qty__6E01572D"
    return DbObject("Table", "dbo", "OrderLines", table=TableDef(
        columns=[
            _col("OrderId", "INT", False),
            _col("LineNo", "SMALLINT", False),
            _col("ProductId", "INT", False),
            _col("Qty", "INT", False),
            _col("UnitPrice", "DECIMAL(18, 2)", False),
            _col("LineTotal", "", computed="([Qty]*[UnitPrice])", persisted=True),
        ],
        keys=[KeyConstraintDef("PK_OrderLines", True, "CLUSTERED", [IndexColumn("OrderId"), IndexColumn("LineNo")])],
        checks=[CheckDef(qty_ck, "([Qty]>(0))", system_named=True)],
        foreign_keys=[
            ForeignKeyDef("FK_OrderLines_Orders", ["OrderId"], "[dbo].[Orders]", ["OrderId"], on_delete="CASCADE"),
            ForeignKeyDef("FK_OrderLines_Products", ["ProductId"], "[dbo].[Products]", ["ProductId"]),
        ],
    ))


def _products(dev: bool) -> DbObject:
    return DbObject("Table", "dbo", "Products", table=TableDef(
        columns=[
            _col("ProductId", "INT", False, identity="1, 1"),
            _col("Sku", "VARCHAR(40)", False,
                 collation=COLLATION if dev else "SQL_Latin1_General_CP1_CS_AS"),
            _col("Name", "NVARCHAR(200)" if dev else "NVARCHAR(100)", False, collation=COLLATION),
            _col("Price", "DECIMAL(18, 2)", False),
            _col("IsDiscontinued", "BIT", False, default=DefaultDef("DF_Products_IsDiscontinued", "((0))")),
        ],
        keys=[_pk("PK_Products", "ProductId"),
              KeyConstraintDef("UQ_Products_Sku", False, "NONCLUSTERED", [IndexColumn("Sku")])],
        checks=[CheckDef("CK_Products_Price", "([Price]>=(0))" if dev else "([Price]>(0))")],
    ))


def _price_history() -> list[DbObject]:
    cols = [
        _col("ProductId", "INT", False),
        _col("Price", "DECIMAL(18, 2)", False),
        _col("ValidFrom", "DATETIME2(7)", False, generated_always="ROW START", hidden=True),
        _col("ValidTo", "DATETIME2(7)", False, generated_always="ROW END", hidden=True),
    ]
    history_cols = [_col(c.name, c.data_type, False) for c in cols]
    return [
        DbObject("Table", "dbo", "PriceHistory", table=TableDef(
            columns=cols, keys=[_pk("PK_PriceHistory", "ProductId")], period=["ValidFrom", "ValidTo"],
            history_table="[dbo].[PriceHistory_History]")),
        DbObject("Table", "dbo", "PriceHistory_History", table=TableDef(
            columns=history_cols,
            indexes=[IndexDef("ix_PriceHistory_History", "CLUSTERED",
                              columns=[IndexColumn("ValidTo"), IndexColumn("ValidFrom")])])),
    ]


def _invoices() -> DbObject:
    return DbObject("Table", "sales", "Invoices", table=TableDef(
        columns=[
            _col("InvoiceId", "BIGINT", False,
                 default=DefaultDef("DF_Invoices_InvoiceId", "(NEXT VALUE FOR [dbo].[InvoiceNumber])")),
            _col("OrderId", "INT", False),
            _col("IssuedAt", "DATETIME2(0)", False),
            _col("Amount", "DECIMAL(18, 2)", False),
        ],
        keys=[_pk("PK_Invoices", "InvoiceId")],
        foreign_keys=[ForeignKeyDef("FK_Invoices_Orders", ["OrderId"], "[dbo].[Orders]", ["OrderId"])],
        indexes=[IndexDef("IX_Invoices_IssuedAt", columns=[IndexColumn("IssuedAt", True)], included=["Amount"])],
    ))


def _customer_load() -> DbObject:
    return DbObject("Table", "staging", "CustomerLoad", table=TableDef(columns=[
        _col("LoadId", "INT", False, identity="1, 1"),
        _col("RawJson", "NVARCHAR(MAX)", collation=COLLATION),
        _col("LoadedAt", "DATETIME2(0)", False, default=DefaultDef("DF__CustomerL__Loade__4222D4EF", "(sysutcdatetime())", True)),
    ]))


def _legacy_import() -> DbObject:
    return DbObject("Table", "dbo", "LegacyImport", table=TableDef(columns=[
        _col("Id", "INT"),
        _col("Payload", "NVARCHAR(MAX)", collation=COLLATION),
        _col("ImportedAt", "DATETIME", False, default=DefaultDef("DF__LegacyImp__Impor__19DFD96B", "(getdate())", True)),
    ]))


def _order_line_list(dev: bool) -> DbObject:
    pk = "PK__OrderLin__B40CC6CD1B0907CE" if dev else "PK__OrderLin__B40CC6CD7F2BE32F"
    ck = "CK__OrderLineL__Qty__1CF15040" if dev else "CK__OrderLineL__Qty__00200768"
    return DbObject("Type", "dbo", "OrderLineList", table=TableDef(
        columns=[_col("ProductId", "INT", False), _col("Qty", "INT", False)],
        keys=[_pk(pk, "ProductId", system_named=True)],
        checks=[CheckDef(ck, "([Qty]>(0))", system_named=True)],
    ))


# --- views -----------------------------------------------------------------

V_ACTIVE_CUSTOMERS = """
CREATE VIEW [dbo].[vActiveCustomers]
AS
SELECT c.CustomerId, c.Name, c.Email
FROM dbo.Customers AS c
WHERE c.IsActive = 1;
"""

V_ORDER_SUMMARY_DEV = """
CREATE VIEW [sales].[vOrderSummary]
WITH SCHEMABINDING
AS
SELECT o.CustomerId,
       YEAR(o.OrderDate)  AS OrderYear,
       COUNT_BIG(*)       AS OrderCount,
       SUM(o.Total)       AS Revenue,
       SUM(o.Total * (1 - ISNULL(o.DiscountPct, 0) / 100)) AS NetRevenue
FROM dbo.Orders AS o
WHERE o.Status <> 9
GROUP BY o.CustomerId, YEAR(o.OrderDate);
"""

V_ORDER_SUMMARY_ACC = """
CREATE VIEW [sales].[vOrderSummary]
WITH SCHEMABINDING
AS
SELECT o.CustomerId,
       YEAR(o.OrderDate)  AS OrderYear,
       COUNT_BIG(*)       AS OrderCount,
       SUM(o.Total)       AS Revenue
FROM dbo.Orders AS o
WHERE o.Status < 9
GROUP BY o.CustomerId, YEAR(o.OrderDate);
"""

# --- procedures ------------------------------------------------------------

P_GET_ORDERS_DEV = """
CREATE PROCEDURE [dbo].[usp_GetOrders]
    @CustomerId INT,
    @FromDate   DATE = NULL,
    @Status     TINYINT = NULL
AS
BEGIN
    SET NOCOUNT ON;

    SELECT o.OrderId,
           o.OrderDate,
           o.Status,
           o.Total,
           o.DiscountPct
    FROM dbo.Orders AS o
    WHERE o.CustomerId = @CustomerId
      AND (@FromDate IS NULL OR o.OrderDate >= @FromDate)
      AND (@Status IS NULL OR o.Status = @Status)
    ORDER BY o.OrderDate DESC;
END
"""

P_GET_ORDERS_ACC = """
CREATE PROCEDURE [dbo].[usp_GetOrders]
    @CustomerId INT,
    @FromDate   DATE = NULL
AS
BEGIN
    SET NOCOUNT ON;

    SELECT o.OrderId,
           o.OrderDate,
           o.Status,
           o.Total
    FROM dbo.Orders AS o
    WHERE o.CustomerId = @CustomerId
      AND (@FromDate IS NULL OR o.OrderDate >= @FromDate)
    ORDER BY o.OrderDate DESC;
END
"""

P_UPDATE_CUSTOMER_DEV = """
CREATE PROCEDURE [dbo].[usp_UpdateCustomer]
    @CustomerId INT,
    @Name       NVARCHAR(200),
    @Email      NVARCHAR(320)
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE dbo.Customers
    SET Name  = @Name,
        Email = @Email
    WHERE CustomerId = @CustomerId;
END
"""

# Same procedure deployed with CREATE OR ALTER, tabs, CRLF line endings and trailing spaces.
P_UPDATE_CUSTOMER_ACC = (
    "CREATE OR ALTER PROCEDURE [dbo].[usp_UpdateCustomer]\r\n"
    "\t@CustomerId INT,\r\n"
    "\t@Name NVARCHAR(200),\r\n"
    "\t@Email NVARCHAR(320)\r\n"
    "AS\r\n"
    "BEGIN\r\n"
    "\tSET NOCOUNT ON;   \r\n"
    "\r\n"
    "\tUPDATE dbo.Customers\r\n"
    "\tSET Name = @Name, Email = @Email\r\n"
    "\tWHERE CustomerId = @CustomerId;\r\n"
    "END\r\n"
)

P_ARCHIVE_ORDERS = """
CREATE PROCEDURE [dbo].[usp_ArchiveOrders]
    @Days INT = 365,
    @BatchSize INT = 5000
AS
BEGIN
    SET NOCOUNT ON;
    {comment}
    DELETE TOP (@BatchSize) o
    FROM dbo.Orders AS o
    WHERE o.OrderDate < DATEADD(DAY, -@Days, CAST(SYSUTCDATETIME() AS date))
      AND o.Status = 4; -- only closed orders
END
"""

P_GET_CUSTOMER_DEV = """
CREATE PROCEDURE [dbo].[usp_GetCustomer]
    @CustomerId INT
AS
BEGIN
    SET NOCOUNT ON;
    SELECT c.CustomerId, c.Name, c.Email, c.Phone
    FROM dbo.Customers AS c
    WHERE c.CustomerId = @CustomerId;
END
"""

P_GET_CUSTOMER_ACC = """
create procedure [dbo].[usp_GetCustomer]
    @CustomerId int
as
begin
    set nocount on;
    select c.CustomerId, c.Name, c.Email, c.Phone
    from dbo.Customers as c
    where c.CustomerId = @CustomerId;
end
"""

P_CLEANUP_STAGING = """
CREATE PROCEDURE [dbo].[usp_CleanupStaging]
    @OlderThanHours INT = 24
AS
BEGIN
    SET NOCOUNT ON;
    DELETE FROM staging.CustomerLoad
    WHERE LoadedAt < DATEADD(HOUR, -@OlderThanHours, SYSUTCDATETIME());
END
"""

P_OLD_REPORT = """
CREATE PROCEDURE [dbo].[usp_OldReport]
AS
BEGIN
    /* Replaced by sales.vOrderSummary in release 2026.3 */
    SELECT CustomerId, COUNT(*) AS Orders, SUM(Total) AS Revenue
    FROM dbo.Orders
    GROUP BY CustomerId;
END
"""

# --- functions -------------------------------------------------------------

F_ORDER_NET_TOTAL_DEV = """
CREATE FUNCTION [dbo].[fn_OrderNetTotal] (@OrderId INT)
RETURNS DECIMAL(18, 2)
AS
BEGIN
    DECLARE @Total DECIMAL(18, 2);

    SELECT @Total = o.Total * (1 - ISNULL(o.DiscountPct, 0) / 100)
    FROM dbo.Orders AS o
    WHERE o.OrderId = @OrderId;

    RETURN ISNULL(@Total, 0);
END
"""

F_ORDER_NET_TOTAL_ACC = """
CREATE FUNCTION [dbo].[fn_OrderNetTotal] (@OrderId INT)
RETURNS DECIMAL(18, 2)
AS
BEGIN
    DECLARE @Total DECIMAL(18, 2);

    SELECT @Total = o.Total
    FROM dbo.Orders AS o
    WHERE o.OrderId = @OrderId;

    RETURN @Total;
END
"""

F_CUSTOMER_ORDERS = """
CREATE FUNCTION [dbo].[fn_CustomerOrders] (@CustomerId INT)
RETURNS TABLE
AS
RETURN
    SELECT o.OrderId, o.OrderDate, o.Total
    FROM dbo.Orders AS o
    WHERE o.CustomerId = @CustomerId;
"""

# --- triggers --------------------------------------------------------------

TR_ORDERS_AUDIT = """
CREATE TRIGGER [dbo].[trg_Orders_Audit]
ON [dbo].[Orders]
AFTER UPDATE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO dbo.AuditLog (TableName, KeyValue, ChangedAt)
    SELECT N'Orders', CAST(i.OrderId AS nvarchar(20)), SYSUTCDATETIME()
    FROM inserted AS i;
END
"""

TR_BLOCK_DROP_TABLE = """
CREATE TRIGGER [trg_BlockDropTable]
ON DATABASE
FOR DROP_TABLE
AS
BEGIN
    RAISERROR (N'Dropping tables is not allowed. Disable trg_BlockDropTable first.', 16, 1);
    ROLLBACK;
END
"""


def _objects(dev: bool) -> list[DbObject]:
    objs = [
        _customers(dev), _orders(dev), _order_lines(dev), _products(dev), *_price_history(),
        _module("View", "dbo", "vActiveCustomers", V_ACTIVE_CUSTOMERS),
        _module("View", "sales", "vOrderSummary", V_ORDER_SUMMARY_DEV if dev else V_ORDER_SUMMARY_ACC,
                indexes=[IndexDef("IX_vOrderSummary", "CLUSTERED", unique=True,
                                  columns=[IndexColumn("CustomerId"), IndexColumn("OrderYear")])]),
        _module("Procedure", "dbo", "usp_GetOrders", P_GET_ORDERS_DEV if dev else P_GET_ORDERS_ACC),
        _module("Procedure", "dbo", "usp_UpdateCustomer", P_UPDATE_CUSTOMER_DEV if dev else P_UPDATE_CUSTOMER_ACC),
        _module("Procedure", "dbo", "usp_ArchiveOrders", P_ARCHIVE_ORDERS.format(comment=(
            "-- Batch size raised to 5000 to keep the nightly job under 10 minutes (SALES-412)" if dev else
            "-- Deletes in batches to keep the transaction log small"))),
        _module("Procedure", "dbo", "usp_GetCustomer", P_GET_CUSTOMER_DEV if dev else P_GET_CUSTOMER_ACC),
        DbObject("Procedure", "dbo", "usp_PayrollExport", note="Definition is encrypted (WITH ENCRYPTION)"),
        _module("Function", "dbo", "fn_OrderNetTotal", F_ORDER_NET_TOTAL_DEV if dev else F_ORDER_NET_TOTAL_ACC),
        _module("Function", "dbo", "fn_CustomerOrders", F_CUSTOMER_ORDERS),
        _module("Trigger", "dbo", "trg_Orders_Audit", TR_ORDERS_AUDIT, parent="[dbo].[Orders]", disabled=not dev),
        _module("Trigger", "", "trg_BlockDropTable", TR_BLOCK_DROP_TABLE, parent="DATABASE"),
        DbObject("Type", "dbo", "Phone", definition=script_alias_type(
            "dbo", "Phone", "NVARCHAR(25)" if dev else "NVARCHAR(20)", True)),
        _order_line_list(dev),
        DbObject("Sequence", "dbo", "InvoiceNumber", definition=script_sequence(
            "dbo", "InvoiceNumber", "BIGINT", "100000", "1", "1", "9223372036854775807", False,
            True, 50 if dev else None)),
        DbObject("Synonym", "dbo", "ArchivedOrders", definition=script_synonym(
            "dbo", "ArchivedOrders", f"[SalesArchive_{'DEV' if dev else 'ACC'}].[dbo].[Orders]")),
        DbObject("Schema", "", "sales", definition=script_schema("sales", "dbo")),
    ]
    if dev:
        objs += [
            _invoices(), _customer_load(),
            _module("Procedure", "dbo", "usp_CleanupStaging", P_CLEANUP_STAGING),
            DbObject("Schema", "", "staging", definition=script_schema("staging", "dbo")),
        ]
    else:
        objs += [_legacy_import(), _module("Procedure", "dbo", "usp_OldReport", P_OLD_REPORT)]
    return objs


def build_demo_snapshots() -> tuple[Snapshot, Snapshot]:
    encrypted = [{"level": "info", "text": (
        "1 module(s) are encrypted (WITH ENCRYPTION); their definitions cannot be compared.")}]
    dev = Snapshot("DEV", "sql-dev01", "Sales", "SQL Server 2022 (16.0.4135.4)", "Developer Edition (64-bit)",
                   COLLATION, 160, "2026-09-23T09:14:02", list(encrypted), _objects(True))
    acc = Snapshot("ACC", "sql-acc01", "Sales", "SQL Server 2019 (15.0.4375.4)", "Standard Edition (64-bit)",
                   COLLATION, 150, "2026-09-23T09:14:03", list(encrypted), _objects(False))
    return dev, acc
