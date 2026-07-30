import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";


const FORBIDDEN_BINDINGS = [
  "ai",
  "browser",
  "d1_databases",
  "durable_objects",
  "hyperdrive",
  "kv_namespaces",
  "queues",
  "r2_buckets",
  "routes",
  "services",
  "vectorize",
  "workflows"
];

export function validateFreeOnlyConfig(config) {
  const errors = [];

  if (config.workers_dev !== true) {
    errors.push("workers_dev는 true여야 합니다.");
  }
  if (config.limits?.cpu_ms !== 10) {
    errors.push("limits.cpu_ms는 무료 플랜 한도인 10이어야 합니다.");
  }
  if (
    !Array.isArray(config.secrets?.required) ||
    config.secrets.required.length !== 1 ||
    config.secrets.required[0] !== "KAKAO_SKILL_SECRET"
  ) {
    errors.push("필수 secret은 KAKAO_SKILL_SECRET 하나여야 합니다.");
  }

  for (const binding of FORBIDDEN_BINDINGS) {
    if (Object.hasOwn(config, binding)) {
      errors.push(`무료 전용 정책에서 금지된 설정입니다: ${binding}`);
    }
  }

  const variableNames = Object.keys(config.vars || {});
  if (
    variableNames.length !== 1 ||
    variableNames[0] !== "MENU_DATA_URL"
  ) {
    errors.push("일반 환경변수는 MENU_DATA_URL 하나만 허용합니다.");
  }

  const menuDataUrl = config.vars?.MENU_DATA_URL;
  if (typeof menuDataUrl !== "string" || menuDataUrl.includes("YOUR_GITHUB_USERNAME")) {
    errors.push("MENU_DATA_URL의 GitHub 사용자명 플레이스홀더를 교체해야 합니다.");
  } else {
    try {
      const url = new URL(menuDataUrl);
      if (
        url.protocol !== "https:" ||
        !url.hostname.endsWith(".github.io") ||
        !url.pathname.endsWith("/data/menu.json") ||
        url.username ||
        url.password ||
        url.search ||
        url.hash
      ) {
        errors.push("MENU_DATA_URL은 GitHub Pages의 HTTPS data/menu.json이어야 합니다.");
      }
    } catch {
      errors.push("MENU_DATA_URL이 올바른 URL이 아닙니다.");
    }
  }

  return errors;
}

function main() {
  const config = JSON.parse(readFileSync(new URL("../wrangler.jsonc", import.meta.url)));
  const errors = validateFreeOnlyConfig(config);

  if (errors.length) {
    console.error("[blocked] 무료 전용 배포 사전검사 실패:");
    for (const error of errors) console.error(`- ${error}`);
    process.exitCode = 1;
    return;
  }
  console.log("[ok] 무료 전용 Worker 설정 사전검사 통과");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}
