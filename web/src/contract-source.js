// What the Flight Deck's "Contract source" panel shows (#30) — derived here rather
// than in the component so the three states that matter (nothing stored, award only,
// award plus rate schedule) are testable without rendering React.
//
// The panel deliberately links to the document instead of reproducing the award. The
// CLIN table and contract summary already print every extracted value; what was
// missing was the ability to check one of them against the page it came from.

const KIND_LABELS = {
  award: "Award document",
  rate_schedule: "Rate schedule",
};

// Newest-first, and only the newest of each kind is offered. A re-uploaded, corrected
// award supersedes the one before it for the purpose of "what are today's numbers
// built on" — the older rows stay in the database (they evidence older figures) but
// putting two awards in a four-line panel would just make the current one ambiguous.
export function sourceDocuments(documents = []) {
  const rows = [...documents].sort((a, b) =>
    String(b.created_at || "").localeCompare(String(a.created_at || "")),
  );
  const newest = (kind) => rows.find((d) => d.kind === kind) || null;
  const award = newest("award");
  const rateSchedule = newest("rate_schedule");
  return {
    award,
    rateSchedule,
    items: [award, rateSchedule].filter(Boolean),
    empty: !award && !rateSchedule,
  };
}

export function kindLabel(kind) {
  return KIND_LABELS[kind] || "Source document";
}

// Size as a person reads it. Bytes are shown for the tiny end because a "0.0 MB"
// document reads as a broken upload when it is really a one-page text file.
export function fileSize(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n <= 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// The first 12 hex characters of the SHA-256 — enough to compare against a checksum
// an auditor ran on their own copy, short enough to sit in a panel. The full digest
// rides on the download response's X-Document-SHA256 header.
export function shortHash(sha256) {
  const s = String(sha256 || "");
  return s ? s.slice(0, 12) : "";
}

// The one line the panel shows when a contract has no stored source. Written to say
// why rather than just what: "none" on a contract ingested last year is expected, and
// a bare "No source document" reads like something went wrong.
export const NO_SOURCE_NOTE =
  "No source document stored — this contract was added before source documents " +
  "were kept, or entered by hand. Re-ingesting its award will attach one.";
