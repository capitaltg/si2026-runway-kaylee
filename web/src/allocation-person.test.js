import { test } from "node:test";
import assert from "node:assert/strict";
import { prefillPerson, rateOptions, validateAddedPerson } from "./allocation-person.js";

test("priced LCAT options carry the loaded rate and qualification floors", () => {
  const [option] = rateOptions({
    rate_lines: [
      {
        lcat: "Senior Engineer",
        rate: 225,
        min_education: "Bachelor's",
        min_experience_yrs: 8,
        clearance: "Secret",
      },
    ],
  });

  assert.deepEqual(option, {
    lcat: "Senior Engineer",
    rate: 225,
    min_education: "Bachelor's",
    min_experience_yrs: 8,
    clearance: "Secret",
  });
});

test("directory selection pre-fills editable identity, LCAT, quals, and utilization", () => {
  const form = prefillPerson(
    {
      employee_id: "E-17",
      name: "Aisha Khan",
      lcats: ["Senior Engineer"],
      quals: {
        education: { value: "Master's" },
        years_experience: { value: "12" },
        clearance: { value: "TS/SCI" },
      },
    },
    { utilization: 0.85 }
  );

  assert.equal(form.name, "Aisha Khan");
  assert.equal(form.employeeId, "E-17");
  assert.equal(form.lcat, "Senior Engineer");
  assert.equal(form.utilization, 0.85);
  assert.deepEqual(form.quals, {
    education: "Master's",
    years_experience: "12",
    clearance: "TS/SCI",
  });
});

test("Other LCAT requires an explicit rate instead of a blended fallback", () => {
  assert.match(
    validateAddedPerson({ name: "Nora Lee", lcatChoice: "other", lcat: "Principal", rate: "" }),
    /rate/i
  );
});

test("a selected rate line becomes the planned person's explicit rate", () => {
  const option = rateOptions({ rate_lines: [{ lcat: "Senior Engineer", rate: 225 }] })[0];
  assert.equal(option.rate, 225);
  assert.equal(
    validateAddedPerson({
      name: "Nora Lee",
      lcatChoice: "Senior Engineer",
      lcat: option.lcat,
      rate: option.rate,
    }),
    null
  );
});
