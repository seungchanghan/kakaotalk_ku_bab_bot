const today = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit"
}).format(new Date());

function safeSourceUrl(value) {
  try {
    const url = new URL(value);
    if (url.protocol === "https:" && url.hostname === "www.korea.ac.kr") {
      return url.href;
    }
  } catch {
    // Invalid or untrusted source URLs are not rendered as links.
  }
  return null;
}

function showError(message) {
  const error = document.createElement("p");
  error.className = "error";
  error.textContent = message;
  document.querySelector("#menus").replaceChildren(error);
}

fetch("./data/menu.json", { cache: "no-store" })
  .then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  })
  .then((data) => {
    document.querySelector("#updated").textContent =
      data.generatedAt ? `갱신: ${data.generatedAt}` : "아직 수집된 식단이 없습니다.";
    const root = document.querySelector("#menus");
    root.replaceChildren();

    for (const restaurant of Object.values(data.restaurants || {})) {
      const meals = restaurant.days?.[today] || {};
      const card = document.createElement("section");
      card.className = "card";
      const title = document.createElement("h2");
      title.textContent = restaurant.name;
      card.append(title);

      if (!Object.keys(meals).length) {
        const empty = document.createElement("p");
        empty.textContent = "오늘 등록된 식단이 없습니다.";
        card.append(empty);
      }

      for (const [mealType, entries] of Object.entries(meals)) {
        const heading = document.createElement("h3");
        heading.textContent = mealType;
        card.append(heading);
        const body = document.createElement("p");
        body.className = "meal";
        body.textContent = entries
          .map((entry) => [entry.title, entry.content].filter(Boolean).join("\n"))
          .join("\n\n");
        card.append(body);
      }

      const verifiedSource = safeSourceUrl(restaurant.sourceUrl);
      if (verifiedSource) {
        const source = document.createElement("a");
        source.href = verifiedSource;
        source.target = "_blank";
        source.rel = "noopener noreferrer";
        source.textContent = "고려대 공식 식단 원문";
        card.append(source);
      }
      root.append(card);
    }

    if (!Object.keys(data.restaurants || {}).length) {
      showError("수집기를 먼저 실행해 주세요.");
    }
  })
  .catch(() => {
    document.querySelector("#updated").textContent = "식단을 불러오지 못했습니다.";
    showError("식단 데이터 요청에 실패했습니다.");
  });

