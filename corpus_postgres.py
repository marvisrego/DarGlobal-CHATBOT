"""Read the active release from the synchronized private Postgres corpus."""

from psycopg import Error, connect
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from corpus_snapshot import COLUMNS, CorpusUnavailable, load_snapshot, normalize_record
from property_search import valid_citation


class PostgresCorpus:
    def __init__(self, dsn, snapshot):
        try:
            self._settings = conninfo_to_dict(dsn)
        except Error:
            raise RuntimeError("DATABASE_URL is not a valid Postgres connection setting.") from None
        if self._settings.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
            self._settings["sslmode"] = "require"
        self._settings["connect_timeout"] = 5
        # The shipped snapshot pins release membership. All answer data comes from the database.
        self._ids = list(load_snapshot(snapshot))

    def __call__(self):
        try:
            with connect(**self._settings, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute("SET LOCAL statement_timeout = '4s'")
                fields = ", ".join(COLUMNS)
                rows = connection.execute(
                    f"SELECT {fields} FROM public.property_corpus WHERE record_id = ANY(%s)",
                    (self._ids,),
                ).fetchall()
        except Error:
            raise CorpusUnavailable("database_read_failed") from None
        if len(rows) != len(self._ids) or {row["record_id"] for row in rows} != set(self._ids):
            raise CorpusUnavailable("incomplete_release")

        records = {}
        try:
            for row in rows:
                metadata = row.pop("metadata")
                row.pop("searchable_text")
                row["collected_at"] = row["collected_at"].isoformat()
                if row["source_as_of"] is not None:
                    row["source_as_of"] = row["source_as_of"].isoformat()
                record = {**metadata, **row}
                normalize_record(record)
                if not valid_citation(record):
                    raise ValueError("Invalid corpus citation.")
                records[record["record_id"]] = record
        except (TypeError, ValueError, KeyError, AttributeError):
            raise CorpusUnavailable("invalid_release_record") from None
        return [records[record_id] for record_id in self._ids]
