import io
import os
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote_plus

import pandas as pd


class CotSqlRefreshError(Exception):
    """Raised when the SQL-backed order refresh cannot be completed."""


@dataclass
class SqlRefreshPayload:
    file_stream: io.StringIO
    filename: str
    row_count: int


_REQUIRED_ENV_VARS = (
    "COT_SQL_HOST",
    "COT_SQL_DATABASE",
    "COT_SQL_USERNAME",
    "COT_SQL_PASSWORD",
)


def is_sql_refresh_configured():
    return all((os.environ.get(name) or "").strip() for name in _REQUIRED_ENV_VARS)


def _env_text(name, default=""):
    value = os.environ.get(name)
    if value is None:
        return str(default)
    return str(value).strip()


def _build_sqlalchemy_engine():
    try:
        from sqlalchemy import create_engine
    except Exception as exc:  # pragma: no cover - dependency path
        raise CotSqlRefreshError(
            "SQLAlchemy dependency is missing. Run: pip install -r requirements.txt"
        ) from exc

    missing = [name for name in _REQUIRED_ENV_VARS if not _env_text(name)]
    if missing:
        raise CotSqlRefreshError(
            "Missing required SQL settings: " + ", ".join(missing)
        )

    host = _env_text("COT_SQL_HOST")
    database = _env_text("COT_SQL_DATABASE")
    username = _env_text("COT_SQL_USERNAME")
    password = _env_text("COT_SQL_PASSWORD")
    driver = _env_text("COT_SQL_DRIVER", "ODBC Driver 18 for SQL Server")
    port = _env_text("COT_SQL_PORT", "1433")
    encrypt = _env_text("COT_SQL_ENCRYPT", "yes")
    trust_server_cert = _env_text("COT_SQL_TRUST_SERVER_CERTIFICATE", "yes")
    timeout = _env_text("COT_SQL_CONNECT_TIMEOUT_SEC", "30")

    connection_parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={host},{port}",
        f"DATABASE={database}",
        f"UID={username}",
        f"PWD={password}",
        f"Encrypt={encrypt}",
        f"TrustServerCertificate={trust_server_cert}",
        f"Connection Timeout={timeout}",
    ]
    odbc_connect = quote_plus(";".join(connection_parts))
    sqlalchemy_url = f"mssql+pyodbc:///?odbc_connect={odbc_connect}"
    return create_engine(sqlalchemy_url, pool_pre_ping=True, future=True)


def _fetch_orders_dataframe():
    try:
        from sqlalchemy import text
    except Exception as exc:  # pragma: no cover - dependency path
        raise CotSqlRefreshError(
            "SQLAlchemy dependency is missing. Run: pip install -r requirements.txt"
        ) from exc

    sql = text(
        """
        WITH order_loads AS (
            SELECT
                CAST(src.[order_number] AS NVARCHAR(80)) AS [order_number],
                MAX(NULLIF(LTRIM(RTRIM(src.[load_name])), '')) AS [load_name]
            FROM (
                SELECT [order_number], [load_name]
                FROM [dbo].[COTLoadsOrdersVINs]
                WHERE [order_number] IS NOT NULL
                UNION ALL
                SELECT [order_number], [load_name]
                FROM [dbo].[COTShippedNotRecNotInv]
                WHERE [order_number] IS NOT NULL
            ) src
            GROUP BY CAST(src.[order_number] AS NVARCHAR(80))
        )
        SELECT
            o.[CREATEDATE],
            o.[SHIPVIA],
            o.[salesorder],
            o.[LINENUM],
            o.[bin],
            o.[itemnum],
            o.[desc],
            o.[ordqty],
            o.[QTYRMN],
            o.[sales],
            o.[plant],
            o.[cust_name],
            o.[store],
            o.[CNUM3],
            o.[CNAME],
            o.[state],
            o.[city],
            o.[zip_code],
            o.[SADDR1],
            o.[SADDR2],
            COALESCE(order_loads.[load_name], 'Not on Load') AS [load_name]
        FROM [dbo].[COT CY Orders Query (Loads App)] o
        LEFT JOIN order_loads
            ON CAST(o.[salesorder] AS NVARCHAR(80)) = order_loads.[order_number]
        WHERE o.[store] NOT IN ('CARRYONLA2', 'COTSAMPLE')
          AND o.[plant] NOT LIKE '%PARTS%'
          AND o.[plant] NOT LIKE '%ZCOT%'
          AND (o.[salecode] IS NULL OR o.[salecode] NOT LIKE '%ACC%')
        ORDER BY o.[salesorder] ASC
        """
    )
    try:
        engine = _build_sqlalchemy_engine()
        with engine.connect() as connection:
            frame = pd.read_sql_query(sql, connection)
    except Exception as exc:  # pragma: no cover - integration path
        raise CotSqlRefreshError(str(exc)) from exc
    if frame is None:
        raise CotSqlRefreshError("Query returned no data frame.")
    if "load_name" in frame.columns:
        frame["load_name"] = frame["load_name"].fillna("Not on Load")
    return frame.fillna("")


def build_orders_upload_payload():
    frame = _fetch_orders_dataframe()
    csv_stream = io.StringIO()
    frame.to_csv(csv_stream, index=False)
    csv_stream.seek(0)
    filename = f"cot_sql_refresh_{date.today().isoformat()}.csv"
    csv_stream.filename = filename
    return SqlRefreshPayload(
        file_stream=csv_stream,
        filename=filename,
        row_count=len(frame.index),
    )
