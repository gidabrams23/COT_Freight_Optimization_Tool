import io
import os
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote_plus

import pandas as pd


class PjSqlRefreshError(Exception):
    """Raised when PJ inventory SQL refresh cannot be completed."""


@dataclass
class PjSqlRefreshPayload:
    file_stream: io.StringIO
    filename: str
    row_count: int


_POC_SQL_DEFAULTS = {
    "PROGRADE_PJ_SQL_HOST": "syn-dataestate-prd-eus-001.sql.azuresynapse.net",
    "PROGRADE_PJ_SQL_DATABASE": "DWHSYNSQLDP",
    "PROGRADE_PJ_SQL_DRIVER": "ODBC Driver 18 for SQL Server",
    "PROGRADE_PJ_SQL_PORT": "1433",
    "PROGRADE_PJ_SQL_ENCRYPT": "yes",
    "PROGRADE_PJ_SQL_TRUST_SERVER_CERTIFICATE": "yes",
    "PROGRADE_PJ_SQL_CONNECT_TIMEOUT_SEC": "30",
    "PROGRADE_PJ_SQL_AUTHENTICATION": "ActiveDirectoryInteractive",
    "PROGRADE_PJ_SQL_QUERY": (
        "SELECT "
        "s.id, "
        "s.arrived, "
        "s.availordered, "
        "s.availphysical, "
        "s.itemid, "
        "s.onorder, "
        "s.ordered, "
        "s.physicalinvent, "
        "s.physicalvalue, "
        "s.postedqty, "
        "s.reservordered, "
        "s.reservphysical, "
        "s.configid, "
        "s.inventlocationid, "
        "s.inventserialid, "
        "s.inventsiteid, "
        "s.inventstatusid, "
        "s.wmslocationid, "
        "s.dataareaid, "
        "e.hstrailerconfiglongitemid "
        "FROM dbo.inventsum s "
        "LEFT JOIN dbo.ecoresconfiguration e ON e.name = s.configid "
        "WHERE s.availphysical > 0 AND hstrailerconfiglongitemid <> 'NULL';"
    ),
}


def _configured_text(name, default=""):
    value = os.environ.get(name)
    if value is not None and str(value).strip():
        return str(value).strip()
    fallback = _POC_SQL_DEFAULTS.get(name, default)
    return str(fallback).strip()


def is_pj_sql_refresh_configured():
    return bool(_configured_text("PROGRADE_PJ_SQL_HOST") and _configured_text("PROGRADE_PJ_SQL_DATABASE") and _configured_text("PROGRADE_PJ_SQL_QUERY"))


def _build_sqlalchemy_engine():
    try:
        from sqlalchemy import create_engine
    except Exception as exc:  # pragma: no cover - dependency path
        raise PjSqlRefreshError(
            "SQLAlchemy dependency is missing. Run: pip install -r requirements.txt"
        ) from exc

    host = _configured_text("PROGRADE_PJ_SQL_HOST")
    database = _configured_text("PROGRADE_PJ_SQL_DATABASE")
    if not host or not database:
        raise PjSqlRefreshError("Missing PJ SQL host/database configuration.")

    driver = _configured_text("PROGRADE_PJ_SQL_DRIVER", "ODBC Driver 18 for SQL Server")
    port = _configured_text("PROGRADE_PJ_SQL_PORT", "1433")
    encrypt = _configured_text("PROGRADE_PJ_SQL_ENCRYPT", "yes")
    trust_server_cert = _configured_text("PROGRADE_PJ_SQL_TRUST_SERVER_CERTIFICATE", "yes")
    timeout = _configured_text("PROGRADE_PJ_SQL_CONNECT_TIMEOUT_SEC", "30")
    authentication = _configured_text("PROGRADE_PJ_SQL_AUTHENTICATION", "ActiveDirectoryInteractive")
    user_principal = _configured_text("PROGRADE_PJ_SQL_USER_PRINCIPAL", "")
    username = _configured_text("PROGRADE_PJ_SQL_USERNAME", "")
    password = _configured_text("PROGRADE_PJ_SQL_PASSWORD", "")

    connection_parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={host},{port}",
        f"DATABASE={database}",
        f"Encrypt={encrypt}",
        f"TrustServerCertificate={trust_server_cert}",
        f"Connection Timeout={timeout}",
    ]

    if authentication:
        connection_parts.append(f"Authentication={authentication}")

    if username and password:
        connection_parts.append(f"UID={username}")
        connection_parts.append(f"PWD={password}")
    elif user_principal:
        connection_parts.append(f"UID={user_principal}")

    odbc_connect = quote_plus(";".join(connection_parts))
    sqlalchemy_url = f"mssql+pyodbc:///?odbc_connect={odbc_connect}"
    return create_engine(sqlalchemy_url, pool_pre_ping=True, future=True)


def _fetch_pj_inventory_dataframe():
    try:
        from sqlalchemy import text
    except Exception as exc:  # pragma: no cover - dependency path
        raise PjSqlRefreshError(
            "SQLAlchemy dependency is missing. Run: pip install -r requirements.txt"
        ) from exc

    query_text = _configured_text("PROGRADE_PJ_SQL_QUERY")
    if not query_text:
        raise PjSqlRefreshError("Missing PJ SQL query text.")

    sql = text(query_text)
    try:
        engine = _build_sqlalchemy_engine()
        with engine.connect() as connection:
            frame = pd.read_sql_query(sql, connection)
    except Exception as exc:  # pragma: no cover - integration path
        raise PjSqlRefreshError(str(exc)) from exc

    if frame is None or frame.empty:
        raise PjSqlRefreshError("PJ SQL query returned no rows.")

    return frame.fillna("")


def build_pj_inventory_upload_payload():
    frame = _fetch_pj_inventory_dataframe()
    csv_stream = io.StringIO()
    frame.to_csv(csv_stream, index=False)
    csv_stream.seek(0)
    filename = f"pj_inventory_sql_refresh_{date.today().isoformat()}.csv"
    csv_stream.filename = filename
    return PjSqlRefreshPayload(
        file_stream=csv_stream,
        filename=filename,
        row_count=len(frame.index),
    )
