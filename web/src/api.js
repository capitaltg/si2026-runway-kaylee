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
