const SEOUL_TIME_ZONE = "Asia/Seoul";
const MIN_SECRET_LENGTH = 32;
const MAX_REQUEST_BYTES = 32 * 1024;
const MAX_MENU_DATA_BYTES = 256 * 1024;
const MEAL_TYPES = [
  ["조식", ["조식", "아침"]],
  ["석식", ["석식", "저녁"]],
  ["중식", ["중식", "점심"]]
];

export default {
  async fetch(request, env) {
    return handleRequest(request, env);
  }
};

export async function handleRequest(request, env, fetchImpl = fetch) {
  const url = new URL(request.url);
  const isHealthRequest = request.method === "GET" && url.pathname === "/health";
  const isSkillRequest = request.method === "POST" && url.pathname === "/kakao/meal";

  if (!isHealthRequest && !isSkillRequest) {
    return textResponse("Not Found", 404);
  }

  if (
    typeof env.KAKAO_SKILL_SECRET !== "string" ||
    env.KAKAO_SKILL_SECRET.length < MIN_SECRET_LENGTH
  ) {
    return textResponse("Service Misconfigured", 503);
  }

  if (request.headers.get("x-kakao-skill-secret") !== env.KAKAO_SKILL_SECRET) {
    return textResponse("Unauthorized", 401);
  }

  if (isHealthRequest) {
    return jsonResponse({ ok: true, service: "ku-meal-kakao-skill" });
  }

  if (!isAllowedMenuDataUrl(env.MENU_DATA_URL)) {
    return textResponse("Service Misconfigured", 503);
  }

  let payload;
  try {
    payload = await readJsonWithLimit(request, MAX_REQUEST_BYTES);
  } catch (error) {
    if (error instanceof PayloadTooLargeError) {
      return textResponse("Payload Too Large", 413);
    }
    return kakaoText("요청 형식을 읽지 못했습니다. 잠시 후 다시 시도해 주세요.");
  }

  let data;
  try {
    const response = await fetchImpl(env.MENU_DATA_URL, {
      cf: { cacheTtl: 300, cacheEverything: true },
      headers: { accept: "application/json" }
    });
    if (!response.ok) throw new Error(`menu data HTTP ${response.status}`);
    data = await readJsonWithLimit(response, MAX_MENU_DATA_BYTES);
    if (data?.schemaVersion !== 1 || typeof data?.restaurants !== "object") {
      throw new Error("invalid menu data schema");
    }
  } catch {
    return kakaoText("학식 데이터를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.");
  }

  const utterance = String(payload?.userRequest?.utterance || "");
  const target = selectTarget(data, utterance, new Date());
  const text = formatMenu(data, target);
  return kakaoText(text, quickReplies());
}

export function selectTarget(data, utterance, now) {
  const normalized = utterance.replace(/\s+/g, "").toLowerCase();
  const restaurants = Object.entries(data?.restaurants || {});
  const matchedRestaurant = restaurants.find(([, restaurant]) =>
    [restaurant.shortName, restaurant.name, ...(restaurant.aliases || [])]
      .filter(Boolean)
      .some((alias) => normalized.includes(String(alias).replace(/\s+/g, "").toLowerCase()))
  );

  const matchedMeal = MEAL_TYPES.find(([, aliases]) =>
    aliases.some((alias) => normalized.includes(alias))
  );

  let date = dateInSeoul(now);
  if (normalized.includes("내일")) date = addDays(date, 1);
  const explicitDate = utterance.match(/(20\d{2})[.\-/년 ]+(\d{1,2})[.\-/월 ]+(\d{1,2})/);
  if (explicitDate) {
    date = [
      explicitDate[1],
      explicitDate[2].padStart(2, "0"),
      explicitDate[3].padStart(2, "0")
    ].join("-");
  }

  return {
    restaurantKey: matchedRestaurant?.[0] || null,
    date,
    mealType: matchedMeal?.[0] || "중식"
  };
}

export function formatMenu(data, target) {
  if (!data?.restaurants || !Object.keys(data.restaurants).length) {
    return "아직 수집된 학식 데이터가 없습니다.";
  }

  if (target.restaurantKey) {
    const restaurant = data.restaurants[target.restaurantKey];
    const entries = restaurant.days?.[target.date]?.[target.mealType] || [];
    if (!entries.length) {
      return [
        `🍚 ${restaurant.shortName} · ${target.date} · ${target.mealType}`,
        "",
        "등록된 식단이 없습니다.",
        restaurant.sourceUrl
      ].join("\n");
    }
    const details = entries.map((entry) =>
      [entry.title, entry.content].filter(Boolean).join("\n")
    );
    return truncate([
      `🍚 ${restaurant.shortName} · ${target.date} · ${target.mealType}`,
      "",
      ...details,
      "",
      `갱신: ${data.generatedAt || "확인 불가"}`,
      restaurant.sourceUrl
    ].join("\n"));
  }

  const summaries = [];
  for (const restaurant of Object.values(data.restaurants)) {
    const entries = restaurant.days?.[target.date]?.[target.mealType] || [];
    if (!entries.length) continue;
    const first = entries[0];
    const summary = (first.title || first.content || "").split("\n")[0];
    summaries.push(`• ${restaurant.shortName}: ${summary}`);
  }
  return truncate([
    `🍚 ${target.date} ${target.mealType} 한눈에 보기`,
    "",
    ...(summaries.length ? summaries : ["등록된 식단이 없습니다."]),
    "",
    "식당 이름을 입력하면 상세 메뉴를 보여드려요."
  ].join("\n"));
}

function quickReplies() {
  return ["자연계", "산학관", "학생회관", "안암학사"].map((name) => ({
    label: name,
    action: "message",
    messageText: `${name} 오늘 점심`
  }));
}

function kakaoText(text, quickReplies = []) {
  return jsonResponse({
    version: "2.0",
    template: {
      outputs: [{ simpleText: { text: truncate(text) } }],
      quickReplies
    }
  });
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: securityHeaders("application/json; charset=utf-8")
  });
}

function textResponse(body, status) {
  return new Response(body, {
    status,
    headers: securityHeaders("text/plain; charset=utf-8")
  });
}

function securityHeaders(contentType) {
  return {
    "content-type": contentType,
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'"
  };
}

function isAllowedMenuDataUrl(value) {
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      url.hostname.endsWith(".github.io") &&
      url.pathname.endsWith("/data/menu.json") &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash
    );
  } catch {
    return false;
  }
}

class PayloadTooLargeError extends Error {}

async function readJsonWithLimit(message, maximumBytes) {
  const declaredLength = Number(message.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > maximumBytes) {
    throw new PayloadTooLargeError();
  }
  if (!message.body) {
    throw new SyntaxError("missing JSON body");
  }

  const reader = message.body.getReader();
  const chunks = [];
  let totalBytes = 0;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    totalBytes += value.byteLength;
    if (totalBytes > maximumBytes) {
      await reader.cancel();
      throw new PayloadTooLargeError();
    }
    chunks.push(value);
  }

  const bytes = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
}

function dateInSeoul(date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: SEOUL_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function addDays(isoDate, days) {
  const date = new Date(`${isoDate}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function truncate(text) {
  return text.length <= 1000 ? text : `${text.slice(0, 985)}\n…(이하 생략)`;
}
