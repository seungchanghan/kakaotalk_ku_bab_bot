import assert from "node:assert/strict";
import test from "node:test";
import { validateFreeOnlyConfig } from "../scripts/predeploy-check.js";


const VALID_CONFIG = {
  workers_dev: true,
  preview_urls: false,
  secrets: { required: ["KAKAO_SKILL_SECRET"] },
  vars: {
    MENU_DATA_URL: "https://tester.github.io/ku-meal/data/menu.json"
  }
};

test("free-only deployment config passes", () => {
  assert.deepEqual(validateFreeOnlyConfig(VALID_CONFIG), []);
});

test("paid-capable binding and placeholder are blocked", () => {
  const errors = validateFreeOnlyConfig({
    ...VALID_CONFIG,
    r2_buckets: [],
    vars: {
      MENU_DATA_URL:
        "https://YOUR_GITHUB_USERNAME.github.io/ku-meal/data/menu.json"
    }
  });

  assert.ok(errors.some((error) => error.includes("r2_buckets")));
  assert.ok(errors.some((error) => error.includes("플레이스홀더")));
});

test("paid-only CPU limits setting is blocked", () => {
  const errors = validateFreeOnlyConfig({
    ...VALID_CONFIG,
    limits: { cpu_ms: 10 }
  });

  assert.ok(errors.some((error) => error.includes("CPU limits")));
});
