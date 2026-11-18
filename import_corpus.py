"""Validate a real JSONL snapshot and upsert it into the private corpus table."""

import argparse
from collections import Counter
from pathlib import Path

from dotenv import dotenv_values

from corpus_snapshot import COLUMNS, ROOT, SNAPSHOT, load_snapshot


MIGRATION = ROOT / "supabase" / "migrations" / "20261118112500_create_property_corpus.sql"
TABLE = "public.property_corpus"
API_ROLES = ("anon", "authenticated", "service_role")


def ensure_table(connection):
    connection.execute("SET LOCAL lock_timeout = '6s'")
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (TABLE,))
    relation = connection.execute(
        "SELECT relkind FROM pg_class WHERE oid = to_regclass(%s)", (TABLE,)
    ).fetchone()
    reused = relation is not None
    if reused:
        if relation[0] != "r":
            raise ValueError(f"{TABLE} exists but is not a regular table.")
        connection.execute("LOCK TABLE public.property_corpus IN SHARE ROW EXCLUSIVE MODE")
        columns = connection.execute(
            """
            SELECT attname, format_type(atttypid, atttypmod),
                   attnotnull AND NOT atthasdef AND attidentity = '' AND attgenerated = ''
            FROM pg_attribute
            WHERE attrelid = to_regclass(%s) AND attnum > 0 AND NOT attisdropped
            """,
            (TABLE,),
        ).fetchall()
        types = {name: data_type for name, data_type, _ in columns}
        extra_required = any(required and name not in COLUMNS for name, _, required in columns)
        primary_key = connection.execute(
            """
            SELECT ARRAY(
                SELECT attr.attname::text
                FROM unnest(idx.indkey) WITH ORDINALITY AS entry(attnum, position)
                JOIN pg_attribute AS attr
                  ON attr.attrelid = idx.indrelid AND attr.attnum = entry.attnum
                WHERE entry.position <= idx.indnkeyatts
                ORDER BY entry.position
            )
            FROM pg_index AS idx
            WHERE idx.indrelid = to_regclass(%s) AND idx.indisprimary
            """,
            (TABLE,),
        ).fetchone()
        if any(types.get(key) != value for key, value in COLUMNS.items()) or extra_required or primary_key != (["record_id"],):
            raise ValueError(f"{TABLE} has an incompatible schema. No changes were made.")

    connection.execute(MIGRATION.read_text(encoding="utf-8"), prepare=False)
    rls = connection.execute("SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(%s)", (TABLE,)).fetchone()
    for role in API_ROLES:
        acl = connection.execute(
            """
            SELECT has_table_privilege(%s, %s, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'),
                   has_any_column_privilege(%s, %s, 'SELECT,INSERT,UPDATE,REFERENCES')
            """,
            (role, TABLE, role, TABLE),
        ).fetchone()
        if any(acl):
            raise ValueError(f"{TABLE} still grants access to the {role} API role. No changes were made.")
    if rls != (True,):
        raise ValueError(f"Could not verify row level security on {TABLE}.")
    return reused


def import_rows(dsn, rows):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.types.json import Jsonb

    try:
        parameters = conninfo_to_dict(dsn)
    except psycopg.Error:
        raise ValueError("DATABASE_URL is not a valid PostgreSQL connection string.") from None
    tls = parameters.get("sslmode")
    sslmode = tls if tls in {"require", "verify-ca", "verify-full"} else "require"
    names = ", ".join(COLUMNS)
    placeholders = ", ".join(f"%({name})s" for name in COLUMNS)
    assignments = ", ".join(f"{name} = EXCLUDED.{name}" for name in COLUMNS if name != "record_id")
    sql = f"""
        INSERT INTO public.property_corpus ({names}) VALUES ({placeholders})
        ON CONFLICT (record_id) DO UPDATE SET {assignments}
    """
    with psycopg.connect(dsn, sslmode=sslmode, connect_timeout=10, prepare_threshold=None) as connection:
        reused = ensure_table(connection)
        with connection.cursor() as cursor:
            cursor.executemany(sql, [
                {**row, "metadata": Jsonb(row["metadata"]), "supporting_sources": Jsonb(row["supporting_sources"])}
                for row in rows.values()
            ])
        stored = connection.execute(
            f"SELECT {names} FROM public.property_corpus WHERE record_id = ANY(%s)", (list(rows),)
        ).fetchall()
        actual = {row[0]: row for row in stored}
        expected = {key: tuple(row[name] for name in COLUMNS) for key, row in rows.items()}
        if actual != expected:
            raise ValueError("Postgres read-back differs from the validated snapshot. The import was rolled back.")
        total = connection.execute("SELECT count(*) FROM public.property_corpus").fetchone()[0]
    return reused, total


def safe_database_error(error):
    detail = str(error).casefold()
    state = error.sqlstate
    if state == "28P01" or "password authentication failed" in detail:
        return "Database authentication failed. Check the password and username in local DATABASE_URL."
    if not state:
        return ("Could not connect to Supabase from this workspace. For an IPv4-only network, "
                "use the Session pooler URI from Supabase Dashboard > Connect in local DATABASE_URL.")
    return f"Postgres rejected the import (SQLSTATE {state}). No import transaction was committed."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=SNAPSHOT)
    parser.add_argument("--dry-run", action="store_true", help="Validate all records without connecting")
    args = parser.parse_args()
    try:
        rows = load_snapshot(args.input)
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"Snapshot validation failed: {error}\n")
    counts = Counter(row["record_type"] for row in rows.values())
    guides = {row["canonical_url"] for row in rows.values() if row["record_type"] == "buyer_guide"}
    print(f"Validated {len(rows)} real rows: {counts['project']} projects and "
          f"{counts['buyer_guide']} sections across {len(guides)} guides.", flush=True)
    if args.dry_run:
        return
    dsn = (dotenv_values(ROOT / ".env", interpolate=False).get("DATABASE_URL") or "").strip()
    if not dsn:
        parser.exit(1, "Paste your Supabase Postgres connection string into DATABASE_URL in local .env.\n")
    try:
        import psycopg
    except ImportError:
        parser.exit(1, "Install the import dependency with python -m pip install -r requirements-import.txt.\n")
    try:
        reused, total = import_rows(dsn, rows)
    except psycopg.Error as error:
        parser.exit(1, safe_database_error(error) + "\n")
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    print(f"{'Reused' if reused else 'Created'} {TABLE}. Verified {len(rows)} snapshot rows by exact read-back, "
          f"including source metadata and supporting citations. Total table rows: {total}.")
    if total != len(rows):
        print(f"Preserved {total - len(rows)} rows outside this snapshot.")


if __name__ == "__main__":
    main()
