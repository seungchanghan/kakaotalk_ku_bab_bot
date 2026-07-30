const SEOUL_TIME_ZONE = "Asia/Seoul";
const MIN_SECRET_LENGTH = 32;
const MAX_REQUEST_BYTES = 32 * 1024;
const MAX_MENU_DATA_BYTES = 256 * 1024;
const MEAL_TYPES = [
  ["조식", ["조식", "아침"]],
  ["석식", ["석식", "저녁"]],
  ["중식", ["중식", "점심"]]
];
const WEEKDAYS = new Map([
  ["월", 0],
  ["화", 1],
  ["수", 2],
  ["목", 3],
  ["금", 4],
  ["토", 5],
  ["일", 6]
]);

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
  return kakaoText(text, quickReplies(target));
}

export function selectTarget(data, utterance, now) {
  const normalized = utterance.replace(/\s+/g, "").toLowerCase();
  const restaurants = Object.entries(data?.restaurants || {});
  const matchedRestaurant = restaurants.find(([, restaurant]) =>
    [restaurant.shortName, restaurant.name, ...(restaurant.aliases || [])]
      .filter(Boolean)
      .some((alias) => normalized.includes(String(alias).replace(/\s+/g, "").toLowerCase()))
  ) || findFuzzyRestaurant(restaurants, utterance);

  const matchedMeal = MEAL_TYPES.find(([, aliases]) =>
    aliases.some((alias) => normalized.includes(alias))
  );

  const date = resolveDate(utterance, normalized, dateInSeoul(now));

  return {
    restaurantKey: matchedRestaurant?.[0] || null,
    date,
    mealType: matchedMeal?.[0] || "중식"
  };
}

function resolveDate(utterance, normalized, today) {
  const fullDate = utterance.match(
    /(20\d{2})[.\-/년 ]+(\d{1,2})[.\-/월 ]+(\d{1,2})/
  );
  if (fullDate) {
    return isoDate(fullDate[1], fullDate[2], fullDate[3], today);
  }

  const shortKoreanDate = utterance.match(/(?:^|\D)(\d{1,2})\s*월\s*(\d{1,2})\s*일?/);
  const shortNumericDate = utterance.match(
    /(?:^|\D)(\d{1,2})[./-](\d{1,2})(?![./\d-])/
  );
  const shortDate = shortKoreanDate || shortNumericDate;
  if (shortDate) {
    return isoDate(today.slice(0, 4), shortDate[1], shortDate[2], today);
  }

  const relativeDates = [
    [["그저께", "그제"], -2],
    [["어제"], -1],
    [["모레"], 2],
    [["내일"], 1],
    [["오늘"], 0]
  ];
  for (const [aliases, offset] of relativeDates) {
    if (aliases.some((alias) => normalized.includes(alias))) {
      return addDays(today, offset);
    }
  }

  const weekday = utterance.match(/(?:(지난|이번|다음)\s*주\s*)?([월화수목금토일])요일/);
  if (weekday) {
    const requested = WEEKDAYS.get(weekday[2]);
    const current = mondayBasedWeekday(today);
    const weekOffset = weekday[1] === "지난" ? -7 : weekday[1] === "다음" ? 7 : 0;
    const offset = weekday[1]
      ? weekOffset - current + requested
      : (requested - current + 7) % 7;
    return addDays(today, offset);
  }

  return today;
}

function isoDate(year, month, day, fallback) {
  const value = [
    String(year),
    String(month).padStart(2, "0"),
    String(day).padStart(2, "0")
  ].join("-");
  const parsed = new Date(`${value}T00:00:00Z`);
  if (
    Number.isNaN(parsed.getTime()) ||
    parsed.getUTCFullYear() !== Number(year) ||
    parsed.getUTCMonth() + 1 !== Number(month) ||
    parsed.getUTCDate() !== Number(day)
  ) {
    return fallback;
  }
  return value;
}

function mondayBasedWeekday(isoDateValue) {
  const day = new Date(`${isoDateValue}T00:00:00Z`).getUTCDay();
  return (day + 6) % 7;
}

function findFuzzyRestaurant(restaurants, utterance) {
  const tokens = String(utterance).toLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
  return restaurants.find(([, restaurant]) => {
    const aliases = [restaurant.shortName, ...(restaurant.aliases || [])]
      .filter(Boolean)
      .map((alias) => String(alias).replace(/\s+/g, "").toLowerCase())
      .filter((alias) => alias.length >= 3);
    return aliases.some((alias) =>
      tokens.some((token) =>
        Math.abs(token.length - alias.length) <= 1 &&
        editDistanceAtMostOne(token, alias)
      )
    );
  });
}

function editDistanceAtMostOne(left, right) {
  if (left === right) return true;
  if (Math.abs(left.length - right.length) > 1) return false;

  let first = left;
  let second = right;
  if (first.length > second.length) [first, second] = [second, first];

  let edits = 0;
  for (let i = 0, j = 0; i < first.length || j < second.length;) {
    if (first[i] === second[j]) {
      i += 1;
      j += 1;
      continue;
    }
    edits += 1;
    if (edits > 1) return false;
    if (first.length === second.length) i += 1;
    j += 1;
  }
  return true;
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
    const summary = summarizeEntry(entries[0]);
    summaries.push(`🏫 ${restaurant.shortName}\n${summary}`);
  }
  return truncate([
    `🍚 ${formatKoreanDate(target.date)} ${target.mealType}`,
    "",
    ...(summaries.length ? [summaries.join("\n\n")] : ["등록된 식단이 없습니다."]),
    "",
    "아래에서 식당을 누르면 전체 메뉴를 보여드려요."
  ].join("\n"));
}

function summarizeEntry(entry) {
  const lines = String(entry?.content || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const menuLines = [];

  for (const line of lines) {
    if (/^\[[^\]]+\]$/.test(line)) {
      if (menuLines.length) break;
      continue;
    }
    if (/^\(.*사이드\s*메뉴.*\)$/i.test(line)) continue;
    if (/^[₩￦]?\s*[\d,]+\s*원?$/.test(line)) continue;
    menuLines.push(line);
  }

  const tokens = menuLines
    .flatMap((line) => line.split(/\s+/))
    .map((token) => token.replace(/\*/g, "·"))
    .filter(Boolean);

  if (!tokens.length) {
    const title = String(entry?.title || "")
      .replace(/^\[|\]$/g, "")
      .trim();
    return title || "메뉴 정보 확인";
  }

  const visible = tokens.slice(0, 3).join(" · ");
  const remainder = tokens.length - 3;
  return remainder > 0 ? `${visible} 외 ${remainder}가지` : visible;
}

function formatKoreanDate(isoDate) {
  const date = new Date(`${isoDate}T12:00:00+09:00`);
  if (Number.isNaN(date.getTime())) return isoDate;
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: SEOUL_TIME_ZONE,
    month: "long",
    day: "numeric",
    weekday: "short"
  }).format(date);
}

function quickReplies(target) {
  return ["자연계", "산학관", "학생회관", "안암학사"].map((name) => ({
    label: name,
    action: "message",
    messageText: `${name} ${target.date} ${target.mealType}`
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
