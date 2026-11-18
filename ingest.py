"""Build property snapshots from fixtures or browser-saved HTML."""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from answers import (
    DAR_REPORT_CITATION, DAR_REPORT_METHOD, DAR_REPORT_URL, DAR_WEB_METHOD,
    OMRAN_AIDA_METHOD, OMRAN_AIDA_URL, WASALT_GUIDE_METHOD, WASALT_GUIDE_URL,
)
from corpus_snapshot import load_snapshot, normalize_record
from property_search import valid_citation

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

    def collect_omran_aida(self):
        response = httpx.get(OMRAN_AIDA_URL, timeout=12)
        response.raise_for_status()
        page = BeautifulSoup(response.content, "html.parser")
        heading = text_at(page, "h1") or ""
        paragraphs = [node.get_text(" ", strip=True) for node in page.select("p")]
        scope = next((text for text in paragraphs if all(
            token in text.casefold() for token in ("aida", "mixed-use", "yiti", "signed an agreement")
        )), "")
        location = next((text for text in paragraphs if all(
            token in text.casefold() for token in ("yiti", "muscat", "oman")
        )), "")
        if not re.search(r"\baida\b", heading, re.IGNORECASE) or not scope or not location:
            raise ValueError("OMRAN announcement did not match the expected AIDA factual evidence")
        return {
            "source": self.source,
            "publisher": "OMRAN Group",
            "record_type": self.record_type,
            "source_type": "joint_venture_partner_announcement",
            "mode": "live",
            "collection_method": OMRAN_AIDA_METHOD,
            "source_url": OMRAN_AIDA_URL,
            "canonical_url": OMRAN_AIDA_URL,
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "title": "AIDA",
            "location": "Yiti, Muscat, Oman",
            "property_type": "Mixed-use",
            "status": None,
            "amenities": None,
        }

    def collect_issuer_report(self):
        try:
            import pymupdf
        except ImportError:
            raise ValueError("install PyMuPDF to read the issuer report") from None
        response = httpx.get(DAR_REPORT_URL, timeout=20)
        response.raise_for_status()
        if not response.content.startswith(b"%PDF-"):
            raise ValueError("FCA response was not a PDF")
        with pymupdf.open(stream=response.content, filetype="pdf") as report:
            if len(report) < 24:
                raise ValueError("Dar Global report is missing the AIDA page")
            page = report[23].get_text()
        expected = (
            r"(?m)^AIDA$", r"MUSCAT, OMAN", r"Status\s+Under construction",
            r"villas and apartments", r"DAR GLOBAL PLC",
            r"HALF YEAR RESULTS FOR THE SIX MONTHS ENDED 30 JUNE 2026",
        )
        if not all(re.search(pattern, page) for pattern in expected):
            raise ValueError("Dar Global report page 24 did not match the expected factual evidence")
        return {
            "source": self.source,
            "publisher": "Dar Global PLC",
            "record_type": self.record_type,
            "source_type": "issuer_half_year_report",
            "mode": "live",
            "collection_method": DAR_REPORT_METHOD,
            "source_url": DAR_REPORT_URL,
            "canonical_url": DAR_REPORT_CITATION,
            "source_page": 24,
            "source_as_of": "2026-06-30",
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "title": "AIDA",
            "location": "Muscat, Oman",
            "property_type": "Villas and apartments",
            "status": "Under construction",
            "amenities": None,
        }


class WasaltAdapter(PropertyAdapter):
    source = "Wasalt"
    record_type = "listing"
    fixture_origin = "https://wasalt.example.invalid"
    detail_section = "en/listings"
    host = "wasalt.sa"

    def summarize_buyer_guide(self, filename, captured):
        path = Path(filename).resolve()
        if path.is_relative_to(ROOT) and not path.is_relative_to(ROOT / "local_html"):
            raise ValueError("keep browser captures under local_html/ or outside the repository")
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("guide capture exceeds 2 MiB")
        visible = path.read_text(encoding="utf-8-sig")
        expected = (
            "How to Buy a House in Saudi Arabia: A 2026 First-Time Buyer",
            "Confirm title deed", "Building permits", "Completion certificates",
            "Maintenance fees", "Service charges",
        )
        if not all(phrase.casefold() in visible.casefold() for phrase in expected):
            raise ValueError("capture did not match the Wasalt buyer guide and checklist")
        return {
            "source": self.source,
            "publisher": self.source,
            "record_type": "buyer_guide",
            "source_type": "publisher_buyer_guide",
            "mode": "live",
            "collection_method": WASALT_GUIDE_METHOD,
            "source_url": WASALT_GUIDE_URL,
            "canonical_url": WASALT_GUIDE_URL,
            "collected_at": capture_time(captured),
            "title": "Wasalt first-time buyer guide",
            "location": "Saudi Arabia",
            "points": [
                "Check who owns the property by verifying the title deed.",
                "Ask for the building permit and completion certificate.",
                "Budget for recurring maintenance and service costs as well as the asking price.",
            ],
        }

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


def snapshot_key(record):
    if record.get("record_id"):
        return record["record_id"]
    if record.get("collection_method") == DAR_REPORT_METHOD:
        return "project:aida"
    return record["canonical_url"]


def expanded_records(capture_path, omran):
    import pymupdf

    manifest = json.loads((ROOT / "data_sources" / "corpus_manifest.json").read_text(encoding="utf-8"))
    capture_path = capture_path.resolve()
    if capture_path.is_relative_to(ROOT) and not capture_path.is_relative_to(ROOT / "local_html"):
        raise ValueError("keep raw guide captures under local_html/ or outside the repository")
    captures = json.loads(capture_path.read_text(encoding="utf-8-sig"))
    response = httpx.get(DAR_REPORT_URL, timeout=20)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF-"):
        raise ValueError("FCA response did not contain the issuer PDF")
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with pymupdf.open(stream=response.content, filetype="pdf") as report:
        for project in manifest["projects"]:
            page = project["page"]
            content = " ".join(report[page - 1].get_text().casefold().split())
            name = project["title"].replace(",", "").casefold()
            normalized = content.replace(",", "")
            location = project["location"].split(", ")
            evidence = [*project["evidence"], project["status"], location[-1], "dar global plc",
                        "six months ended 30 june 2026"]
            if name not in normalized or not all(item.casefold() in content for item in evidence):
                # Some short labels are a prefix of the annotated issuer title.
                raise ValueError(f"issuer page {page} did not match {project['title']}")
            if not all(part.casefold() in content.replace(" & ", " ") for part in location):
                raise ValueError(f"issuer page {page} did not verify {project['location']}")
            record = {
                "record_id": f"project:{project['id']}", "source": "DarGlobal", "publisher": "Dar Global PLC",
                "record_type": "project", "source_type": "issuer_half_year_report", "mode": "live",
                "collection_method": DAR_REPORT_METHOD, "source_url": DAR_REPORT_URL,
                "canonical_url": f"{DAR_REPORT_CITATION.split('#')[0]}#page={page}", "source_page": page,
                "source_as_of": "2026-06-30", "collected_at": timestamp, "title": project["title"],
                "location": project["location"], "property_type": project["property_type"],
                "status": project["status"], "amenities": None, "aliases": project["aliases"],
            }
            if project["id"] == "aida" and omran:
                record["supporting_sources"] = [omran]
            yield record
    for guide in manifest["guides"]:
        capture = captures.get(guide["key"])
        if not capture or capture["url"] != guide["url"]:
            print(f"Skipped Wasalt guide {guide['key']}: no matching accessible capture", file=sys.stderr)
            continue
        if re.search(r"just a moment|access denied|captcha", capture["heading"], re.IGNORECASE):
            raise ValueError(f"Wasalt guide {guide['key']} capture shows an access challenge")
        lines = [line.strip() for line in capture["text"].splitlines() if line.strip()]
        headings = {heading.strip() for heading in capture["sections"]}
        for section in guide["sections"]:
            heading = section["section"]
            starts = [index for index, line in enumerate(lines) if line == heading]
            if heading not in headings or not starts:
                raise ValueError(f"Wasalt guide {guide['key']} lacks section {heading}")
            evidence_span = " ".join(lines[starts[-1]:starts[-1] + 45]).casefold()
            if not all(term.casefold() in evidence_span for term in section["evidence"]):
                raise ValueError(f"Wasalt guide {guide['key']} section {heading} did not verify its notes")
            yield {
                "record_id": f"guide:{section['id']}", "source": "Wasalt", "publisher": "Wasalt",
                "record_type": "buyer_guide", "source_type": "publisher_guide_section", "mode": "live",
                "collection_method": WASALT_GUIDE_METHOD, "source_url": guide["url"],
                "canonical_url": guide["url"], "collected_at": capture_time(capture["captured"]),
                "title": guide["title"], "section": heading, "points": section["points"],
                "aliases": section["aliases"],
            }


def append_corpus(base, capture_path):
    load_snapshot(base)
    records = [
        json.loads(line) for line in base.read_text(encoding="utf-8-sig").splitlines() if line.strip()
    ]
    captures = json.loads(capture_path.read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "data_sources" / "corpus_expansion.json").read_text(encoding="utf-8"))
    ids = {record["record_id"] for record in records}

    def capture_for(url):
        capture = captures[url]
        if capture["url"] != url or capture["status"] != 200 or re.search(
            r"just a moment|access denied|captcha", capture["title"], re.IGNORECASE
        ):
            raise ValueError(f"unverified source capture for {url}")
        return capture_time(capture["collected_at"]), capture["text"]

    def append(record, evidence, body):
        if not all(term.casefold() in body.casefold() for term in evidence):
            raise ValueError(f"capture did not verify {record['record_id']}")
        normalize_record(record)
        if not valid_citation(record) or record["record_id"] in ids:
            raise ValueError(f"invalid citation or duplicate ID for {record['record_id']}")
        ids.add(record["record_id"])
        records.append(record)

    for project in manifest["projects"]:
        fields = {key: value for key, value in project.items() if key not in {"url", "id", "evidence"}}
        url = project["url"]
        captured, body = capture_for(url)
        append({
            **fields, "record_id": f"project:{project['id']}", "source": "DarGlobal", "publisher": "Dar Global PLC",
            "record_type": "project", "source_type": "issuer_project_webpage", "mode": "live",
            "collection_method": DAR_WEB_METHOD, "source_url": url, "canonical_url": url,
            "location": "Jeddah, Saudi Arabia", "collected_at": captured,
        }, [project["section"], *project["evidence"]], " ".join(body.split()))

    for guide in manifest["guides"]:
        url = guide["url"]
        captured, body = capture_for(url)
        date = datetime.fromisoformat(guide["source_as_of"])
        date_text = f"{date.day} {date:%B} {date.year}"
        if date_text not in " ".join(body.replace("،", " ").split()):
            raise ValueError(f"capture did not verify publication date for {url}")
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        for section in guide["sections"]:
            heading = section["section"]
            starts = [i for i, line in enumerate(lines) if line == heading]
            if not starts:
                raise ValueError(f"capture lacks section {heading}")
            append({
                "record_id": f"guide:{section['id']}", "source": "Wasalt", "publisher": "Wasalt",
                "record_type": "buyer_guide", "source_type": "publisher_guide_section", "mode": "live",
                "collection_method": WASALT_GUIDE_METHOD, "source_url": url, "canonical_url": url,
                "collected_at": captured, "source_as_of": guide["source_as_of"], "title": guide["title"],
                "section": heading, "points": section["points"], "aliases": section["aliases"],
            }, section["evidence"], " ".join(lines[starts[-1]:starts[-1] + 8]))
    return records


def main():
    parser = argparse.ArgumentParser(description="Build a local property JSONL snapshot.")
    parser.add_argument("--limit-per-source", type=int, choices=range(1, MAX_PAGES + 1), default=1)
    parser.add_argument("--output", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Request direct online collection")
    mode.add_argument("--html", nargs=3, action="append", metavar=("FILE", "URL", "CAPTURED_AT"),
                      help="Import a browser-saved detail page; repeat for each page")
    mode.add_argument("--expand-corpus", type=Path, metavar="GUIDE_CAPTURES",
                      help="Build the expanded issuer and guide corpus from local Chrome captures")
    mode.add_argument("--append-corpus", type=Path, metavar="CAPTURES",
                      help="Append the release additions from locally saved, verified browser captures")
    parser.add_argument("--base-snapshot", type=Path, default=ROOT / "deploy_data" / "property_live.jsonl")
    parser.add_argument("--omran-aida", action="store_true", help="Collect AIDA facts from OMRAN's announcement")
    parser.add_argument("--dar-report", action="store_true", help="Collect AIDA facts from Dar Global's filed report")
    parser.add_argument("--wasalt-guide", nargs=2, metavar=("SNAPSHOT", "CAPTURED_AT"),
                        help="Summarize the Wasalt buyer guide from a local Playwright snapshot")
    args = parser.parse_args()

    if args.append_corpus:
        output = args.output or ROOT / "deploy_data" / "property_live_v2.jsonl"
        if output.resolve() in {FIXTURE_OUTPUT.resolve(), args.base_snapshot.resolve()} or output.suffix != ".jsonl":
            parser.error("the expanded corpus requires a separate .jsonl output")
        try:
            path = args.append_corpus.resolve()
            if path.is_relative_to(ROOT) and not path.is_relative_to(ROOT / "local_html"):
                raise ValueError("keep publisher captures under local_html/ or outside the repository")
            records = append_corpus(args.base_snapshot, path)
            write_snapshot(output, records)
            print(f"Validated and wrote {len(records)} passages to {output}")
        except (OSError, ValueError, KeyError, TypeError) as error:
            parser.exit(1, f"Could not append corpus: {error}\n")
        return 0

    if args.expand_corpus:
        output = args.output or ROOT / "snapshots" / "property_live.jsonl"
        if output.resolve() == FIXTURE_OUTPUT.resolve() or output.suffix.lower() != ".jsonl":
            parser.error("the real corpus requires a separate .jsonl output")
        try:
            existing = []
            if output.is_file():
                with output.open(encoding="utf-8") as saved:
                    existing = [json.loads(line) for line in saved if line.strip()]
            omran = next((row for row in existing if row.get("collection_method") == OMRAN_AIDA_METHOD), None)
            if omran is None:
                omran = next((
                    source for row in existing for source in row.get("supporting_sources", [])
                    if source.get("collection_method") == OMRAN_AIDA_METHOD
                ), None)
            if omran is None:
                omran = ADAPTERS[0].collect_omran_aida()
            records = list(expanded_records(args.expand_corpus, omran))
            records.extend(row for row in existing if row.get("mode") == "live" and row.get("record_type") == "listing")
            write_snapshot(output, records)
        except (ImportError, OSError, httpx.HTTPError, ValueError, KeyError) as error:
            parser.exit(1, f"Could not build the corpus: {error}\n")
        print(f"Real records: {len(records)} | DarGlobal projects: "
              f"{sum(row['record_type'] == 'project' for row in records)} | Wasalt guide sections: "
              f"{sum(row['record_type'] == 'buyer_guide' for row in records)} | Fixture records: 0")
        return 0

    new_sources = args.dar_report or args.wasalt_guide
    if args.live and (args.omran_aida or new_sources):
        parser.error("use named sources without --live")
    if args.live:
        print("Real records: 0 (DarGlobal: 0, Wasalt: 0) | Fixture records: 0", flush=True)
        parser.exit(1, "Live data requested, but no real records were collected. "
                       "Direct collection from DarGlobal and Wasalt is disabled. "
                       "Use --omran-aida for the partner announcement or --html for approved pages. "
                       "No snapshot was written.\n")

    if args.html or args.omran_aida or new_sources:
        output = args.output or ROOT / "snapshots" / "property_live.jsonl"
        if output.resolve() == FIXTURE_OUTPUT.resolve() or output.suffix.lower() != ".jsonl":
            parser.error("real records require a separate .jsonl output")
        records = {}
        rejected = 0
        named_sources = []
        if args.omran_aida:
            named_sources.append(("OMRAN AIDA announcement", ADAPTERS[0].collect_omran_aida, ()))
        if args.dar_report:
            named_sources.append(("Dar Global report", ADAPTERS[0].collect_issuer_report, ()))
        if args.wasalt_guide:
            named_sources.append(("Wasalt buyer guide", ADAPTERS[1].summarize_buyer_guide, args.wasalt_guide))
        for name, collector, arguments in named_sources:
            try:
                record = collector(*arguments)
            except (OSError, httpx.HTTPError, ValueError) as error:
                rejected += 1
                print(f"Rejected {name}: {error}", file=sys.stderr)
            else:
                records[snapshot_key(record)] = record
        for filename, url, captured in args.html or ():
            try:
                record = saved_record(filename, url, captured)
            except (OSError, ValueError) as error:
                rejected += 1
                print(f"Rejected {Path(filename).name}: {error}", file=sys.stderr)
                continue
            records.setdefault(snapshot_key(record), record)
        if not records:
            print(f"Real records: 0 (DarGlobal: 0, Wasalt: 0) | Fixture records: 0 | Rejected inputs: {rejected}",
                  flush=True)
            parser.exit(1, "No new real records were imported. No snapshot was written.\n")
        if new_sources and output.exists():
            try:
                with output.open(encoding="utf-8") as existing:
                    previous = [json.loads(line) for line in existing if line.strip()]
                if any(record.get("mode") != "live" for record in previous):
                    raise ValueError("existing snapshot contains non-live data")
                merged = {snapshot_key(record): record for record in previous}
                for key, record in records.items():
                    if record["record_type"] == "buyer_guide" and any(
                        item.get("record_id") and item.get("canonical_url") == record["canonical_url"]
                        for item in previous
                    ):
                        continue
                    merged[key] = {**merged.get(key, {}), **record}
                records = merged
            except (OSError, ValueError, KeyError) as error:
                parser.exit(1, f"Cannot preserve existing real records: {error}\n")
        counts = {adapter.source: 0 for adapter in ADAPTERS}
        for record in records.values():
            counts[record["source"]] += 1
        print(f"Real records: {len(records)} (DarGlobal: {counts['DarGlobal']}, "
              f"Wasalt: {counts['Wasalt']}) | Fixture records: 0 | Rejected inputs: {rejected}", flush=True)
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
