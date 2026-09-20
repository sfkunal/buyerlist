"""SQLite persistence for the fund index, plus the compact prompt rendering.

The index is the project's real asset: it is built once, committed to the repo,
and is the only thing standing between the ranking model and hallucinated fund
names. Everything here is deliberately boring.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..schemas import FundProfile

DB_PATH = Path("data/funds.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS funds (
    domain           TEXT PRIMARY KEY,
    firm_name        TEXT NOT NULL,
    website          TEXT NOT NULL,
    tier             TEXT NOT NULL,          -- deep | shallow
    discovered_via   TEXT,
    profile_json     TEXT NOT NULL,
    scraped_at       TEXT NOT NULL,
    pages_fetched    INTEGER,
    extract_conf     TEXT
);
CREATE TABLE IF NOT EXISTS build_log (
    domain     TEXT,
    ok         INTEGER,
    note       TEXT,
    at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_funds_tier ON funds(tier);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_fund(
    conn: sqlite3.Connection,
    *,
    domain: str,
    tier: str,
    discovered_via: str | None,
    profile: FundProfile,
    scraped_at: str,
    pages_fetched: int,
) -> None:
    conn.execute(
        """INSERT INTO funds
           (domain, firm_name, website, tier, discovered_via, profile_json,
            scraped_at, pages_fetched, extract_conf)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(domain) DO UPDATE SET
             firm_name=excluded.firm_name, website=excluded.website,
             tier=excluded.tier, profile_json=excluded.profile_json,
             scraped_at=excluded.scraped_at, pages_fetched=excluded.pages_fetched,
             extract_conf=excluded.extract_conf""",
        (
            domain,
            profile.firm_name,
            profile.website,
            tier,
            discovered_via,
            profile.model_dump_json(),
            scraped_at,
            pages_fetched,
            profile.extraction_confidence,
        ),
    )
    conn.commit()


def log_build(conn: sqlite3.Connection, domain: str, ok: bool, note: str, at: str) -> None:
    conn.execute(
        "INSERT INTO build_log (domain, ok, note, at) VALUES (?,?,?,?)",
        (domain, 1 if ok else 0, note, at),
    )
    conn.commit()


def existing_domains(conn: sqlite3.Connection) -> set[str]:
    return {r["domain"] for r in conn.execute("SELECT domain FROM funds")}


def load_funds(conn: sqlite3.Connection, tier: str | None = None) -> list[dict]:
    q = "SELECT * FROM funds"
    args: tuple = ()
    if tier:
        q += " WHERE tier = ?"
        args = (tier,)
    out = []
    for r in conn.execute(q, args):
        d = dict(r)
        d["profile"] = FundProfile.model_validate_json(d.pop("profile_json"))
        out.append(d)
    return out


def _usd(n: int | None) -> str:
    if n is None:
        return "?"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f}M"
    return f"{n / 1000:.0f}K"


def compact_line(row: dict) -> str:
    """One line per fund for the cached ranking prompt.

    Kept terse on purpose: this text is the stable, cached prefix of every
    ranking call, so its size sets the floor on per-run cost.
    """
    p: FundProfile = row["profile"]
    ebitda = f"EBITDA {_usd(p.ebitda_min_usd)}-{_usd(p.ebitda_max_usd)}"
    ev = f"EV {_usd(p.ev_min_usd)}-{_usd(p.ev_max_usd)}"
    geo = ", ".join(p.geographies[:3]) or "?"
    sectors = "; ".join(p.sector_themes[:4]) or "?"
    naics = ",".join(p.naics_coverage[:8]) or "?"

    line = (
        f"[{p.firm_name}] hq={p.hq_city or '?'},{p.hq_state or '?'} | {p.control} | "
        f"{ebitda} | {ev} | geo={geo} | naics={naics} | sectors={sectors}"
    )
    if p.exclusions:
        line += f" | excludes={'; '.join(p.exclusions[:3])}"

    # Platform + add-on history is what makes an add-on thesis possible, so it
    # earns its tokens even in the compact view.
    if p.platforms:
        plats = []
        # Show more than before: a fund's portfolio is the evidence an add-on
        # thesis is built from, and truncating it hides the best matches.
        for pl in p.platforms[:14]:
            naics = ",".join(pl.naics_codes[:3])
            tag = {"add_on": " ADD-ON", "platform": "", "unknown": ""}.get(pl.role, "")
            if pl.role == "add_on" and pl.parent_platform:
                tag = f" ADD-ON of {pl.parent_platform}"
            addons = f" +{len(pl.add_ons)}" if pl.add_ons else ""
            states = ""
            if pl.add_ons:
                seen = [a.state for a in pl.add_ons if a.state]
                if seen:
                    states = "(" + ",".join(sorted(set(seen))[:5]) + ")"
            plats.append(f"{pl.name}[{pl.sector}/{naics}]{tag}{addons}{states}")
        line += "\n    portfolio: " + " | ".join(plats)
    return line


def compact_index(conn: sqlite3.Connection, domains: list[str] | None = None) -> str:
    rows = load_funds(conn)
    if domains is not None:
        allow = set(domains)
        rows = [r for r in rows if r["domain"] in allow]
    rows.sort(key=lambda r: r["firm_name"].lower())
    return "\n".join(compact_line(r) for r in rows)


def stats(conn: sqlite3.Connection) -> dict:
    rows = load_funds(conn)
    plats = sum(len(r["profile"].platforms) for r in rows)
    addons = sum(len(pl.add_ons) for r in rows for pl in r["profile"].platforms)
    with_ebitda = sum(1 for r in rows if r["profile"].ebitda_min_usd is not None)
    return {
        "funds": len(rows),
        "deep": sum(1 for r in rows if r["tier"] == "deep"),
        "shallow": sum(1 for r in rows if r["tier"] == "shallow"),
        "platforms": plats,
        "add_ons": addons,
        "with_ebitda_band": with_ebitda,
    }
