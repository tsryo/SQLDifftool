import pytest

from sqldifftool.compare import Comparison
from sqldifftool.normalize import CompareOptions
from sqldifftool.scriptparser import decode, parse_scripts, split_batches, split_statements, strip_storage
from sqldifftool.scripter import script_object

SSMS_DEV = """USE [Sales]
GO
/****** Object:  Table [dbo].[Orders]    Script Date: 24-9-2026 10:12:44 ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
CREATE TABLE [dbo].[Orders](
\t[OrderId] [int] IDENTITY(1,1) NOT NULL,
\t[CustomerId] [int] NOT NULL,
\t[Status] [tinyint] NOT NULL,
\t[Notes] [nvarchar](max) NULL,
 CONSTRAINT [PK_Orders] PRIMARY KEY CLUSTERED
(
\t[OrderId] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON, OPTIMIZE_FOR_SEQUENTIAL_KEY = OFF) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
GO
/****** Object:  Index [IX_Orders_Customer]    Script Date: 24-9-2026 10:12:44 ******/
CREATE NONCLUSTERED INDEX [IX_Orders_Customer] ON [dbo].[Orders]
(
\t[CustomerId] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON, OPTIMIZE_FOR_SEQUENTIAL_KEY = OFF) ON [PRIMARY]
GO
ALTER TABLE [dbo].[Orders] ADD  DEFAULT ((0)) FOR [Status]
GO
ALTER TABLE [dbo].[Orders] ADD  CONSTRAINT [DF__Orders__Notes__3B75D760]  DEFAULT (N'') FOR [Notes]
GO
ALTER TABLE [dbo].[Orders]  WITH CHECK ADD  CONSTRAINT [FK_Orders_Customers] FOREIGN KEY([CustomerId])
REFERENCES [dbo].[Customers] ([CustomerId])
ON DELETE CASCADE
GO
ALTER TABLE [dbo].[Orders] CHECK CONSTRAINT [FK_Orders_Customers]
GO
/****** Object:  StoredProcedure [dbo].[usp_Legacy]    Script Date: 24-9-2026 10:12:44 ******/
SET ANSI_NULLS OFF
GO
SET QUOTED_IDENTIFIER ON
GO
CREATE PROCEDURE [dbo].[usp_Legacy]
AS
BEGIN
    SELECT 'GO
' AS x; -- GO
    /*
GO
    */
    SELECT 1;
END
GO
CREATE TRIGGER [trg_Orders] ON [dbo].[Orders] AFTER INSERT AS SET NOCOUNT ON;
GO
ALTER TABLE [dbo].[Orders] DISABLE TRIGGER [trg_Orders]
GO
GRANT EXECUTE ON [dbo].[usp_Legacy] TO [app]
GO
"""


def ssms(text: str) -> bytes:
    return b"\xff\xfe" + text.encode("utf-16-le")


def test_decode_boms():
    assert decode(ssms("CREATE")) == "CREATE"
    assert decode(b"\xef\xbb\xbfCREATE") == "CREATE"
    assert decode("CRÉATE".encode("cp1252")) == "CRÉATE"
    assert decode("CREATE".encode("utf-16-le")) == "CREATE"


def test_go_inside_strings_and_comments_does_not_split():
    batches = split_batches("SELECT 'a\nGO\n'\n-- GO\n/*\nGO\n*/\ngo 2\nSELECT 2\n  GO  -- end\n")
    assert len(batches) == 2
    assert batches[1].strip() == "SELECT 2"


def test_statements_split_without_semicolons_but_modules_stay_whole():
    stmts = split_statements("SET ANSI_NULLS ON\nSET QUOTED_IDENTIFIER ON\nCREATE TABLE t (a int)\n"
                             "CREATE INDEX ix ON t (a)")
    assert len(stmts) == 4
    stmts = split_statements("CREATE OR ALTER PROC p AS SELECT 1; CREATE TABLE x (a int);")
    assert len(stmts) == 1


def test_strip_storage():
    text = ("CREATE TABLE t (a int, CONSTRAINT pk PRIMARY KEY (a) WITH (PAD_INDEX = OFF, IGNORE_DUP_KEY = ON) "
            "ON [PRIMARY], b int REFERENCES r (id) ON DELETE CASCADE) ON [ps]([a]) TEXTIMAGE_ON [PRIMARY]")
    assert strip_storage(text) == ("CREATE TABLE t (a int, CONSTRAINT pk PRIMARY KEY (a) WITH (IGNORE_DUP_KEY = ON), "
                                   "b int REFERENCES r (id) ON DELETE CASCADE)")


@pytest.fixture
def snap():
    return parse_scripts([("export/Sales.sql", ssms(SSMS_DEV))], "DEV")


def test_snapshot_meta(snap):
    assert (snap.label, snap.server, snap.database, snap.source) == ("DEV", "Script files", "Sales", "scripts")
    keys = {o.key for o in snap.objects}
    assert keys == {"table|dbo|orders", "procedure|dbo|usp_legacy", "trigger|dbo|trg_orders"}
    assert any("GRANT EXECUTE" in w["text"] for w in snap.warnings)


def test_table_gets_attached_statements_without_storage(snap):
    table = next(o for o in snap.objects if o.category == "Table")
    text = script_object(table, "", ignore_system_names=True)
    assert "Script Date" not in text
    assert "PRIMARY]" not in text and "PAD_INDEX" not in text and "TEXTIMAGE_ON" not in text
    assert "CHECK CONSTRAINT" not in text and "WITH CHECK" not in text
    assert "ALTER TABLE [dbo].[Orders] ADD DEFAULT (N'') FOR [Notes];" in text.replace("  ", " ")
    assert "ON DELETE CASCADE;" in text
    assert text.rstrip().endswith("[CustomerId] ASC\n);")
    shown = script_object(table, "", ignore_system_names=False)
    assert "[DF__Orders__Notes__3B75D760]" in shown


def test_modules(snap):
    proc = next(o for o in snap.objects if o.category == "Procedure")
    assert proc.ansi_nulls is False and proc.quoted_identifier is True
    assert proc.definition.startswith("CREATE PROCEDURE [dbo].[usp_Legacy]") and proc.definition.endswith("END")
    trig = next(o for o in snap.objects if o.category == "Trigger")
    assert trig.parent == "[dbo].[Orders]" and trig.disabled


def test_order_and_system_names_do_not_matter():
    acc = SSMS_DEV.replace("DF__Orders__Notes__3B75D760", "DF__Orders__Notes__5CD6CB2B")
    table_part, rest = acc.split("/****** Object:  Index", 1)
    index, rest = rest.split("ALTER TABLE [dbo].[Orders] ADD  DEFAULT", 1)
    acc = table_part + "ALTER TABLE [dbo].[Orders] ADD  DEFAULT" + rest + "/****** Object:  Index" + index
    left = parse_scripts([("dev.sql", SSMS_DEV.encode())], "DEV")
    right = parse_scripts([("acc.sql", ssms(acc))], "ACC")
    cmp = Comparison(left, right)
    assert cmp.entries["table|dbo|orders"].status == "identical"
    cmp.apply_options(CompareOptions(ignore_system_names=False))
    assert cmp.entries["table|dbo|orders"].status == "different"


def test_one_file_per_object_and_duplicates():
    files = [
        ("DEV/dbo.Customers.Table.sql", b"CREATE TABLE dbo.Customers (Id int NOT NULL)\nGO\n"),
        ("DEV/dbo.Customers.Index.sql", b"CREATE UNIQUE INDEX UX ON dbo.Customers (Id)\nGO\n"),
        ("DEV/dbo.vCustomers.View.sql", b"CREATE VIEW dbo.vCustomers AS SELECT Id FROM dbo.Customers\nGO\n"),
        ("DEV/all.sql", b"CREATE SCHEMA [staging] AUTHORIZATION [dbo]\nGO\nCREATE VIEW dbo.vCustomers AS SELECT 1 AS Id\n"
                        b"GO\nCREATE INDEX IX ON dbo.Missing (a)\nGO\nALTER DATABASE [x] SET COMPATIBILITY_LEVEL = 150\n"),
        ("DEV/readme.txt", b"not sql"),
    ]
    snap = parse_scripts(files, "DEV")
    assert snap.database == "DEV" and snap.server_version == "4 script files" and snap.compat_level == 150
    view = next(o for o in snap.objects if o.category == "View")
    assert "SELECT 1 AS Id" in view.definition  # all.sql sorts first
    table = next(o for o in snap.objects if o.category == "Table")
    assert table.attached == ["CREATE UNIQUE INDEX UX ON dbo.Customers (Id);"]
    texts = " ".join(w["text"] for w in snap.warnings)
    assert "defined more than once" in texts and "dbo.Missing" in texts
    assert any(o.category == "Schema" and o.name == "staging" for o in snap.objects)


def test_errors():
    with pytest.raises(ValueError, match="choose at least one .sql file"):
        parse_scripts([("a.txt", b"")], "DEV")
    with pytest.raises(ValueError, match="no CREATE statements"):
        parse_scripts([("a.sql", b"SELECT 1")], "DEV")


def test_sp_executesql_hint():
    script = b"IF NOT EXISTS (SELECT 1) EXEC dbo.sp_executesql @statement = N'CREATE VIEW v AS SELECT 1'\nGO\nCREATE TABLE t (a int)"
    snap = parse_scripts([("a.sql", script)], "DEV")
    assert any("Check for object existence" in w["text"] for w in snap.warnings)
