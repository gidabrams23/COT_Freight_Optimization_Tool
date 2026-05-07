import io
import os
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote_plus

import pandas as pd


class BtSqlRefreshError(Exception):
    """Raised when Big Tex SQL refresh cannot be completed."""


@dataclass
class BtSqlRefreshPayload:
    file_stream: io.StringIO
    filename: str
    row_count: int


_POC_SQL_DEFAULTS = {
    "PROGRADE_BT_SQL_HOST": "atwaztsdb01.database.windows.net",
    "PROGRADE_BT_SQL_DATABASE": "TakeStockDB",
    "PROGRADE_BT_SQL_DRIVER": "ODBC Driver 18 for SQL Server",
    "PROGRADE_BT_SQL_PORT": "1433",
    "PROGRADE_BT_SQL_ENCRYPT": "yes",
    "PROGRADE_BT_SQL_TRUST_SERVER_CERTIFICATE": "yes",
    "PROGRADE_BT_SQL_CONNECT_TIMEOUT_SEC": "30",
    "PROGRADE_BT_SQL_AUTHENTICATION": "",
    "PROGRADE_BT_SQL_USERNAME": "readuser_atw",
    "PROGRADE_BT_SQL_PASSWORD": "Readonly#20!0",
    "PROGRADE_BT_SQL_QUERY": (
        "SELECT "
        "s.conum, s.itemnum, s.serid, s.whse, s.transtype, s.cost, "
        "s.physdate, s.memo, s.onhand, s.committed_, s.wip, "
        "s.intransit, s.discrepant, s.lastusedate, s.TS_LastUpdated, "
        "i.description1, i.majorcat, mc.majorcatdesc "
        "FROM icserial s "
        "LEFT JOIN icitem i ON i.itemnum = s.itemnum AND i.conum = s.conum "
        "LEFT JOIN icmajorcat mc ON mc.majorcat = i.majorcat AND mc.conum = s.conum "
        "WHERE s.onhand > 0 AND s.conum = '001';"
    ),
}


def _configured_text(name, default=""):
    value = os.environ.get(name)
    if value is not None and str(value).strip():
        return str(value).strip()
    fallback = _POC_SQL_DEFAULTS.get(name, default)
    return str(fallback).strip()


def is_bt_sql_refresh_configured():
    return bool(
        _configured_text("PROGRADE_BT_SQL_HOST")
        and _configured_text("PROGRADE_BT_SQL_DATABASE")
        and _configured_text("PROGRADE_BT_SQL_QUERY")
    )


def _build_sqlalchemy_engine():
    try:
        from sqlalchemy import create_engine
    except Exception as exc:  # pragma: no cover - dependency path
        raise BtSqlRefreshError(
            "SQLAlchemy dependency is missing. Run: pip install -r requirements.txt"
        ) from exc

    host = _configured_text("PROGRADE_BT_SQL_HOST")
    database = _configured_text("PROGRADE_BT_SQL_DATABASE")
    if not host or not database:
        raise BtSqlRefreshError("Missing Big Tex SQL host/database configuration.")

    driver = _configured_text("PROGRADE_BT_SQL_DRIVER", "ODBC Driver 18 for SQL Server")
    port = _configured_text("PROGRADE_BT_SQL_PORT", "1433")
    encrypt = _configured_text("PROGRADE_BT_SQL_ENCRYPT", "yes")
    trust_server_cert = _configured_text("PROGRADE_BT_SQL_TRUST_SERVER_CERTIFICATE", "yes")
    timeout = _configured_text("PROGRADE_BT_SQL_CONNECT_TIMEOUT_SEC", "30")
    authentication = _configured_text("PROGRADE_BT_SQL_AUTHENTICATION", "")
    username = _configured_text("PROGRADE_BT_SQL_USERNAME", "")
    password = _configured_text("PROGRADE_BT_SQL_PASSWORD", "")

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

    odbc_connect = quote_plus(";".join(connection_parts))
    sqlalchemy_url = f"mssql+pyodbc:///?odbc_connect={odbc_connect}"
    return create_engine(sqlalchemy_url, pool_pre_ping=True, future=True)


def _fetch_bt_inventory_dataframe():
    try:
        from sqlalchemy import text
    except Exception as exc:  # pragma: no cover - dependency path
        raise BtSqlRefreshError(
            "SQLAlchemy dependency is missing. Run: pip install -r requirements.txt"
        ) from exc

    query_text = _configured_text("PROGRADE_BT_SQL_QUERY")
    if not query_text:
        raise BtSqlRefreshError("Missing Big Tex SQL query text.")

    sql = text(query_text)
    try:
        engine = _build_sqlalchemy_engine()
        with engine.connect() as connection:
            frame = pd.read_sql_query(sql, connection)
    except Exception as exc:  # pragma: no cover - integration path
        raise BtSqlRefreshError(str(exc)) from exc

    if frame is None or frame.empty:
        raise BtSqlRefreshError("Big Tex SQL query returned no rows.")

    return frame.fillna("")


def build_bt_inventory_upload_payload():
    frame = _fetch_bt_inventory_dataframe()
    csv_stream = io.StringIO()
    frame.to_csv(csv_stream, index=False)
    csv_stream.seek(0)
    filename = f"bigtex_inventory_sql_refresh_{date.today().isoformat()}.csv"
    csv_stream.filename = filename
    return BtSqlRefreshPayload(
        file_stream=csv_stream,
        filename=filename,
        row_count=len(frame.index),
    )
