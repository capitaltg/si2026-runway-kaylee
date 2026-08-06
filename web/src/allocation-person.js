// Pure form helpers for planned allocation people (#65). Keeping this separate
// from the React view makes the rate invariant testable: an added person must carry
// a concrete rate and can never accidentally fall through to a CLIN blended rate.

export function rateOptions(clin) {
  return (clin?.rate_lines || [])
    .filter((line) => (line?.lcat || "").trim() && Number(line.rate) >= 0)
    .map((line) => ({
      lcat: line.lcat.trim(),
      rate: Number(line.rate),
      min_education: line.min_education || null,
      min_experience_yrs: line.min_experience_yrs ?? null,
      clearance: line.clearance || null,
    }));
}

export function prefillPerson(person, utilization) {
  const qual = (field) => person?.quals?.[field]?.value || "";
  return {
    name: person?.name || "",
    employeeId: person?.employee_id || "",
    lcat: person?.lcats?.[0] || "",
    utilization: utilization?.utilization ?? null,
    quals: {
      education: qual("education"),
      years_experience: qual("years_experience"),
      clearance: qual("clearance"),
    },
  };
}

// The source toggle changes which controls are visible, not the planned person.
// Clearing only the search filter means returning to the directory shows every
// candidate while retaining the selected employee in the dropdown.
export function switchPersonSource(form, source) {
  return { ...form, source, search: "" };
}

export function selectDirectoryPersonForm(form, employeeId, prefill) {
  return { ...form, ...prefill, source: "directory", personId: employeeId, search: "" };
}

export function validateAddedPerson(form) {
  if (!(form?.name || "").trim()) return "Enter a name.";
  if (!(form?.lcat || "").trim()) return "Choose an LCAT or enter one under Other.";
  const rate = Number(form?.rate);
  if (!(rate >= 0) || String(form?.rate ?? "").trim() === "")
    return "Enter an explicit hourly rate.";
  return null;
}
