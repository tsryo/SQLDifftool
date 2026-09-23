from sqldifftool.connection import ConnectionSpec, build_connection_string, friendly_error


def test_windows_auth_connection_string():
    spec = ConnectionSpec(server=r"sql-dev01\INST", database="Sales", driver="ODBC Driver 17 for SQL Server")
    assert build_connection_string(spec) == (
        r"DRIVER={ODBC Driver 17 for SQL Server};SERVER=sql-dev01\INST;DATABASE=Sales;Trusted_Connection=yes;"
        "Encrypt=no;TrustServerCertificate=no;APP=SQLDifftool")


def test_sql_login_escapes_special_characters():
    spec = ConnectionSpec(server="h,1433", database="db", auth="sql", username="u", password="p;w}d",
                          encrypt=True, driver="ODBC Driver 18 for SQL Server")
    cs = build_connection_string(spec)
    assert "UID=u;PWD={p;w}}d}" in cs
    assert "Encrypt=yes" in cs


def test_raw_connection_string_gets_database_and_driver():
    spec = ConnectionSpec(auth="connstr", connection_string="Server=x;Authentication=ActiveDirectoryInteractive",
                          database="Sales", driver="ODBC Driver 18 for SQL Server")
    assert build_connection_string(spec) == (
        "DRIVER={ODBC Driver 18 for SQL Server};Server=x;Authentication=ActiveDirectoryInteractive;DATABASE=Sales")
    with_db = ConnectionSpec(auth="connstr", connection_string="Driver={X};Server=x;Database=Other")
    assert build_connection_string(with_db, include_database=False) == "Driver={X};Server=x"


def test_friendly_error_strips_driver_noise():
    exc = Exception("08001", "[08001] [Microsoft][ODBC Driver 17 for SQL Server]Named Pipes Provider: Could not open "
                             "a connection to SQL Server [53].  (53) (SQLDriverConnect); [08001] [Microsoft]...")
    msg = friendly_error(exc)
    assert msg.startswith("Named Pipes Provider: Could not open a connection to SQL Server [53].")
    assert "Check the server name" in msg
    login = Exception("28000", "[28000] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]Login failed for user 'x'. (18456) (SQLDriverConnect)")
    assert friendly_error(login) == "Login failed for user 'x'."
