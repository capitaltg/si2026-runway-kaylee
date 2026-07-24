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

export async function confirm(extraction) {
  const r = await fetch(`${BASE}/api/contracts/confirm`, {
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

export async function syncTimesheets(contractId, { rows = 300, seed = 42 } = {}) {
  const r = await fetch(
    `${BASE}/api/contracts/${contractId}/timesheets/sync?rows=${rows}&seed=${seed}`,
    { method: "POST" }
  );
  if (!r.ok) throw new Error(`Sync failed (${r.status})`);
  return r.json();
}
