import sqlite3
from datetime import datetime, date
from decimal import Decimal


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    result = {}
    for key in row.keys():
        value = row[key]
        if isinstance(value, (datetime, date)):
            result[key] = value.isoformat()
        elif isinstance(value, Decimal):
            result[key] = str(value)
        else:
            result[key] = value
    return result


def rows_to_list(rows: list[sqlite3.Row]) -> list[dict]:
    return [row_to_dict(r) for r in rows]
