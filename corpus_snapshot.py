"""Validate and normalize the real DarGlobal and Wasalt corpus."""

from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
SNAPSHOT = ROOT / "snapshots" / "property_live.jsonl"
RELEASES = {
    "v1": ("property_live.jsonl", 11, 14, 7),
    "v2": ("property_live_v2.jsonl", 14, 23, 9),
}


class CorpusUnavailable(Exception):
    def __init__(self, category):
        self.category = category
        super().__init__("The property corpus is temporarily unavailable.")


def live_snapshot(version):
    filename = RELEASES[version][0]
    tracked = ROOT / "deploy_data" / filename
    return tracked if tracked.is_file() else SNAPSHOT.with_name(filename)


def snapshot_digest(path):
    return sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


COLUMNS = {
    "record_id": "text",
    "publisher": "text",
    "record_type": "text",
    "title": "text",
    "searchable_text": "text",
    "source_url": "text",
    "canonical_url": "text",
    "collected_at": "timestamp with time zone",
    "source_as_of": "date",
    "collection_method": "text",
    "metadata": "jsonb",
    "supporting_sources": "jsonb",
}


def invalid_constant(value):
    raise ValueError("JSON includes a non-finite number")


def unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("JSON object includes duplicate keys")
    return result


def checked_json(value):
    if isinstance(value, str) and "\x00" in value:
        raise ValueError("JSON includes a NUL character")
    if isinstance(value, dict):
        for key, item in value.items():
            checked_json(key)
            checked_json(item)
    elif isinstance(value, list):
        for item in value:
            checked_json(item)


def text_value(record, key, *, optional=False):
    value = record.get(key)
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{key} must be non-empty text")
    return value


def text_list(record, key, *, optional=False):
    values = record.get(key)
    if optional and values is None:
        return []
    if not isinstance(values, list) or any(
        not isinstance(value, str) or not value.strip() or "\x00" in value for value in values
    ):
        raise ValueError(f"{key} must be a list of non-empty strings")
    return values


def source_url(record, key):
    value = text_value(record, key)
    parts = urlsplit(value)
    hostname = parts.hostname or ""
    if (
        parts.scheme != "https" or not hostname or hostname.endswith(".invalid")
        or parts.username or parts.password
    ):
        raise ValueError(f"{key} must be a real HTTPS URL without credentials")
    return value


def source_metadata(record):
    if not isinstance(record, dict) or record.get("mode") != "live":
        raise ValueError("only real source records are accepted")
    collected = datetime.fromisoformat(text_value(record, "collected_at"))
    if collected.utcoffset() is None:
        raise ValueError("collected_at must include a time zone")
    date_value = text_value(record, "source_as_of", optional=True)
    if date_value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
        raise ValueError("source_as_of must be an ISO calendar date")
    as_of = date.fromisoformat(date_value) if date_value else None
    if record.get("status") and as_of is None:
        raise ValueError("a status claim requires source_as_of")
    return {
        "publisher": text_value(record, "publisher"),
        "source_url": source_url(record, "source_url"),
        "canonical_url": source_url(record, "canonical_url"),
        "collected_at": collected,
        "source_as_of": as_of,
        "collection_method": text_value(record, "collection_method"),
    }


def normalize_record(record):
    source = source_metadata(record)
    record_type = text_value(record, "record_type")
    record_id = text_value(record, "record_id")
    title = text_value(record, "title")
    qualifiers = list(text_list(record, "aliases", optional=True))
    if record_type == "project" and record.get("source") == "DarGlobal":
        if not re.fullmatch(r"project:[a-z0-9][a-z0-9-]*", record_id):
            raise ValueError("invalid project record_id")
        qualifiers.extend([
            text_value(record, "location", optional=True),
            text_value(record, "property_type", optional=True),
            text_value(record, "status", optional=True),
            text_value(record, "development_value", optional=True),
            *text_list(record, "details", optional=True),
            *text_list(record, "amenities", optional=True),
        ])
    elif record_type == "buyer_guide" and record.get("source") == "Wasalt":
        if not re.fullmatch(r"guide:[a-z0-9][a-z0-9-]*", record_id):
            raise ValueError("invalid guide record_id")
        points = text_list(record, "points")
        if not points:
            raise ValueError("a guide section requires at least one point")
        qualifiers.extend([text_value(record, "section"), *points])
    else:
        raise ValueError("expected a DarGlobal project or a Wasalt buyer-guide section")
    if source["source_as_of"]:
        qualifiers.append(source["source_as_of"].isoformat())
    supporting = record.get("supporting_sources", [])
    if not isinstance(supporting, list):
        raise ValueError("supporting_sources must be a list")
    for item in supporting:
        source_metadata(item)
        text_value(item, "title")
    return {
        "record_id": record_id,
        "record_type": record_type,
        "title": title,
        "searchable_text": "\n".join(dict.fromkeys(filter(None, [title, *qualifiers]))),
        **source,
        "metadata": {key: value for key, value in record.items() if key not in COLUMNS},
        "supporting_sources": supporting,
    }


def load_snapshot(path):
    rows = {}
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            source = json.loads(line, parse_constant=invalid_constant, object_pairs_hook=unique_object)
            checked_json(source)
            json.dumps(source, ensure_ascii=False, allow_nan=False).encode("utf-8")
            row = normalize_record(source)
            key = row["record_id"]
            if key in rows:
                raise ValueError("duplicate record_id")
        except (TypeError, ValueError, RecursionError) as error:
            # Do not include source text or parser exception values in logs.
            message = "Invalid JSON record" if isinstance(error, json.JSONDecodeError) else "Schema validation failed"
            raise ValueError(f"{message} on snapshot line {number}.") from None
        rows[key] = row
    if not rows:
        raise ValueError("The snapshot is empty.")
    return rows
