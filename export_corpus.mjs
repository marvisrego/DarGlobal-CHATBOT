import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { homedir, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const bundled = join(homedir(), ".cache", "codex-runtimes", "codex-primary-runtime",
  "dependencies", "node", "node_modules");
const modules = resolve(process.env.CORPUS_ARTIFACT_NODE_MODULES || bundled);
const require = createRequire(join(dirname(modules), "corpus-export-loader.cjs"));
const { FileBlob, SpreadsheetFile, Workbook } = await import(
  pathToFileURL(require.resolve("@oai/artifact-tool", { paths: [modules] })).href
);

const localSnapshot = join(root, "snapshots", "property_live.jsonl");
const snapshot = existsSync(localSnapshot) ? localSnapshot : join(root, "deploy_data", "property_live.jsonl");
const output = join(root, "exports", "property_corpus.xlsx");
const records = (await readFile(snapshot, "utf8")).split(/\r?\n/).filter(Boolean)
  .map((line) => JSON.parse(line)).filter((row) => row.mode === "live");
const sources = new Map();
const usedRecords = new Set();

function sourceId(row) {
  if (!row.publisher || !row.source_url || !row.canonical_url || !row.collected_at || !row.collection_method) {
    throw new Error("Source provenance is incomplete");
  }
  const identity = JSON.stringify([row.publisher, row.canonical_url]);
  const hash = createHash("sha256").update(identity).digest("hex").slice(0, 16);
  const id = `src:${hash}`;
  const metadata = [
    id, row.publisher, row.source_type ?? null, row.source_url, row.canonical_url,
    row.collected_at, row.collection_method, row.source_page ?? null, row.source_as_of ?? null,
  ];
  if (sources.has(id) && JSON.stringify(sources.get(id)) !== JSON.stringify(metadata)) {
    throw new Error(`Conflicting metadata for ${id}`);
  }
  sources.set(id, metadata);
  return id;
}

const projects = [];
const guides = [];
for (const row of records) {
  if (row.record_type !== "project" && row.record_type !== "buyer_guide") continue;
  if (!row.record_id || usedRecords.has(row.record_id)) throw new Error("Missing or duplicate record ID");
  usedRecords.add(row.record_id);
  const primary = sourceId(row);
  if (row.record_type === "project") {
    const supporting = (row.supporting_sources ?? []).map(sourceId);
    if (supporting.length > 1) throw new Error("Project has more than one supporting source");
    projects.push([
      row.record_id, row.title, row.location, row.property_type ?? null, row.status ?? null,
      row.source_as_of ?? null, primary, supporting[0] ?? null,
    ]);
  } else {
    guides.push([row.record_id, row.title, row.section, row.points.join("\n"), primary]);
  }
}
if (!projects.length || !guides.length) throw new Error("Snapshot must contain real projects and guide sections");

const tables = [
  {
    name: "DarGlobal Projects",
    columns: ["Record ID", "Project", "Location", "Property type", "Reported status",
      "Status as of", "Primary source ID", "Supporting source ID"],
    widths: [175, 300, 170, 160, 140, 105, 165, 165],
    rows: projects, height: 34, preview: "A1:H6",
  },
  {
    name: "Wasalt Guide Sections",
    columns: ["Record ID", "Guide", "Article section", "Verified summary", "Source ID"],
    widths: [175, 218, 285, 405, 165],
    rows: guides, height: 50, preview: "A1:E6",
  },
  {
    name: "Sources",
    columns: ["Source ID", "Publisher", "Source type", "Original source URL", "Citation URL",
      "Captured at (UTC)", "Extraction method", "PDF page", "Source as of"],
    widths: [165, 125, 165, 320, 320, 180, 245, 85, 110],
    rows: [...sources.values()].sort((a, b) => a[0].localeCompare(b[0])),
    height: 52, preview: "A1:I6",
  },
];

const workbook = Workbook.create();
for (const { name, columns, widths, rows, height } of tables) {
  const sheet = workbook.worksheets.add(name);
  sheet.getRangeByIndexes(0, 0, rows.length + 1, columns.length).values = [columns, ...rows];
  sheet.getRangeByIndexes(0, 0, 1, columns.length).format = {
    fill: "#283D4B",
    font: { name: "Calibri", size: 11, bold: true, color: "#FFFFFF" },
    verticalAlignment: "center", rowHeightPx: 30,
  };
  sheet.getRangeByIndexes(1, 0, rows.length, columns.length).format = {
    font: { name: "Calibri", size: 11, color: "#17232B" },
    wrapText: true, verticalAlignment: "center", rowHeightPx: height,
  };
  for (const [index, width] of widths.entries()) {
    sheet.getRangeByIndexes(0, index, rows.length + 1, 1).format.columnWidthPx = width;
  }
  for (let index = 1; index <= rows.length; index += 2) {
    sheet.getRangeByIndexes(index, 0, 1, columns.length).format.fill = "#EDF2F5";
  }
  if (name === "Sources") {
    sheet.getRangeByIndexes(1, 5, rows.length, 1).setNumberFormat("yyyy-mm-dd hh:mm:ss");
    sheet.getRangeByIndexes(1, 7, rows.length, 1).format.horizontalAlignment = "center";
  }
  sheet.freezePanes.freezeRows(1);
}

await mkdir(dirname(output), { recursive: true });
await (await SpreadsheetFile.exportXlsx(workbook)).save(output);
const reopened = await SpreadsheetFile.importXlsx(await FileBlob.load(output));
for (const { name, columns, rows } of tables) {
  const values = reopened.worksheets.getItem(name)
    .getRangeByIndexes(0, 0, rows.length + 1, columns.length).values;
  const comparable = (table) => table.map((row, r) => row.map((value, c) => {
    if (name === "Sources" && r > 0 && c === 5) {
      const milliseconds = typeof value === "number"
        ? Math.round((value - 25569) * 86400) * 1000
        : new Date(value).getTime();
      return new Date(Math.round(milliseconds / 1000) * 1000).toISOString();
    }
    return value === "" ? null : value;
  }));
  const expected = comparable([columns, ...rows]);
  const actual = comparable(values);
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    const row = expected.findIndex((item, index) => JSON.stringify(item) !== JSON.stringify(actual[index]));
    throw new Error(`Saved workbook differs on ${name}, row ${row + 1}: ${JSON.stringify(actual[row])}`);
  }
}
if (process.argv.includes("--preview")) {
  const directory = await mkdtemp(join(tmpdir(), "property-corpus-preview-"));
  for (const { name, preview } of tables) {
    const blob = await reopened.render({ sheetName: name, range: preview, scale: 1 });
    await writeFile(join(directory, `${name}.png`), new Uint8Array(await blob.arrayBuffer()));
  }
  console.log(`Workbook previews: ${directory}`);
}
console.log(`Verified ${projects.length} projects, ${guides.length} sections across ${new Set(guides.map((row) => row[4])).size} guides, and ${sources.size} sources.`);
console.log(output);
