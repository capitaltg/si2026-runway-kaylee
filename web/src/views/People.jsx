import React, { useEffect, useMemo, useState } from "react";
import {
  getPeople,
  getPeopleUtilization,
  addPerson,
  savePersonQuals,
  mergePerson,
  deletePerson,
} from "../api.js";
import { panelStyle, shortDate } from "../format.js";

// People directory (#69) — the app's first genuinely global view.
//
// Two rules shape this screen:
//
//   1. Nothing here may imply an upload is required. The directory is already
//      populated from timesheets, and an un-annotated person is a normal,
//      supported state — so the header counts people, never missing files, and
//      "unknown" is styled as neutral rather than as a warning.
//   2. Utilisation is loaded on demand. It costs a burn pass per contract, and
//      the directory's job is credentials, not hours.
//
// No compensation anywhere: Runway visualises money, it does not manage payroll.

const grotesk = "'Space Grotesk',sans-serif";
const mono = "'IBM Plex Mono',monospace";

// The fields the directory stores, and how each is described to the person typing
// it.
//
// Three of the four are drawn from closed vocabularies (#98), because #66 compares
// them to a labor category's floor and cannot do that across two spellings. The
// options themselves are NOT listed here — they arrive with the directory payload
// (`qual_vocab`) so this screen and the check read one ladder, not two that drifted.
//
// Field of study is the exception and stays open text: "Computer Science" is not
// more or less than "Mechanical Engineering", so there is nothing to compare and
// nothing to close. Splitting it off the level is what keeps "BS Computer Science"
// sayable without making it the thing being checked.
//
// Years is a number for the same comparability reason — `12`, `12 yrs`, `~12` and
// `12+` were all reachable before. The argument behind the number was never the
// number's job: it goes in the source note, which is the whole point of an
// assertion carrying its provenance.
const QUAL_FIELDS = [
  {
    key: "education",
    label: "Education level",
    type: "select",
    vocab: "education",
    unsetLabel: "Not recorded",
  },
  {
    key: "education_field",
    label: "Field of study",
    type: "text",
    placeholder: "Computer Science",
    optionalNote: true,
    note: "Context, not a credential — nothing compares one field of study to another. Recorded for the person reading the record.",
  },
  {
    key: "years_experience",
    label: "Years of experience",
    type: "number",
    placeholder: "12",
    note: "Recorded as an assertion with a source, not a fact. “12 · per proposal resume, 2026-03” is defensible in an audit; a bare 12 is a number someone will dispute.",
  },
  {
    key: "clearance",
    label: "Clearance",
    type: "select",
    vocab: "clearance",
    unsetLabel: "Not recorded",
    // "None" is one of the options and means they hold no clearance. That is a
    // different fact from not having recorded one, and the ladder has to keep them
    // apart or the check reports an unasked question as a failure.
    note: "“None” means they hold no clearance — which is not the same as leaving this unrecorded.",
  },
];

const STATUS = {
  complete: { label: "Recorded", color: "var(--good)", bg: "var(--goodBg)" },
  partial: { label: "Partial", color: "var(--accent)", bg: "var(--panel2)" },
  // Deliberately not a warning colour. Unknown is the day-one state for everybody
  // and painting 114 people amber would read as 114 problems.
  unknown: { label: "Not recorded", color: "var(--faint)", bg: "var(--panel2)" },
};

const label = {
  fontSize: 10.5,
  letterSpacing: ".1em",
  textTransform: "uppercase",
  fontWeight: 700,
  color: "var(--faint)",
};

const input = {
  width: "100%",
  boxSizing: "border-box",
  padding: "8px 10px",
  borderRadius: 9,
  border: "1px solid var(--border)",
  background: "var(--inputBg)",
  color: "var(--text)",
  fontSize: 13,
  fontFamily: "inherit",
};

const button = (primary) => ({
  padding: "8px 14px",
  borderRadius: 9,
  border: primary ? "none" : "1px solid var(--border)",
  background: primary ? "var(--accent)" : "var(--panel)",
  color: primary ? "#fff" : "var(--dim)",
  fontSize: 12.5,
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
});

const chip = {
  fontSize: 11,
  color: "var(--dim)",
  border: "1px solid var(--border)",
  borderRadius: 7,
  padding: "2px 8px",
  whiteSpace: "nowrap",
};

function Pill({ status }) {
  const s = STATUS[status] || STATUS.unknown;
  return (
    <span
      style={{
        fontSize: 10.5,
        fontWeight: 700,
        padding: "2px 9px",
        borderRadius: 20,
        color: s.color,
        background: s.bg,
        whiteSpace: "nowrap",
      }}
    >
      {s.label}
    </span>
  );
}

export default function People({ onOpenContract }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [util, setUtil] = useState(null);
  const [utilBusy, setUtilBusy] = useState(false);
  const [addOpen, setAddOpen] = useState(false);

  // Runway has no auth, so there is nobody to attribute a qual to automatically.
  // Rather than invent an identity or drop provenance, ask once and remember it —
  // "typed by Kaylee, 2026-08-05" is the point of the field.
  const [author, setAuthor] = useState(
    () => localStorage.getItem("runway.author") || "",
  );

  const load = () =>
    getPeople()
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError(e.message));

  useEffect(() => {
    load();
  }, []);

  const utilById = useMemo(() => {
    const map = {};
    (util?.people || []).forEach((p) => (map[p.employee_id] = p));
    return map;
  }, [util]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return data?.people || [];
    return (data?.people || []).filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        p.employee_id.toLowerCase().includes(q) ||
        p.lcats.some((l) => l.toLowerCase().includes(q)) ||
        p.contracts.some((c) => (c.contract || "").toLowerCase().includes(q)),
    );
  }, [data, query]);

  const selected = (data?.people || []).find((p) => p.employee_id === selectedId);

  function loadUtilization() {
    setUtilBusy(true);
    getPeopleUtilization()
      .then(setUtil)
      .catch((e) => setError(e.message))
      .finally(() => setUtilBusy(false));
  }

  function rememberAuthor(v) {
    setAuthor(v);
    localStorage.setItem("runway.author", v);
  }

  if (error && !data) {
    return <div style={{ padding: 40, color: "var(--bad)" }}>Couldn't load people: {error}</div>;
  }
  if (!data) {
    return <div style={{ padding: 40, color: "var(--dim)" }}>Loading people…</div>;
  }

  const cov = data.coverage;
  const recorded = cov.complete + cov.partial;

  return (
    <div style={{ padding: "26px 26px 60px", maxWidth: 1400 }}>
      <div style={{ marginBottom: 18 }}>
        <h2 style={{ margin: 0, fontFamily: grotesk, fontSize: 22, fontWeight: 600, color: "var(--text)" }}>
          People
        </h2>
        {/* Counts people, not missing files. The directory is the deliverable; the
            quals are an optional annotation on top of it. */}
        <div style={{ fontSize: 13.5, color: "var(--dim)", marginTop: 5 }}>
          {data.count} {data.count === 1 ? "person" : "people"}, built from your synced
          timesheets. Qualifications are optional — add them one person at a time,
          whenever you need them.
        </div>
      </div>

      {/* coverage strip — three states, so "checked and clear" stays distinguishable
          from "not checked" once #66 renders a compliance verdict off it. */}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 16 }}>
        {[
          ["In the directory", data.count, "var(--text)"],
          ["Qualifications recorded", recorded, "var(--good)"],
          ["Not recorded yet", cov.unknown, "var(--faint)"],
        ].map(([t, n, color]) => (
          <div key={t} style={{ ...panelStyle, padding: "12px 16px", flex: "1 1 180px" }}>
            <div style={label}>{t}</div>
            <div style={{ fontFamily: grotesk, fontWeight: 700, fontSize: 24, color, marginTop: 4 }}>
              {n}
            </div>
          </div>
        ))}
      </div>

      {data.unidentified.rows > 0 && (
        <div
          style={{
            ...panelStyle,
            padding: "11px 15px",
            marginBottom: 14,
            borderColor: "var(--warn)",
            fontSize: 12.5,
            color: "var(--dim)",
          }}
        >
          <strong style={{ color: "var(--warn)" }}>Data quality:</strong>{" "}
          {data.unidentified.rows} timesheet{" "}
          {data.unidentified.rows === 1 ? "row" : "rows"} across{" "}
          {data.unidentified.contracts}{" "}
          {data.unidentified.contracts === 1 ? "contract" : "contracts"} carry no
          employee ID, so they aren't attributed to anyone here. They're excluded
          rather than merged together — collapsing them would invent a person.
        </div>
      )}

      {(data.merge_suggestions || []).length > 0 && (
        <div style={{ ...panelStyle, padding: "13px 16px", marginBottom: 14, borderColor: "var(--accent)" }}>
          <div style={{ fontSize: 12.5, color: "var(--dim)", marginBottom: 9 }}>
            Someone you added by hand looks like a person your timesheets now carry.
            Matched on name only, so this is a suggestion — confirm it yourself.
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            {data.merge_suggestions.map((s) => (
              <div key={s.from} style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <span style={{ fontSize: 13, color: "var(--text)" }}>
                  <strong>{s.name}</strong>{" "}
                  <span style={{ fontFamily: mono, fontSize: 11.5, color: "var(--faint)" }}>
                    {s.from} → {s.into}
                  </span>
                </span>
                <button
                  style={button(true)}
                  onClick={() =>
                    mergePerson(s.from, s.into)
                      .then(() => {
                        if (selectedId === s.from) setSelectedId(s.into);
                        return load();
                      })
                      .catch((e) => setError(e.message))
                  }
                >
                  Merge into {s.into}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* toolbar */}
      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search name, ID, labor category or contract…"
          style={{ ...input, width: 320 }}
        />
        <button style={button(false)} onClick={() => setAddOpen((v) => !v)}>
          {addOpen ? "Cancel" : "+ Add a person"}
        </button>
        {/* On demand by design: this is a burn pass per contract, and the People
            view should not pay for it just to list names. */}
        <button style={button(false)} onClick={loadUtilization} disabled={utilBusy}>
          {utilBusy ? "Working…" : util ? "Refresh utilisation" : "Load utilisation"}
        </button>
        {error && <span style={{ fontSize: 12, color: "var(--bad)" }}>{error}</span>}
      </div>

      {addOpen && <AddPerson onDone={() => { setAddOpen(false); load(); }} onError={setError} />}

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        {/* directory table */}
        <div style={{ ...panelStyle, padding: 0, flex: "1 1 560px", minWidth: 0, overflow: "hidden" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "var(--panel2)" }}>
                {["Name", "Employee ID", "Contracts", "Labor categories", util ? "Hrs/wk" : null, "Quals"]
                  .filter(Boolean)
                  .map((h) => (
                    <th
                      key={h}
                      style={{
                        ...label,
                        textAlign: "left",
                        padding: "10px 13px",
                        borderBottom: "1px solid var(--border)",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {h}
                    </th>
                  ))}
              </tr>
            </thead>
            <tbody>
              {shown.map((p) => {
                const u = utilById[p.employee_id];
                const active = p.employee_id === selectedId;
                return (
                  <tr
                    key={p.employee_id}
                    onClick={() => setSelectedId(active ? null : p.employee_id)}
                    style={{
                      cursor: "pointer",
                      background: active ? "var(--panel2)" : "transparent",
                      borderBottom: "1px solid var(--border)",
                    }}
                  >
                    <td style={{ padding: "9px 13px", color: "var(--text)", fontWeight: 600 }}>
                      {p.name}
                      {p.origin === "manual" && (
                        <span style={{ ...chip, marginLeft: 7, color: "var(--accent)" }}>Added manually</span>
                      )}
                    </td>
                    <td style={{ padding: "9px 13px", fontFamily: mono, fontSize: 11.5, color: "var(--faint)" }}>
                      {p.employee_id}
                      {p.id_provisional && (
                        <span title="Runway-minted placeholder — not a payroll ID" style={{ marginLeft: 5 }}>
                          *
                        </span>
                      )}
                    </td>
                    <td style={{ padding: "9px 13px", color: "var(--dim)" }}>
                      {p.contract_count || <span style={{ color: "var(--faint)" }}>—</span>}
                    </td>
                    <td style={{ padding: "9px 13px", color: "var(--dim)" }}>
                      {p.lcats.length ? p.lcats.join(" · ") : <span style={{ color: "var(--faint)" }}>—</span>}
                    </td>
                    {util && (
                      <td
                        style={{
                          padding: "9px 13px",
                          fontFamily: mono,
                          fontWeight: 700,
                          fontSize: 12.5,
                          color: !u
                            ? "var(--faint)"
                            : u.utilization > 1
                              ? "var(--warn)"
                              : "var(--dim)",
                        }}
                      >
                        {u ? `${u.total_hours}` : "—"}
                      </td>
                    )}
                    <td style={{ padding: "9px 13px" }}>
                      <Pill status={p.quals_status} />
                    </td>
                  </tr>
                );
              })}
              {!shown.length && (
                <tr>
                  <td colSpan={6} style={{ padding: 24, textAlign: "center", color: "var(--faint)", fontSize: 13 }}>
                    Nobody matches “{query}”.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* detail panel */}
        <div style={{ flex: "0 0 380px", position: "sticky", top: 16 }}>
          {selected ? (
            <PersonPanel
              key={selected.employee_id}
              person={selected}
              vocab={data.qual_vocab || {}}
              util={utilById[selected.employee_id]}
              author={author}
              setAuthor={rememberAuthor}
              onOpenContract={onOpenContract}
              onSaved={load}
              onError={setError}
            />
          ) : (
            <div style={{ ...panelStyle, color: "var(--dim)", fontSize: 13, lineHeight: 1.55 }}>
              <div style={{ ...label, marginBottom: 7 }}>The directory</div>
              Everyone who has ever charged time is already here — identity and
              charging history come from your timesheets, so there was nothing to set
              up.
              <div style={{ marginTop: 10 }}>
                Select a person to record their qualifications. Someone added by
                hand — a planned hire, a candidate — is a pickable name and never
                appears on a contract's allocation matrix until a timesheet says so.
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function AddPerson({ onDone, onError }) {
  const [name, setName] = useState("");
  const [eid, setEid] = useState("");
  const [busy, setBusy] = useState(false);

  function submit() {
    if (!name.trim()) return;
    setBusy(true);
    addPerson(name.trim(), eid.trim())
      .then(onDone)
      .catch((e) => onError(e.message))
      .finally(() => setBusy(false));
  }

  return (
    <div style={{ ...panelStyle, marginBottom: 14, display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
      <div style={{ flex: "1 1 200px" }}>
        <div style={{ ...label, marginBottom: 5 }}>Name</div>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Planned hire" style={input} />
      </div>
      <div style={{ flex: "1 1 200px" }}>
        <div style={{ ...label, marginBottom: 5 }}>Employee ID (optional)</div>
        <input value={eid} onChange={(e) => setEid(e.target.value)} placeholder="EMP-4471" style={input} />
      </div>
      <button style={button(true)} onClick={submit} disabled={busy || !name.trim()}>
        {busy ? "Adding…" : "Add"}
      </button>
      {/* The reason to want the real id, stated where the decision is made. */}
      <div style={{ flex: "1 1 100%", fontSize: 11.5, color: "var(--faint)", lineHeight: 1.5 }}>
        If you know their real payroll ID, enter it — they'll link up to their own
        timesheets automatically the first time a feed carries them, instead of
        becoming a second profile. Left blank, Runway assigns a placeholder ID and
        offers to merge it later.
      </div>
    </div>
  );
}

function PersonPanel({ person, vocab, util, author, setAuthor, onOpenContract, onSaved, onError }) {
  // Draft state seeded from what's stored. Only edited fields are sent, so saving a
  // clearance can't disturb a years assertion somebody sourced separately.
  const [draft, setDraft] = useState(() => {
    const d = {};
    QUAL_FIELDS.forEach(({ key }) => {
      d[key] = {
        value: person.quals[key]?.value || "",
        source_note: person.quals[key]?.source_note || "",
      };
    });
    return d;
  });
  const [busy, setBusy] = useState(false);

  const dirty = QUAL_FIELDS.some(
    ({ key }) =>
      draft[key].value !== (person.quals[key]?.value || "") ||
      draft[key].source_note !== (person.quals[key]?.source_note || ""),
  );

  function set(key, patch) {
    setDraft((d) => ({ ...d, [key]: { ...d[key], ...patch } }));
  }

  function save() {
    setBusy(true);
    const quals = {};
    QUAL_FIELDS.forEach(({ key }) => {
      const before = person.quals[key];
      if (
        draft[key].value !== (before?.value || "") ||
        draft[key].source_note !== (before?.source_note || "")
      ) {
        quals[key] = draft[key];
      }
    });
    savePersonQuals(person.employee_id, quals, author)
      .then(onSaved)
      .catch((e) => onError(e.message))
      .finally(() => setBusy(false));
  }

  return (
    <div style={{ ...panelStyle, display: "flex", flexDirection: "column", gap: 14 }}>
      <div>
        <div style={{ fontFamily: grotesk, fontSize: 17, fontWeight: 600, color: "var(--text)" }}>
          {person.name}
        </div>
        <div style={{ fontFamily: mono, fontSize: 11.5, color: "var(--faint)", marginTop: 3 }}>
          {person.employee_id}
          {person.id_provisional && " · placeholder ID"}
          {person.origin === "manual" && " · added manually"}
        </div>
      </div>

      {/* charging facts — derived, never editable here. The directory has no
          authority over who charges what. */}
      <div>
        <div style={{ ...label, marginBottom: 6 }}>Charging (from timesheets)</div>
        {person.contracts.length ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            {person.contracts.map((c) => (
              <div key={c.contract_id} style={{ fontSize: 12.5, color: "var(--dim)", lineHeight: 1.45 }}>
                <span
                  onClick={() => onOpenContract && onOpenContract(c.contract_id)}
                  style={{ color: "var(--accent)", fontWeight: 600, cursor: onOpenContract ? "pointer" : "default" }}
                >
                  {c.contract}
                </span>
                <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 3 }}>
                  {c.lcats.map((l) => (
                    <span key={l} style={chip}>{l}</span>
                  ))}
                  {c.clins.map((cl) => (
                    <span key={cl} style={{ ...chip, fontFamily: mono }}>CLIN {cl}</span>
                  ))}
                </div>
                <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 3 }}>
                  {shortDate(c.first_week)} → {shortDate(c.last_week)}
                </div>
              </div>
            ))}
            {util && (
              <div style={{ fontSize: 12, color: "var(--dim)", marginTop: 2 }}>
                {util.total_hours} hrs/wk across {util.assignments.length}{" "}
                {util.assignments.length === 1 ? "contract" : "contracts"}
              </div>
            )}
          </div>
        ) : (
          <div style={{ fontSize: 12.5, color: "var(--faint)", lineHeight: 1.5 }}>
            No charges yet. They're a pickable candidate and won't appear on any
            contract's allocation matrix until a timesheet says they do.
          </div>
        )}
      </div>

      <div style={{ height: 1, background: "var(--border)" }} />

      {/* quals — the only authored part of a person's record */}
      <div>
        <div style={{ ...label, marginBottom: 3 }}>Qualifications</div>
        <div style={{ fontSize: 11.5, color: "var(--faint)", marginBottom: 10, lineHeight: 1.5 }}>
          All optional. Leave a field blank to keep it unrecorded.
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 13 }}>
          {QUAL_FIELDS.map((f) => {
            const stored = person.quals[f.key];
            const options = f.vocab ? vocab[f.vocab] || [] : null;
            // A value typed in before the vocabularies existed. Shown as-is rather
            // than guessed at or silently blanked — it is somebody's assertion, and
            // rewriting it would be inventing a fact. It stays saveable unchanged;
            // only a *new* value has to come off the ladder.
            const legacy =
              options && draft[f.key].value && !options.includes(draft[f.key].value)
                ? draft[f.key].value
                : null;
            return (
              <div key={f.key}>
                <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", marginBottom: 4 }}>
                  {f.label}
                  {f.optionalNote && (
                    <span style={{ fontWeight: 400, color: "var(--faint)" }}> · optional</span>
                  )}
                </div>
                {options ? (
                  <select
                    value={draft[f.key].value}
                    onChange={(e) => set(f.key, { value: e.target.value })}
                    style={input}
                  >
                    {/* Unset is not a value. Picking it back clears the field to
                        unknown, which has to stay reachable or "optional" isn't
                        true — and for clearance it is emphatically not "None". */}
                    <option value="">{f.unsetLabel}</option>
                    {options.map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                    {legacy && <option value={legacy}>{legacy} (unrecognised)</option>}
                  </select>
                ) : (
                  <input
                    type={f.type === "number" ? "number" : "text"}
                    min={f.type === "number" ? 0 : undefined}
                    max={f.type === "number" ? 70 : undefined}
                    step={f.type === "number" ? 1 : undefined}
                    value={draft[f.key].value}
                    onChange={(e) => set(f.key, { value: e.target.value })}
                    placeholder={f.placeholder}
                    style={input}
                  />
                )}
                {legacy && (
                  <div style={{ fontSize: 11, color: "var(--warn)", marginTop: 4, lineHeight: 1.45 }}>
                    Recorded before this became a set list. Pick the matching value
                    so the qualification check can read it.
                  </div>
                )}
                <input
                  value={draft[f.key].source_note}
                  onChange={(e) => set(f.key, { source_note: e.target.value })}
                  placeholder="Source — e.g. per proposal resume, 2026-03"
                  style={{ ...input, marginTop: 5, fontSize: 12 }}
                />
                {f.note && (
                  <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 4, lineHeight: 1.45 }}>
                    {f.note}
                  </div>
                )}
                {/* Provenance: the evidence trail is the deliverable, not a boolean.
                    DCAA asks you to demonstrate the person meets the category you
                    billed, which a bare value cannot do. */}
                {stored && (
                  <div style={{ fontSize: 10.5, color: "var(--faint)", marginTop: 4, fontStyle: "italic" }}>
                    {stored.authored_by ? `typed by ${stored.authored_by}` : "typed"} ·{" "}
                    {shortDate(stored.authored_at)}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div>
        <div style={{ ...label, marginBottom: 5 }}>Recorded by</div>
        <input
          value={author}
          onChange={(e) => setAuthor(e.target.value)}
          placeholder="Your name"
          style={input}
        />
      </div>

      <div style={{ display: "flex", gap: 9, alignItems: "center" }}>
        <button style={button(true)} onClick={save} disabled={busy || !dirty}>
          {busy ? "Saving…" : "Save qualifications"}
        </button>
        {/* Only ever offered for someone the feed doesn't carry. A person with hours
            belongs to the timesheet feed, not to this screen. */}
        {person.origin === "manual" && (
          <button
            style={{ ...button(false), color: "var(--bad)" }}
            onClick={() =>
              deletePerson(person.employee_id)
                .then(onSaved)
                .catch((e) => onError(e.message))
            }
          >
            Remove
          </button>
        )}
      </div>
    </div>
  );
}
