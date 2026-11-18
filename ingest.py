"""Build property snapshots from fixtures or browser-saved HTML."""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "ingestion_fixtures"
FIXTURE_OUTPUT = ROOT / "snapshots" / "property_pilot.jsonl"
MAX_PAGES = 5


def text_at(page, selector):
    node = page.select_one(selector)
    return (node.get_text(" ", strip=True) or None) if node else None


def optional_text(value):
    return (value.strip() or None) if isinstance(value, str) else None


def number(value, numeric_type):
    if not isinstance(value, (int, float, str)) or isinstance(value, bool):
        return None
    try:
        parsed = numeric_type(str(value).replace(",", ""))
    except ValueError:
        return None
    return parsed if 0 <= parsed < float("inf") else None


def labeled_value(page, *labels):
    labels = {label.casefold() for label in labels}
    for node in page.select("dt, th, label, strong, span, p, li"):
        text = node.get_text(" ", strip=True)
        label, separator, value = text.partition(":")
        if separator and label.strip().casefold() in labels:
            return value.strip() or None
        if text.casefold() not in labels:
            continue
        sibling = node.find_next_sibling()
        if sibling is not None:
            value = sibling.get_text(" ", strip=True)
            if value and len(value) <= 100 and value.rstrip(":").casefold() not in labels:
                return value
    return None


def heading_location(page):
    heading = page.select_one("h1")
    if heading is None:
        return None
    for sibling in list(heading.next_siblings)[:6]:
        if not hasattr(sibling, "get_text"):
            continue
        label = sibling.get_text(" ", strip=True)
        if label.casefold() in {"oman", "spain"}:
            return label
        if re.fullmatch(r"[^|,.:]{2,40}\s*\|\s*(?:Oman|Spain)", label, re.IGNORECASE):
            return ", ".join(part.strip() for part in label.split("|"))
    return None


def objects(value):
    if isinstance(value, dict):
        yield value
        for key in ("@graph", "mainEntity", "itemOffered", "item"):
            yield from objects(value.get(key))
    elif isinstance(value, list):
        for item in value:
            yield from objects(item)


def structured_objects(page):
    for script in page.select('script[type="application/ld+json"]'):
        try:
            yield from objects(json.loads(script.get_text()))
        except ValueError:
            continue


def object_or_empty(value):
    return value if isinstance(value, dict) else {}


class PropertyAdapter:
    def page(self, filename):
        markup = (FIXTURES / self.source.lower() / filename).read_text(encoding="utf-8")
        return BeautifulSoup(markup, "html.parser")

    def collect(self, limit, collected_at):
        discovery = self.page("discovery.html")
        detail_urls = {}
        for link in discovery.select("main a[href]"):
            parts = urlsplit(urljoin(self.fixture_origin, link["href"]))
            if parts.netloc != urlsplit(self.fixture_origin).netloc or parts.scheme != "https":
                continue
            path = Path(parts.path.strip("/"))
            if path.parent.as_posix() != self.detail_section or not path.name.isascii():
                continue
            if not path.name.replace("-", "").isalnum():
                continue
            detail_urls.setdefault(path.name, urljoin(self.fixture_origin, parts.path))
            if len(detail_urls) == limit:
                break

        if not detail_urls:
            raise ValueError(f"{self.source}: no detail links found")
        for slug, url in detail_urls.items():
            detail = self.page(f"{slug}.html")
            canonical = detail.select_one('link[rel="canonical"][href]')
            if canonical is None or canonical["href"] != url:
                raise ValueError(f"{self.source}: missing or unexpected fixture canonical URL")
            yield {
                "source": self.source,
                "record_type": self.record_type,
                "mode": "synthetic_fixture",
                "canonical_url": url,
                "collected_at": collected_at,
                **self.extract(detail),
            }


class DarGlobalAdapter(PropertyAdapter):
    source = "DarGlobal"
    record_type = "project"
    fixture_origin = "https://darglobal.example.invalid"
    detail_section = "projects"
    host = "darglobal.co.uk"

    def extract(self, detail):
        amenities = [
            label for item in detail.select("[data-amenities] li")
            if (label := item.get_text(" ", strip=True))
        ]
        return {
            "title": text_at(detail, "main h1"),
            "location": text_at(detail, "[data-location]"),
            "status": text_at(detail, "[data-status]"),
            "amenities": list(dict.fromkeys(amenities)) or None,
        }

    def extract_saved(self, detail, url):
        fields = self.extract(detail)
        title = fields["title"] or text_at(detail, "h1")
        if title:
            title = re.sub(r"^welcome to\s+", "", title, flags=re.IGNORECASE)
        fields["title"] = title
        fields["location"] = fields["location"] or labeled_value(detail, "location") or heading_location(detail)
        fields["status"] = fields["status"] or labeled_value(detail, "status", "project status")
        fields["property_type"] = labeled_value(detail, "property type")
        return fields


class WasaltAdapter(PropertyAdapter):
    source = "Wasalt"
    record_type = "listing"
    fixture_origin = "https://wasalt.example.invalid"
    detail_section = "en/listings"
    host = "wasalt.sa"

    def extract(self, detail):
        return self.listing_fields(self.apartment(detail))

    def apartment(self, detail):
        for entity in structured_objects(detail):
            kinds = entity.get("@type")
            if kinds == "Apartment" or (isinstance(kinds, list) and "Apartment" in kinds):
                return entity
        raise ValueError("Wasalt: no individual Apartment structured data found; save a listing detail page")

    def listing_fields(self, listing, parent_offer=None):
        address = object_or_empty(listing.get("address"))
        offer = object_or_empty(listing.get("offers")) or object_or_empty(parent_offer)
        area = object_or_empty(listing.get("floorSize"))
        location = [optional_text(address.get(key)) for key in ("addressLocality", "addressRegion")]
        return {
            "title": optional_text(listing.get("name")),
            "location": ", ".join(filter(None, location)) or None,
            "price": number(offer.get("price"), float),
            "currency": optional_text(offer.get("priceCurrency")),
            "bedrooms": number(listing.get("numberOfBedrooms"), int),
            "area_sqm": number(area.get("value"), float) if area.get("unitCode") == "MTK" else None,
        }

    def extract_saved(self, detail, url):
        path = urlsplit(url).path
        heading = text_at(detail, "main h1") or text_at(detail, "h1") or ""
        if re.search(r"(?i)\bauctions?\b", f"{path} {heading}"):
            raise ValueError("Wasalt: auction pages are excluded")
        if re.search(r"/apartments-for-sale-in-[^/]+/?$", path, re.IGNORECASE):
            raise ValueError("Wasalt: save individual apartment detail pages instead of the results page")
        try:
            apartment = self.apartment(detail)
        except ValueError:
            property_type = labeled_value(detail, "property type", "type") or ""
            if property_type.casefold() != "apartment":
                raise
            apartment = {}
        parent_offer = next((
            item.get("offers") for item in structured_objects(detail)
            if item.get("@type") == "RealEstateListing" and item.get("mainEntity") == apartment
        ), None)
        fields = self.listing_fields(apartment, parent_offer)
        fields["title"] = fields["title"] or heading or None
        district = labeled_value(detail, "district")
        city = labeled_value(detail, "city")
        fields["location"] = fields["location"] or ", ".join(filter(None, (district, city))) or None
        price_label = labeled_value(detail, "asking price", "price") or ""
        price = re.fullmatch(r"(?:([A-Z]{3})\s*)?([\d,]+(?:\.\d+)?)(?:\s*([A-Z]{3}))?", price_label)
        if price:
            if fields["price"] is None:
                fields["price"] = number(price[2], float)
            fields["currency"] = fields["currency"] or price[1] or price[3]
        if fields["bedrooms"] is None:
            fields["bedrooms"] = number(labeled_value(detail, "bedrooms"), int)
        if fields["area_sqm"] is None:
            area_label = labeled_value(detail, "area", "floor area") or ""
            area = re.fullmatch(r"([\d,]+(?:\.\d+)?)\s*(?:m²|m2|sqm)", area_label, re.IGNORECASE)
            if area:
                fields["area_sqm"] = number(area[1], float)
        return fields


ADAPTERS = (DarGlobalAdapter(), WasaltAdapter())


def public_url(value):
    parts = urlsplit(value)
    if parts.scheme != "https" or parts.username or parts.password or parts.port is not None:
        raise ValueError("use an original HTTPS publisher URL")
    host = (parts.hostname or "").removeprefix("www.")
    adapter = next((item for item in ADAPTERS if item.host == host), None)
    if not adapter or not parts.path.strip("/"):
        raise ValueError("use a DarGlobal or Wasalt detail URL")
    return adapter, parts


def capture_time(value):
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("capture time must include a time zone, such as +04:00 or Z")
    return stamp.astimezone(timezone.utc).isoformat(timespec="seconds")


def saved_record(filename, original_url, captured):
    adapter, original = public_url(original_url)
    recorded_at = capture_time(captured)
    path = Path(filename).resolve()
    if path.is_relative_to(ROOT) and not path.is_relative_to(ROOT / "local_html"):
        raise ValueError("keep raw pages under local_html/ or outside the repository")
    if path.suffix.lower() not in {".html", ".htm"}:
        raise ValueError("provide an .html or .htm file")
    if path.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("saved HTML exceeds 25 MiB")
    page = BeautifulSoup(path.read_bytes(), "html.parser")
    title = (text_at(page, "title") or "").casefold()
    first_heading = (text_at(page, "h1") or "").casefold()
    challenge = page.select_one(
        'iframe[src*="_Incapsula_Resource"], #challenge-form, #cf-challenge-running, '
        'script[src*="/cdn-cgi/challenge-platform/"]'
    )
    challenge_text = r"just a moment|access denied|request blocked|verify (?:that )?you are human|security check|captcha"
    incapsula_shell = (
        not first_heading and page.select_one('script[src*="_Incapsula_Resource"]')
    )
    if challenge or incapsula_shell or re.search(challenge_text, f"{title} {first_heading}"):
        raise ValueError("challenge or access-denied page rejected")
    if "synthetic" in title and "fixture" in title:
        raise ValueError("synthetic fixture cannot be imported as a real record")

    canonical = page.select_one('link[rel="canonical"][href]')
    if canonical:
        canonical_adapter, parts = public_url(urljoin(original_url, canonical["href"]))
        if canonical_adapter is not adapter or parts.path.rstrip("/") != original.path.rstrip("/"):
            raise ValueError("canonical URL does not match the supplied detail page")
    else:
        parts = original
    canonical_url = urlunsplit((parts.scheme, adapter.host, parts.path.rstrip("/"), "", ""))
    fields = adapter.extract_saved(page, canonical_url)
    if not fields["title"] or not any(value for key, value in fields.items() if key != "title"):
        raise ValueError("no verifiable property details found in saved HTML")
    return {
        "source": adapter.source,
        "record_type": adapter.record_type,
        "mode": "live",
        "collection_method": "browser_saved_html",
        "source_url": original_url,
        "canonical_url": canonical_url,
        "collected_at": recorded_at,
        **fields,
    }


def write_snapshot(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        dir=path.parent, mode="w", encoding="utf-8", newline="\n", delete=False
    ) as snapshot:
        temporary = Path(snapshot.name)
        try:
            for record in records:
                snapshot.write(json.dumps(record, ensure_ascii=False) + "\n")
            snapshot.close()
            temporary.replace(path)
        finally:
            snapshot.close()
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Build a local property JSONL snapshot.")
    parser.add_argument("--limit-per-source", type=int, choices=range(1, MAX_PAGES + 1), default=1)
    parser.add_argument("--output", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Request direct online collection")
    mode.add_argument("--html", nargs=3, action="append", metavar=("FILE", "URL", "CAPTURED_AT"),
                      help="Import a browser-saved detail page; repeat for each page")
    args = parser.parse_args()

    if args.live:
        print("Real records: 0 (DarGlobal: 0, Wasalt: 0) | Fixture records: 0", flush=True)
        parser.exit(1, "Live data requested, but no real records were collected. "
                       "Neither source is approved for extraction. No snapshot was written.\n")

    if args.html:
        output = args.output or ROOT / "snapshots" / "property_live.jsonl"
        if output.resolve() == FIXTURE_OUTPUT.resolve() or output.suffix.lower() != ".jsonl":
            parser.error("browser-saved records require a separate .jsonl output")
        records = {}
        rejected = 0
        for filename, url, captured in args.html:
            try:
                record = saved_record(filename, url, captured)
            except (OSError, ValueError) as error:
                rejected += 1
                print(f"Rejected {Path(filename).name}: {error}", file=sys.stderr)
                continue
            records.setdefault(record["canonical_url"], record)
        counts = {adapter.source: 0 for adapter in ADAPTERS}
        for record in records.values():
            counts[record["source"]] += 1
        print(f"Real records: {len(records)} (DarGlobal: {counts['DarGlobal']}, "
              f"Wasalt: {counts['Wasalt']}) | Fixture records: 0 | Rejected inputs: {rejected}", flush=True)
        if not records:
            parser.exit(1, "No real records were imported. No snapshot was written.\n")
        try:
            write_snapshot(output, records.values())
        except OSError as error:
            parser.exit(1, f"Cannot write snapshot: {error}\n")
        print(f"Snapshot: {output}")
        return int(bool(rejected))

    output = args.output or FIXTURE_OUTPUT
    if output.resolve() == ROOT / "snapshots" / "property_live.jsonl" or output.suffix.lower() != ".jsonl":
        parser.error("fixtures require a separate .jsonl output")
    collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        records = [
            record
            for adapter in ADAPTERS
            for record in adapter.collect(args.limit_per_source, collected_at)
        ]
        write_snapshot(output, records)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Ingestion failed: {error}\n")
    print(f"Real records: 0 (DarGlobal: 0, Wasalt: 0) | Fixture records: {len(records)} | Snapshot: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
