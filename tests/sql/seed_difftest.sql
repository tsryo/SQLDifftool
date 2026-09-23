/*
    Creates two small databases with known schema differences, to try SQL Difftool
    against a real server:  DiffTest_DEV (left)  vs  DiffTest_ACC (right).

    Run in SSMS or:  sqlcmd -S <server> -E -i tests\sql\seed_difftest.sql
    Needs permission to create databases. Drops and recreates both databases.

    Expected result with default options (ignore whitespace + system constraint names):
      Different   : dbo.Orders (column + index), dbo.usp_GetOrders, dbo.fn_OrderTotal,
                    dbo.trg_Orders_Audit (disabled in ACC), dbo.Phone (length)
      Only in DEV : dbo.Invoices, dbo.usp_NewReport
      Only in ACC : dbo.LegacyImport
      Identical   : dbo.Customers and dbo.AuditLog (only auto-named constraints differ),
                    dbo.usp_Reformatted (only whitespace / CREATE OR ALTER differs), dbo.vActiveCustomers
*/
SET NOCOUNT ON;
GO
USE master;
GO
IF DB_ID(N'DiffTest_DEV') IS NOT NULL BEGIN ALTER DATABASE DiffTest_DEV SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE DiffTest_DEV; END
IF DB_ID(N'DiffTest_ACC') IS NOT NULL BEGIN ALTER DATABASE DiffTest_ACC SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE DiffTest_ACC; END
GO
CREATE DATABASE DiffTest_DEV;
GO
CREATE DATABASE DiffTest_ACC;
GO

/* ======================================================================= DEV */
USE DiffTest_DEV;
GO
CREATE TYPE dbo.Phone FROM nvarchar(25) NULL;
GO
CREATE TABLE dbo.Customers (
    CustomerId int IDENTITY(1, 1) NOT NULL CONSTRAINT PK_Customers PRIMARY KEY,
    Name nvarchar(200) NOT NULL,
    Phone dbo.Phone NULL,
    CreatedAt datetime2(3) NOT NULL DEFAULT (sysutcdatetime())   -- system-named default
);
CREATE TABLE dbo.Orders (
    OrderId int IDENTITY(1, 1) NOT NULL CONSTRAINT PK_Orders PRIMARY KEY,
    CustomerId int NOT NULL CONSTRAINT FK_Orders_Customers REFERENCES dbo.Customers (CustomerId),
    OrderDate date NOT NULL,
    Total decimal(18, 2) NOT NULL CONSTRAINT CK_Orders_Total CHECK (Total >= 0),
    DiscountPct decimal(5, 2) NULL
);
CREATE INDEX IX_Orders_CustomerId ON dbo.Orders (CustomerId, OrderDate DESC) INCLUDE (Total);
CREATE TABLE dbo.Invoices (
    InvoiceId bigint NOT NULL CONSTRAINT PK_Invoices PRIMARY KEY,
    OrderId int NOT NULL CONSTRAINT FK_Invoices_Orders REFERENCES dbo.Orders (OrderId),
    Amount decimal(18, 2) NOT NULL
);
CREATE TABLE dbo.AuditLog (Id int IDENTITY PRIMARY KEY, TableName sysname NOT NULL, KeyValue nvarchar(20) NOT NULL, ChangedAt datetime2 NOT NULL);
GO
CREATE VIEW dbo.vActiveCustomers
AS
SELECT c.CustomerId, c.Name FROM dbo.Customers AS c;
GO
CREATE PROCEDURE dbo.usp_GetOrders
    @CustomerId int,
    @FromDate date = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SELECT o.OrderId, o.OrderDate, o.Total, o.DiscountPct
    FROM dbo.Orders AS o
    WHERE o.CustomerId = @CustomerId
      AND (@FromDate IS NULL OR o.OrderDate >= @FromDate)
    ORDER BY o.OrderDate DESC;
END
GO
CREATE PROCEDURE dbo.usp_Reformatted
    @Id int
AS
BEGIN
    SELECT CustomerId, Name
    FROM dbo.Customers
    WHERE CustomerId = @Id;
END
GO
CREATE PROCEDURE dbo.usp_NewReport
AS
SELECT CustomerId, COUNT(*) AS Orders FROM dbo.Orders GROUP BY CustomerId;
GO
CREATE FUNCTION dbo.fn_OrderTotal (@OrderId int)
RETURNS decimal(18, 2)
AS
BEGIN
    RETURN (SELECT Total * (1 - ISNULL(DiscountPct, 0) / 100) FROM dbo.Orders WHERE OrderId = @OrderId);
END
GO
CREATE TRIGGER dbo.trg_Orders_Audit ON dbo.Orders AFTER UPDATE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO dbo.AuditLog (TableName, KeyValue, ChangedAt)
    SELECT N'Orders', CAST(i.OrderId AS nvarchar(20)), SYSUTCDATETIME() FROM inserted AS i;
END
GO

/* ======================================================================= ACC */
USE DiffTest_ACC;
GO
CREATE TYPE dbo.Phone FROM nvarchar(20) NULL;
GO
CREATE TABLE dbo.Customers (
    CustomerId int IDENTITY(1, 1) NOT NULL CONSTRAINT PK_Customers PRIMARY KEY,
    Name nvarchar(200) NOT NULL,
    Phone dbo.Phone NULL,
    CreatedAt datetime2(3) NOT NULL DEFAULT (sysutcdatetime())   -- gets a different auto-name
);
CREATE TABLE dbo.Orders (
    OrderId int IDENTITY(1, 1) NOT NULL CONSTRAINT PK_Orders PRIMARY KEY,
    CustomerId int NOT NULL CONSTRAINT FK_Orders_Customers REFERENCES dbo.Customers (CustomerId),
    OrderDate date NOT NULL,
    Total decimal(18, 2) NOT NULL CONSTRAINT CK_Orders_Total CHECK (Total >= 0)
);
CREATE INDEX IX_Orders_CustomerId ON dbo.Orders (CustomerId, OrderDate DESC);
CREATE TABLE dbo.LegacyImport (Id int NULL, Payload nvarchar(max) NULL, ImportedAt datetime NOT NULL DEFAULT (getdate()));
CREATE TABLE dbo.AuditLog (Id int IDENTITY PRIMARY KEY, TableName sysname NOT NULL, KeyValue nvarchar(20) NOT NULL, ChangedAt datetime2 NOT NULL);
GO
CREATE VIEW dbo.vActiveCustomers
AS
SELECT c.CustomerId, c.Name FROM dbo.Customers AS c;
GO
CREATE PROCEDURE dbo.usp_GetOrders
    @CustomerId int
AS
BEGIN
    SET NOCOUNT ON;
    SELECT o.OrderId, o.OrderDate, o.Total
    FROM dbo.Orders AS o
    WHERE o.CustomerId = @CustomerId
    ORDER BY o.OrderDate DESC;
END
GO
CREATE OR ALTER PROCEDURE dbo.usp_Reformatted
	@Id int
AS
BEGIN
	SELECT CustomerId, Name FROM dbo.Customers WHERE CustomerId = @Id;
END
GO
CREATE FUNCTION dbo.fn_OrderTotal (@OrderId int)
RETURNS decimal(18, 2)
AS
BEGIN
    RETURN (SELECT Total FROM dbo.Orders WHERE OrderId = @OrderId);
END
GO
CREATE TRIGGER dbo.trg_Orders_Audit ON dbo.Orders AFTER UPDATE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO dbo.AuditLog (TableName, KeyValue, ChangedAt)
    SELECT N'Orders', CAST(i.OrderId AS nvarchar(20)), SYSUTCDATETIME() FROM inserted AS i;
END
GO
DISABLE TRIGGER dbo.trg_Orders_Audit ON dbo.Orders;
GO
USE master;
GO
PRINT 'Created DiffTest_DEV and DiffTest_ACC.';
