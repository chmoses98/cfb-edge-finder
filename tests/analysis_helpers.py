"""Read an analysis artifact back the way its documented rules say to.

Reconstructing tickers here, from `ticker_prefix + t` and from full
tickers, is deliberate: it exercises the same rule the artifact tells a
reader to use. A helper that peeked at the source packets instead would
pass even if the artifact's own instructions produced the wrong ticker.
"""

from __future__ import annotations

from typing import Any


def analysis_tickers(document: dict[str, Any]) -> set[str]:
    tickers: set[str] = set()
    for game in document["games"]:
        for block in game["markets"].values():
            columns = block["columns"]
            prefix = block.get("ticker_prefix")
            key = "t" if prefix is not None else "ticker"
            index = columns.index(key)
            for row in block["rows"]:
                tickers.add((prefix or "") + str(row[index]))
    return tickers


def analysis_rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Every row as a dict, with its family and game attached."""
    rows: list[dict[str, Any]] = []
    for game in document["games"]:
        for family, block in game["markets"].items():
            columns = block["columns"]
            prefix = block.get("ticker_prefix")
            for row in block["rows"]:
                record = dict(zip(columns, row, strict=True))
                record["ticker"] = (
                    (prefix or "") + str(record["t"]) if prefix is not None else record["ticker"]
                )
                record["family"] = family
                record["game_key"] = game["game_key"]
                if "quote_age_s" not in record and "quote_age_s" in block:
                    record["quote_age_s"] = block["quote_age_s"]
                rows.append(record)
    return rows
