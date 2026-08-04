const BASE = "http://localhost:8001";

export async function ingest(file) {
  const fd = new FormData();
  if (file) fd.append("file", file);
  const r = await fetch(`${BASE}/api/contracts/ingest`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(`Ingest failed (${r.status})`);
  return r.json();
}

export async function getSources() {
  const r = await fetch(`${BASE}/api/sources`);
  if (!r.ok) throw new Error(`Sources failed (${r.status})`);
  return r.json();
}

export async function confirm(extraction, seed) {
  // seed (optional) records the Fixtura batch this award came from, so future
  // timesheet syncs for this contract stay coherent instead of using the default.
  const q = seed != null && seed !== "" ? `?seed=${encodeURIComponent(seed)}` : "";
  const r = await fetch(`${BASE}/api/contracts/confirm${q}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(extraction),
  });
  if (!r.ok) throw new Error(`Confirm failed (${r.status})`);
  return r.json();
}

export async function listContracts() {
  const r = await fetch(`${BASE}/api/contracts`);
  if (!r.ok) throw new Error(`Contracts failed (${r.status})`);
  return r.json();
}

export async function getBurn(contractId) {
  const r = await fetch(`${BASE}/api/contracts/${contractId}/burn`);
  if (!r.ok) throw new Error(`Burn failed (${r.status})`);
  return r.json();
}

export async function getPortfolio() {
  const r = await fetch(`${BASE}/api/portfolio`);
  if (!r.ok) throw new Error(`Portfolio failed (${r.status})`);
  return r.json();
}

export async function listExpenses(contractId, clin) {
  const q = clin ? `?clin=${encodeURIComponent(clin)}` : "";
  const r = await fetch(`${BASE}/api/contracts/${contractId}/expenses${q}`);
  if (!r.ok) throw new Error(`Expenses failed (${r.status})`);
  return r.json();
}

export async function addExpense(contractId, expense) {
  const r = await fetch(`${BASE}/api/contracts/${contractId}/expenses`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(expense),
  });
  if (!r.ok) throw new Error(`Add expense failed (${r.status})`);
  return r.json();
}

export async function deleteExpense(contractId, expenseId) {
  const r = await fetch(
    `${BASE}/api/contracts/${contractId}/expenses/${expenseId}`,
    { method: "DELETE" }
  );
  if (!r.ok) throw new Error(`Delete expense failed (${r.status})`);
  return r.json();
}

export async function getFunding(contractId) {
  const r = await fetch(`${BASE}/api/contracts/${contractId}/funding`);
  if (!r.ok) throw new Error(`Funding failed (${r.status})`);
  return r.json();
}

export async function addMod(contractId, file) {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${BASE}/api/contracts/${contractId}/mods`, {
    method: "POST",
    body: fd,
  });
  if (!r.ok) {
    let detail = `Mod upload failed (${r.status})`;
    try {
      const j = await r.json();
      if (j.detail) detail = j.detail;
    } catch {
      // non-JSON error body; keep the status-code message
    }
    throw new Error(detail);
  }
  return r.json();
}

export async function syncTimesheets(contractId, { rows, seed } = {}) {
  // Only send params the caller actually set, so the backend's demo defaults
  // (row count + seed tuned to burn the bundled contract on plan) govern.
  const qs = new URLSearchParams();
  if (rows != null) qs.set("rows", rows);
  if (seed != null) qs.set("seed", seed);
  const q = qs.toString();
  const r = await fetch(
    `${BASE}/api/contracts/${contractId}/timesheets/sync${q ? `?${q}` : ""}`,
    { method: "POST" }
  );
  if (!r.ok) throw new Error(`Sync failed (${r.status})`);
  return r.json();
}

// Supplemental rate-schedule import (#64). Real awards print the CLIN summary on
// the form face and the fully-burdened rates on a separate continuation sheet, so
// ingesting the face alone leaves every LCAT priced at the blended rate. This
// endpoint existed since the burn engine shipped and nothing in the UI called it —
// which made the resulting flag storm literally unfixable from the app.
export async function importRateSchedule(contractId, file) {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${BASE}/api/contracts/${contractId}/rates`, {
    method: "POST",
    body: fd,
  });
  if (!r.ok) {
    let detail = `Rate import failed (${r.status})`;
    try {
      const j = await r.json();
      if (j.detail) detail = j.detail;
    } catch {
      // non-JSON error body; keep the status-code message
    }
    throw new Error(detail);
  }
  return r.json();
}

// Every rate line in play on a contract, plus its saved LCAT mappings — the target
// list the "map this LCAT" affordance offers.
export async function getLcatRates(contractId) {
  const r = await fetch(`${BASE}/api/contracts/${contractId}/lcat-rates`);
  if (!r.ok) throw new Error(`Rate lines failed (${r.status})`);
  return r.json();
}

// Map a timesheet LCAT onto a rate line the award prices, optionally one on a
// different CLIN. Returns before/after spend + runway per CLIN, because applying a
// mapping re-resolves burn — the caller shows the change rather than just clearing
// a badge.
export async function setLcatAlias(contractId, { source, lcat, clin }) {
  const r = await fetch(`${BASE}/api/contracts/${contractId}/lcat-aliases`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source, lcat, clin }),
  });
  if (!r.ok) throw new Error(`Mapping failed (${r.status})`);
  return r.json();
}

export async function deleteLcatAlias(contractId, source) {
  const r = await fetch(
    `${BASE}/api/contracts/${contractId}/lcat-aliases?source=${encodeURIComponent(source)}`,
    { method: "DELETE" }
  );
  if (!r.ok) throw new Error(`Unmapping failed (${r.status})`);
  return r.json();
}

// Indirect-rate model (#77): the fringe/OH/G&A pools and direct labor rates in
// force, plus the derived buildup. `contractId` null reads/writes the company-wide
// default that every contract inherits per pool. All of it is optional — with none
// of it stored the app runs at Level 1 (billing burn, margin withheld).
export async function getRateModel(contractId) {
  const url = contractId ? `${BASE}/api/contracts/${contractId}/rate-model` : `${BASE}/api/rate-model`;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`Rate model failed (${r.status})`);
  return r.json();
}

export async function saveRateModel(contractId, body) {
  const url = contractId ? `${BASE}/api/contracts/${contractId}/rate-model` : `${BASE}/api/rate-model`;
  const r = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`Saving rates failed (${r.status})`);
  return r.json();
}

// Give a contract a custom nickname (a callsign like "FALCON"), or clear it by
// passing an empty name. The nickname becomes its display name app-wide.
export async function renameContract(contractId, name) {
  const r = await fetch(`${BASE}/api/contracts/${contractId}/name`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!r.ok) throw new Error(`Rename failed (${r.status})`);
  return r.json();
}

// Allocation matrix (#21). The employee × labor-CLIN hrs/wk grid + each CLIN's
// budget/spend/clock, for the what-if simulator (recompute happens client-side).
export async function getAllocation(contractId) {
  const r = await fetch(`${BASE}/api/contracts/${contractId}/allocation`);
  if (!r.ok) throw new Error(`Allocation failed (${r.status})`);
  return r.json();
}

// Portfolio resource conflicts: people booked >100% across contracts.
export async function getAllocationConflicts() {
  const r = await fetch(`${BASE}/api/allocation/conflicts`);
  if (!r.ok) throw new Error(`Conflicts failed (${r.status})`);
  return r.json();
}

// Saved allocation what-if plans for a contract.
export async function listPlans(contractId) {
  const r = await fetch(`${BASE}/api/contracts/${contractId}/plans`);
  if (!r.ok) throw new Error(`Plans failed (${r.status})`);
  return r.json();
}

export async function savePlan(contractId, name, data) {
  const r = await fetch(`${BASE}/api/contracts/${contractId}/plans`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, data }),
  });
  if (!r.ok) throw new Error(`Save plan failed (${r.status})`);
  return r.json();
}

export async function deletePlan(contractId, planId) {
  const r = await fetch(`${BASE}/api/contracts/${contractId}/plans/${planId}`, {
    method: "DELETE",
  });
  if (!r.ok) throw new Error(`Delete plan failed (${r.status})`);
  return r.json();
}

// Ask Runway (#15). Streams a plain-text answer grounded in the burn engine's
// numbers; onChunk fires with each incremental piece so the panel can render the
// answer as it arrives. Returns the full answer once the stream closes.
export async function askRunway({ question, history = [], contractId = null }, onChunk) {
  const r = await fetch(`${BASE}/api/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, history, contract_id: contractId }),
  });
  if (!r.ok || !r.body) throw new Error(`Ask failed (${r.status})`);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let full = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const chunk = decoder.decode(value, { stream: true });
    if (chunk) {
      full += chunk;
      onChunk?.(chunk);
    }
  }
  return full;
}

// Runway Drafts. Streams the narrative PROSE for a generated document (numbers
// are filled client-side); onChunk fires per chunk so the panel can render it
// live. Mirrors askRunway's plain-text stream. Returns the full prose on close.
export async function draftProse({ contractId = null, docType }, onChunk) {
  const r = await fetch(`${BASE}/api/draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ contract_id: contractId, doc_type: docType }),
  });
  if (!r.ok || !r.body) throw new Error(`Draft failed (${r.status})`);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let full = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const chunk = decoder.decode(value, { stream: true });
    if (chunk) {
      full += chunk;
      onChunk?.(chunk);
    }
  }
  return full;
}
