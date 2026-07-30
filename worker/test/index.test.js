import assert from "node:assert/strict";
import test from "node:test";
import { formatMenu, handleRequest, selectTarget } from "../src/index.js";


const DATA = {
  schemaVersion: 1,
  generatedAt: "2026-07-30T12:00:00+09:00",
  restaurants: {
    science: {
      name: "자연계 학생식당",
      shortName: "자연계",
      aliases: ["자연계", "애기능"],
      sourceUrl: "https://example.com/science",
      days: {
        "2026-07-30": {
          중식: [{ title: "", content: "제육덮밥\n배추김치", notes: "" }]
        },
        "2026-07-31": {
          중식: [{ title: "", content: "카레라이스\n깍두기", notes: "" }]
        }
      }
    }
  }
};
const SECRET = "test-secret-at-least-thirty-two-characters";


test("selectTarget recognizes aliases and tomorrow", () => {
  const target = selectTarget(
    DATA,
    "내일 애기능 점심",
    new Date("2026-07-30T03:00:00Z")
  );
  assert.deepEqual(target, {
    restaurantKey: "science",
    date: "2026-07-31",
    mealType: "중식"
  });
});

test("bare 밥 command defaults to today's lunch overview", () => {
  const target = selectTarget(
    DATA,
    "밥",
    new Date("2026-07-30T03:00:00Z")
  );
  assert.deepEqual(target, {
    restaurantKey: null,
    date: "2026-07-30",
    mealType: "중식"
  });
});

test("selectTarget recognizes relative dates, weekdays, short dates, and a typo", () => {
  const now = new Date("2026-07-30T03:00:00Z");
  const restaurants = {
    ...DATA.restaurants,
    dormitory: {
      shortName: "안암학사",
      aliases: ["안암학사", "기숙사", "긱식"],
      days: {}
    }
  };
  const data = { ...DATA, restaurants };

  assert.equal(selectTarget(data, "어제 학식", now).date, "2026-07-29");
  assert.equal(selectTarget(data, "그저께 학식", now).date, "2026-07-28");
  assert.equal(selectTarget(data, "모레 학식", now).date, "2026-08-01");
  assert.equal(selectTarget(data, "지난주 금요일 학식", now).date, "2026-07-24");
  assert.equal(selectTarget(data, "금요일 학식", now).date, "2026-07-31");
  assert.equal(selectTarget(data, "7월 29일 학식", now).date, "2026-07-29");
  assert.equal(selectTarget(data, "7/29 학식", now).date, "2026-07-29");
  assert.equal(
    selectTarget(data, "안암학샤 오늘 저녁", now).restaurantKey,
    "dormitory"
  );
});

test("formatMenu returns a detailed menu", () => {
  const text = formatMenu(DATA, {
    restaurantKey: "science",
    date: "2026-07-30",
    mealType: "중식"
  });
  assert.match(text, /제육덮밥/);
  assert.match(text, /자연계/);
});

test("overview summarizes actual dishes instead of internal category labels", () => {
  const data = {
    schemaVersion: 1,
    restaurants: {
      science: {
        shortName: "자연계",
        days: {
          "2026-07-31": {
            중식: [{
              title: "",
              content:
                "[학생식당]\n성준이의팟타이 새우볼꼬치 쌀국수장국 단무지 배추김치 양배추샐러드\n" +
                "(사이드메뉴: 닭강정)\n[교직원식당]\n김치날치알밥 물만두찜"
            }]
          }
        }
      },
      industry: {
        shortName: "산학관",
        days: {
          "2026-07-31": {
            중식: [{
              title: "중식B",
              content: "치킨까스*소스 콩나물국 쫄면야채무침 팽이버섯볶음 흑미밥"
            }]
          }
        }
      }
    }
  };

  const text = formatMenu(data, {
    restaurantKey: null,
    date: "2026-07-31",
    mealType: "중식"
  });

  assert.doesNotMatch(text, /\[학생식당\]|중식B/);
  assert.match(text, /성준이의팟타이 · 새우볼꼬치 · 쌀국수장국 외 3가지/);
  assert.match(text, /치킨까스·소스 · 콩나물국 · 쫄면야채무침 외 2가지/);
  assert.match(text, /7월 31일.*중식/);
});

test("Kakao endpoint validates secret and returns version 2.0", async () => {
  const request = new Request("https://worker.example/kakao/meal", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-kakao-skill-secret": SECRET
    },
    body: JSON.stringify({ userRequest: { utterance: "자연계 오늘 점심" } })
  });
  const fakeFetch = async () =>
    new Response(JSON.stringify(DATA), {
      headers: { "content-type": "application/json" }
    });

  const response = await handleRequest(
    request,
    {
      KAKAO_SKILL_SECRET: SECRET,
      MENU_DATA_URL: "https://tester.github.io/ku-meal/data/menu.json"
    },
    fakeFetch
  );
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.version, "2.0");
  assert.match(body.template.outputs[0].simpleText.text, /제육덮밥/);
});

test("missing or short secret fails closed", async () => {
  const request = () =>
    new Request("https://worker.example/kakao/meal", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{}"
    });
  let fetchCalled = false;
  const fakeFetch = async () => {
    fetchCalled = true;
    return new Response("{}");
  };

  const missing = await handleRequest(
    request(),
    { MENU_DATA_URL: "https://tester.github.io/ku-meal/data/menu.json" },
    fakeFetch
  );
  const short = await handleRequest(
    request(),
    {
      KAKAO_SKILL_SECRET: "too-short",
      MENU_DATA_URL: "https://tester.github.io/ku-meal/data/menu.json"
    },
    fakeFetch
  );

  assert.equal(missing.status, 503);
  assert.equal(short.status, 503);
  assert.equal(fetchCalled, false);
});

test("wrong secret is rejected before fetching menu data", async () => {
  const request = new Request("https://worker.example/kakao/meal", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-kakao-skill-secret": "wrong"
    },
    body: "{}"
  });
  let fetchCalled = false;

  const response = await handleRequest(
    request,
    {
      KAKAO_SKILL_SECRET: SECRET,
      MENU_DATA_URL: "https://tester.github.io/ku-meal/data/menu.json"
    },
    async () => {
      fetchCalled = true;
      return new Response("{}");
    }
  );

  assert.equal(response.status, 401);
  assert.equal(fetchCalled, false);
});

test("oversized authorized request is rejected before fetching", async () => {
  const request = new Request("https://worker.example/kakao/meal", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "content-length": "40000",
      "x-kakao-skill-secret": SECRET
    },
    body: "{}"
  });
  let fetchCalled = false;

  const response = await handleRequest(
    request,
    {
      KAKAO_SKILL_SECRET: SECRET,
      MENU_DATA_URL: "https://tester.github.io/ku-meal/data/menu.json"
    },
    async () => {
      fetchCalled = true;
      return new Response("{}");
    }
  );

  assert.equal(response.status, 413);
  assert.equal(fetchCalled, false);
});

test("health endpoint also requires the secret", async () => {
  const unauthorized = await handleRequest(
    new Request("https://worker.example/health"),
    { KAKAO_SKILL_SECRET: SECRET }
  );
  const authorized = await handleRequest(
    new Request("https://worker.example/health", {
      headers: { "x-kakao-skill-secret": SECRET }
    }),
    { KAKAO_SKILL_SECRET: SECRET }
  );

  assert.equal(unauthorized.status, 401);
  assert.equal(authorized.status, 200);
});

test("menu data URL is restricted to GitHub Pages JSON", async () => {
  const request = new Request("https://worker.example/kakao/meal", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-kakao-skill-secret": SECRET
    },
    body: "{}"
  });
  let fetchCalled = false;

  const response = await handleRequest(
    request,
    {
      KAKAO_SKILL_SECRET: SECRET,
      MENU_DATA_URL: "https://attacker.example/menu.json"
    },
    async () => {
      fetchCalled = true;
      return new Response("{}");
    }
  );

  assert.equal(response.status, 503);
  assert.equal(fetchCalled, false);
});
