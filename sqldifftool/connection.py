"""Connection settings for one side of a comparison, and ODBC connection-string building."""
from __future__ import annotations

import re
from dataclasses import dataclass, fields

LOGIN_TIMEOUT_SECONDS = 15
QUERY_TIMEOUT_SECONDS = 300


@dataclass
class ConnectionSpec:
    label: str = "Left"
    server: str = ""
    database: str = ""
    auth: str = "windows"            # windows | sql | connstr
    username: str = ""
    password: str = ""
    connection_string: str = ""
    encrypt: bool = False
    trust_server_certificate: bool = False
    driver: str = ""                 # empty = pick the newest installed driver

    @classmethod
    def from_dict(cls, data: dict | None) -> ConnectionSpec:
        data = data or {}
        spec = cls(**{f.name: data[f.name] for f in fields(cls) if f.name in data and data[f.name] is not None})
        spec.server, spec.database = spec.server.strip(), spec.database.strip()
        return spec

    def validate(self) -> None:
        if self.auth == "connstr":
            if not self.connection_string.strip():
                raise ValueError(f"{self.label}: connection string is required")
            return
        if not self.server:
            raise ValueError(f"{self.label}: server is required")
        if self.auth == "sql" and not self.username:
            raise ValueError(f"{self.label}: user name is required for SQL login")


def _pyodbc():
    try:
        import pyodbc
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError("pyodbc is not installed: pip install -r requirements.txt") from exc
    return pyodbc


def sql_server_drivers() -> list[str]:
    try:
        return [d for d in _pyodbc().drivers() if "SQL Server" in d]
    except RuntimeError:
        return []


def pick_driver() -> str:
    drivers = sql_server_drivers()
    versioned = []
    for d in drivers:
        m = re.fullmatch(r"ODBC Driver (\d+) for SQL Server", d)
        if m:
            versioned.append((int(m.group(1)), d))
    if versioned:
        return max(versioned)[1]
    for fallback in ("SQL Server Native Client 11.0", "SQL Server"):
        if fallback in drivers:
            return fallback
    raise RuntimeError("No SQL Server ODBC driver found. Install 'ODBC Driver 18 for SQL Server'.")


def _odbc_value(value: str) -> str:
    if any(c in value for c in ";{}=") or value != value.strip():
        return "{" + value.replace("}", "}}") + "}"
    return value


def build_connection_string(spec: ConnectionSpec, include_database: bool = True) -> str:
    if spec.auth == "connstr":
        cs = spec.connection_string.strip().rstrip(";")
        if not re.search(r"(?i)(^|;)\s*driver\s*=", cs):
            cs = f"DRIVER={{{spec.driver or pick_driver()}}};{cs}"
        has_db = re.search(r"(?i)(^|;)\s*(database|initial catalog)\s*=", cs)
        if include_database and spec.database and not has_db:
            cs += f";DATABASE={_odbc_value(spec.database)}"
        if not include_database and has_db:
            cs = re.sub(r"(?i)(^|;)\s*(database|initial catalog)\s*=[^;]*", r"\1", cs)
            cs = re.sub(r";{2,}", ";", cs).strip(";")
        return cs
    parts = [("DRIVER", "{" + (spec.driver or pick_driver()) + "}"), ("SERVER", _odbc_value(spec.server))]
    if include_database and spec.database:
        parts.append(("DATABASE", _odbc_value(spec.database)))
    if spec.auth == "sql":
        parts += [("UID", _odbc_value(spec.username)), ("PWD", _odbc_value(spec.password))]
    else:
        parts.append(("Trusted_Connection", "yes"))
    parts += [("Encrypt", "yes" if spec.encrypt else "no"),
              ("TrustServerCertificate", "yes" if spec.trust_server_certificate else "no"),
              ("APP", "SQLDifftool")]
    return ";".join(f"{k}={v}" for k, v in parts)


def connect(spec: ConnectionSpec, include_database: bool = True):
    spec.validate()
    pyodbc = _pyodbc()
    conn = pyodbc.connect(build_connection_string(spec, include_database),
                          timeout=LOGIN_TIMEOUT_SECONDS, autocommit=True)
    conn.timeout = QUERY_TIMEOUT_SECONDS
    return conn


_VENDOR_PREFIX = re.compile(r"\[(Microsoft|ODBC[^\]]*|SQL Server|SQL Server Native Client[^\]]*|unixODBC|[0-9A-Z]{5})\]")
_UNREACHABLE = ("Could not open a connection", "TCP Provider", "Named Pipes Provider", "server was not found",
                "Login timeout expired")


def friendly_error(exc: Exception) -> str:
    """Turn a pyodbc error into one readable sentence."""
    msg = exc.args[1] if len(exc.args) > 1 and isinstance(exc.args[1], str) else str(exc)
    msg = _VENDOR_PREFIX.sub("", msg)
    msg = re.sub(r"\s*\(\d+\)\s*(\(SQL\w+\))?", "", msg)
    msg = re.sub(r"\s+", " ", msg.split(";")[0]).strip()
    if any(s in msg for s in _UNREACHABLE):
        msg += " Check the server name (host\\instance or host,port) and that it is reachable from this machine."
    return msg or exc.__class__.__name__
