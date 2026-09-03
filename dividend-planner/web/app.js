/* 배당 설계기 — 라우터 + 화면. 빌드 단계 없이 브라우저에서 바로 돈다. */
(function () {
  "use strict";
  var F = C.F, esc = C.esc;

  /* 이 앱이 어느 경로에 올라가 있는지 스스로 알아낸다. code-server 의
     /proxy/8770/ 이나 리버스 프록시 뒤에서도 그대로 돌아야 하기 때문이다.
     자기 <script> 태그의 절대 URL에서 디렉터리를 떼면 그게 앱의 뿌리다.
     라우트를 한 칸 깊이(/plan, /ticker)로만 쓰는 것도 같은 이유다 —
     그래야 상대 경로로 적은 CSS·JS가 어느 화면에서든 같은 곳을 가리킨다. */
  var BASE = (function () {
    var tag = document.currentScript ||
      document.querySelector('script[src$="app.js"]');
    var href = tag ? tag.src : location.href;
    return new URL(href.replace(/[^/]*$/, ""), location.href).pathname;
  })();
  function url(route) { return BASE + (route || ""); }
  function currentRoute() {
    var path = location.pathname;
    if (path.indexOf(BASE) === 0) path = path.slice(BASE.length);
    return path.replace(/^\/+/, "").replace(/\/+$/, "");
  }
  function planSearch() {
    // 종목 화면에서 계획 화면으로 돌아갈 때 symbol 만 떼어낸다
    var q = new URLSearchParams(location.search);
    q.delete("symbol");
    var text = q.toString();
    return text ? "?" + text : "";
  }
  var app = document.getElementById("app");
  var PROFILE_KEY = "dividend-planner-profile";
  var THEME_KEY = "dividend-planner-theme";
  var charts = [];          // 리사이즈 때 다시 그릴 차트들
  var lastPlan = null;
  var health = null;        // 데이터 기준월·출처·최대 백테스트 기간

  /* ------------------------------------------------------------------ 테마 */
  var SUN = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.2 5.2l1.4 1.4M17.4 17.4l1.4 1.4M18.8 5.2l-1.4 1.4M6.6 17.4l-1.4 1.4"/></svg>';
  var MOON = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.5 14.3A8.6 8.6 0 1 1 9.7 3.5a6.9 6.9 0 0 0 10.8 10.8Z"/></svg>';
  function isDark() {
    var t = document.documentElement.getAttribute("data-theme");
    if (t) return t === "dark";
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function paintThemeButton() {
    document.getElementById("theme").innerHTML = isDark() ? SUN : MOON;
  }
  try {
    var saved = localStorage.getItem(THEME_KEY);
    if (saved) document.documentElement.setAttribute("data-theme", saved);
  } catch (e) { /* 저장소 접근 불가 — 시스템 설정을 따른다 */ }
  document.getElementById("theme").onclick = function () {
    var next = isDark() ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
    paintThemeButton();
    drawCharts();
  };
  paintThemeButton();

  /* -------------------------------------------------------------------- API */
  function post(path, body) {
    return fetch(url(path), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      if (!r.ok) return r.text().then(function (t) { throw new Error(r.status + " " + t.slice(0, 200)); });
      return r.json();
    });
  }
  function get(path) {
    return fetch(url(path)).then(function (r) {
      if (!r.ok) return r.text().then(function (t) { throw new Error(r.status + " " + t.slice(0, 200)); });
      return r.json();
    });
  }

  /* ----------------------------------------------------------------- 라우터 */
  function go(route) {
    history.pushState({}, "", url(route));
    render();
  }
  window.addEventListener("popstate", render);
  document.addEventListener("click", function (e) {
    var a = e.target.closest("a[data-link]");
    if (a) { e.preventDefault(); go(a.getAttribute("data-route") || ""); }
  });
  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(drawCharts, 140);
  });

  function drawCharts() {
    charts.forEach(function (c) {
      var el = document.getElementById(c.id);
      if (!el) return;
      var w = Math.max(280, el.clientWidth);
      el.innerHTML = c.draw(w);
    });
  }
  function chart(draw, height) {
    var id = "chart-" + charts.length + "-" + Math.random().toString(36).slice(2, 7);
    charts.push({ id: id, draw: draw });
    return '<div id="' + id + '" style="min-height:' + (height || 220) + 'px"></div>';
  }
  function resetCharts() { charts = []; }
  function mount(html) {
    app.innerHTML = html;
    drawCharts();
  }

  /* ------------------------------------------------------------- 마크다운 */
  function inline(s) {
    return esc(s)
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }
  function markdown(text) {
    return text.split(/\n{2,}/).map(function (block) {
      var b = block.trim();
      if (!b) return "";
      if (/^#{1,6}\s/.test(b)) return "<h2>" + inline(b.replace(/^#{1,6}\s*/, "")) + "</h2>";
      if (/^[-*]\s/.test(b)) {
        return "<ul>" + b.split("\n").filter(Boolean).map(function (l) {
          return "<li>" + inline(l.replace(/^\s*[-*]\s*/, "")) + "</li>";
        }).join("") + "</ul>";
      }
      return "<p>" + inline(b.replace(/\n/g, " ")) + "</p>";
    }).join("");
  }

  /* --------------------------------------------------------------- AI 패널 */
  function aiPanel(id, title, sub) {
    return '<section class="ai" id="' + id + '"><div class="ai-head"><div>' +
      '<div class="card-title">' + esc(title) + '</div><div class="card-sub">' + esc(sub) + "</div></div>" +
      '<div class="spacer"></div><button class="btn ghost" data-ai-retry="' + id + '" style="padding:6px 12px;font-size:12.5px">다시 생성</button></div>' +
      '<div class="ai-body" data-ai-body><span class="eyebrow">대기 중</span></div></section>';
  }
  function runAi(id, endpoint, body) {
    var root = document.getElementById(id);
    if (!root) return;
    var out = root.querySelector("[data-ai-body]");
    if (root._abort) root._abort.abort();
    var ctrl = new AbortController();
    root._abort = ctrl;
    out.innerHTML = '<span class="eyebrow">요청 전송 중</span>';
    var text = "";
    fetch(url(endpoint), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body), signal: ctrl.signal
    }).then(function (res) {
      if (!res.body) throw new Error("스트림을 열 수 없습니다");
      var reader = res.body.getReader(), dec = new TextDecoder(), buf = "";
      function pump() {
        return reader.read().then(function (r) {
          if (r.done) return;
          buf += dec.decode(r.value, { stream: true });
          var parts = buf.split("\n\n");
          buf = parts.pop() || "";
          parts.forEach(function (raw) {
            var ev = /^event: (.+)$/m.exec(raw), da = /^data: (.+)$/m.exec(raw);
            if (!ev || !da) return;
            var data = JSON.parse(da[1]);
            if (ev[1] === "phase") {
              if (!text) out.innerHTML = '<span class="eyebrow">' + esc(data.label) + "</span>";
            } else if (ev[1] === "delta") {
              text += data.text;
              out.innerHTML = markdown(text);
              out.classList.add("ai-cursor");
            } else if (ev[1] === "final") {
              out.classList.remove("ai-cursor");
              if (data.truncated) out.innerHTML += '<p class="eyebrow">설명이 길이 제한으로 잘렸습니다.</p>';
            } else if (ev[1] === "error") {
              out.classList.remove("ai-cursor");
              out.innerHTML += '<div class="notice">설명을 불러오지 못했습니다 — ' + esc(data.message) + "</div>";
            }
          });
          return pump();
        });
      }
      return pump();
    }).catch(function (err) {
      if (ctrl.signal.aborted) return;
      out.classList.remove("ai-cursor");
      out.innerHTML = '<div class="notice">설명을 불러오지 못했습니다 — ' + esc(err.message) + "</div>";
    });
  }

  /* ------------------------------------------------------- 위저드 (홈 화면) */
  var HOUSEHOLDS = [["single", "1인"], ["couple", "부부"], ["couple_children", "부부+자녀"], ["single_parent", "한부모+자녀"]];
  var HOUSINGS = [["owned", "자가 (대출 없음)"], ["owned_loan", "자가 (대출 중)"], ["jeonse", "전세"], ["monthly_rent", "월세"]];
  var RISKS = [["conservative", "안정형", "변동성·배당 삭감 이력에 더 큰 벌점"],
    ["balanced", "중립형", "기본값"], ["aggressive", "공격형", "성장·수익 팩터에 더 큰 가중치"]];
  var DEFAULT_PROFILE = {
    age: 35, retire_age: 60, household_type: "couple", housing: "owned",
    monthly_income_after_tax_krw: null, monthly_spending_krw: null,
    current_assets_krw: 0, pension_monthly_krw: 0
  };
  var SCENARIO_LABEL = { minimum: "최소 노후", adequate: "적정 노후", comfortable: "여유 노후" };
  var wiz = { step: 0, scenario: "adequate", risk: "balanced", us: 0.75, etf: 0.6,
    monthly: null, goal: null, busy: false, error: null };
  function loadProfile() {
    try {
      var raw = sessionStorage.getItem(PROFILE_KEY);
      if (raw) return Object.assign({}, DEFAULT_PROFILE, JSON.parse(raw));
    } catch (e) {}
    return Object.assign({}, DEFAULT_PROFILE);
  }
  var profile = loadProfile();
  function setProfile(k, v) {
    profile[k] = v;
    try { sessionStorage.setItem(PROFILE_KEY, JSON.stringify(profile)); } catch (e) {}
  }

  function numField(key, label, hint, placeholder, quick) {
    var v = profile[key];
    return '<div class="field"><label for="f-' + key + '">' + esc(label) +
      (hint ? '<span class="hint">' + esc(hint) + "</span>" : "") + "</label>" +
      '<input id="f-' + key + '" inputmode="numeric" data-num="' + key + '" value="' +
      (v == null ? "" : Number(v).toLocaleString("ko-KR")) + '" placeholder="' + esc(placeholder || "모르면 비워두세요") + '">' +
      (quick ? '<div class="quick">' + quick.map(function (q) {
        return '<button data-quick="' + key + '" data-value="' + q[1] + '">' + esc(q[0]) + "</button>";
      }).join("") + '<button data-quick="' + key + '" data-value="">비우기</button></div>' : "") + "</div>";
  }
  function chipRow(key, options) {
    return '<div class="chips">' + options.map(function (o) {
      return '<button class="chip' + (profile[key] === o[0] ? " on" : "") + '" data-chip="' + key + '" data-value="' + o[0] + '">' + esc(o[1]) + "</button>";
    }).join("") + "</div>";
  }

  function renderHome() {
    resetCharts();
    var rail = ["기본 정보", "소득과 자산", "목표 확정"].map(function (t, i) {
      return '<div class="' + (wiz.step === i + 1 ? "on" : "") + '">step ' + (i + 1) + " / 3 · " + esc(t) + "</div>";
    }).join("");
    var body;
    if (wiz.step === 0) {
      body = '<section class="hero"><h1>노후에 매달 얼마가 필요하고,<br><em>배당으로 얼마나</em> 채울 수 있을까요?</h1>' +
        "<p>목표 금액을 몰라도 괜찮습니다. 아는 것만 입력하면 나머지는 통계로 추정해 목표를 계산합니다. " +
        "계산에 쓴 가정과 추정 항목은 전부 화면에 남습니다.</p>" +
        '<button class="btn" data-step="1">목표 금액 계산하기</button></section>' +
        '<div class="notice"><b>이 도구가 하는 일</b> — 나이·소득으로 은퇴 시점 필요 생활비를 역산하고, ' +
        '36종목 유니버스를 팩터 점수로 줄 세워 포트폴리오를 만들고, 같은 전략을 과거에 실제로 굴렸다면 ' +
        '어떤 배당이 들어왔을지 실측 데이터로 검증합니다.</div>';
    } else if (wiz.step === 1) {
      body = '<div class="steprail">' + rail + '</div><h2>당신에 대해 알려주세요</h2>' +
        '<p class="card-sub" style="margin-bottom:18px">모르는 항목은 비워두세요. 통계로 추정하고, 무엇을 추정했는지 다음 화면에 표시합니다.</p>' +
        '<div class="grid g2">' + numField("age", "현재 나이", null, "35") + numField("retire_age", "은퇴 희망 나이", null, "60") + "</div>" +
        '<div class="field"><label>가구 구성<span class="hint">노후 생활비 하한을 결정합니다</span></label>' + chipRow("household_type", HOUSEHOLDS) + "</div>" +
        '<div class="field"><label>주거 형태<span class="hint">노후 주거비 가산액을 결정합니다</span></label>' + chipRow("housing", HOUSINGS) + "</div>" +
        '<div class="row" style="margin-top:22px"><button class="btn ghost" data-step="0">이전</button><button class="btn" data-step="2">다음 단계</button></div>';
    } else if (wiz.step === 2) {
      body = '<div class="steprail">' + rail + '</div><h2>소득과 자산</h2>' +
        '<p class="card-sub" style="margin-bottom:18px">비워두면 연령대 중위소득·평균 소비성향 통계로 추정합니다.</p>' +
        '<div class="grid g2">' +
        numField("monthly_income_after_tax_krw", "세후 월 소득", null, "모르면 비워두세요", [["300만", 3000000], ["500만", 5000000], ["800만", 8000000]]) +
        numField("monthly_spending_krw", "월 지출", null, "모르면 비워두세요", [["200만", 2000000], ["300만", 3000000], ["500만", 5000000]]) +
        numField("current_assets_krw", "현재 금융자산", null, "0", [["1천만", 10000000], ["5천만", 50000000], ["1억", 100000000], ["3억", 300000000]]) +
        numField("pension_monthly_krw", "은퇴 후 예상 연금 (월)", "국민연금+퇴직연금, 모르면 0", "0", [["80만", 800000], ["120만", 1200000], ["200만", 2000000]]) +
        "</div>" +
        '<div class="row" style="margin-top:22px"><button class="btn ghost" data-step="1">이전</button>' +
        '<button class="btn" data-calc' + (wiz.busy ? " disabled" : "") + ">" + (wiz.busy ? "계산 중…" : "목표 계산하기") + "</button></div>" +
        (wiz.error ? '<div class="notice" style="margin-top:14px">' + esc(wiz.error) + "</div>" : "");
    } else {
      body = renderGoalStep(rail);
    }
    mount('<div class="wrap narrow rise">' + body + "</div>");
  }

  function renderGoalStep(rail) {
    var g = wiz.goal, s = g.scenarios[wiz.scenario];
    var monthly = wiz.monthly == null ? g.monthly_contribution_krw : wiz.monthly;
    var cards = ["minimum", "adequate", "comfortable"].map(function (k) {
      var sc = g.scenarios[k];
      return '<button class="' + (wiz.scenario === k ? "on" : "") + '" data-scn="' + k + '">' +
        '<div class="lab">' + esc(sc.label) + '</div><div class="big">' + F.wonShort(sc.target_monthly_dividend) + "</div>" +
        '<div class="sub">현재가치 생활비 ' + F.wonShort(sc.living_cost_today) +
        (sc.floor_applied ? " · 통계 하한 적용" : "") + "</div></button>";
    }).join("");
    var est = g.estimated_fields.filter(function (e) { return e.field !== "monthly_contribution_krw"; });
    return '<div class="steprail">' + rail + '</div><h2>목표 확정</h2>' +
      '<p class="card-sub" style="margin-bottom:16px">' + g.age + "세 → " + g.retire_age + "세, " +
      g.horizon_years + "년 동안 준비합니다. 은퇴 후 필요 생활비는 현재 지출의 " +
      Math.round(g.assumptions.replacement_ratio * 100) + "%로 보고, 물가상승률 연 " +
      F.pct(g.assumptions.inflation, 1) + "로 은퇴 시점 금액으로 환산했습니다.</p>" +
      '<div class="scn" style="margin-bottom:16px">' + cards + "</div>" +
      (est.length ? '<div class="notice" style="margin-bottom:16px"><b>' + est.length +
        "개 항목을 추정했습니다.</b> " + est.map(function (e) {
          return e.label + " " + F.wonShort(e.value) + " (" + e.basis + ")";
        }).join(" · ") + ". 정확한 값을 알면 이전 단계에서 입력해 주세요.</div>" : "") +
      '<div class="card"><div class="card-title">투자 조건</div>' +
      '<div class="card-sub">추천 포트폴리오의 성격을 결정합니다.</div>' +
      '<div class="field" style="margin-top:14px"><label>월 적립액 <span class="hint">투자에 쓸 수 있는 돈</span></label>' +
      '<input inputmode="numeric" data-monthly value="' + Math.round(monthly).toLocaleString("ko-KR") + '"></div>' +
      '<div class="field"><label>위험 성향</label><div class="chips">' + RISKS.map(function (r) {
        return '<button class="chip' + (wiz.risk === r[0] ? " on" : "") + '" data-risk="' + r[0] + '" title="' + esc(r[2]) + '">' + esc(r[1]) + "</button>";
      }).join("") + "</div></div>" +
      '<div class="field"><label>ETF 비중 <span class="hint">나머지는 개별주</span></label>' +
      '<div class="row"><input type="range" min="0" max="100" step="5" value="' + Math.round(wiz.etf * 100) +
      '" data-etf style="flex:1"><b class="num">' + Math.round(wiz.etf * 100) + '%</b></div>' +
      '<div class="card-sub" style="margin-top:6px">ETF를 코어로 깔면 종목 하나가 어긋나도 전체가 덜 흔들립니다. ' +
      '개별주는 배당수익률과 배당성장률을 끌어올리는 역할을 맡습니다. ' +
      '유니버스의 ETF는 모두 미국 상장이라 ETF 비중은 미국 비중을 넘을 수 없습니다.</div></div>' +
      '<div class="field"><label>미국 주식 비중 <span class="hint">나머지는 국내</span></label>' +
      '<div class="row"><input type="range" min="0" max="100" step="5" value="' + Math.round(wiz.us * 100) +
      '" data-us style="flex:1"><b class="num">' + Math.round(wiz.us * 100) + '%</b></div>' +
      '<div class="card-sub" style="margin-top:6px">미국 종목은 배당 이력이 길고 분기·월 배당이 많아 현금흐름이 고르지만 환율 위험이 있습니다. ' +
      '국내 종목은 원화 생활비와 직결되고 세금 처리가 단순합니다.</div></div></div>' +
      '<div class="row" style="margin-top:4px"><button class="btn ghost" data-step="2">← 조건 다시 입력</button>' +
      '<button class="btn brass" data-plan>' + F.wonShort(s.target_monthly_dividend) + " 목표로 계획 보기</button></div>";
  }

  function parseNum(str) {
    var t = String(str).replace(/[^0-9.-]/g, "");
    if (!t) return null;
    var v = Number(t);
    return isNaN(v) ? null : v;
  }

  app.addEventListener("click", function (e) {
    var t = e.target;
    var step = t.closest("[data-step]");
    if (step) { wiz.step = Number(step.getAttribute("data-step")); wiz.error = null; return renderHome(); }
    var chip = t.closest("[data-chip]");
    if (chip) { setProfile(chip.getAttribute("data-chip"), chip.getAttribute("data-value")); return renderHome(); }
    var quick = t.closest("[data-quick]");
    if (quick) {
      var val = quick.getAttribute("data-value");
      setProfile(quick.getAttribute("data-quick"), val === "" ? null : Number(val));
      return renderHome();
    }
    var scn = t.closest("[data-scn]");
    if (scn) { wiz.scenario = scn.getAttribute("data-scn"); return renderHome(); }
    var risk = t.closest("[data-risk]");
    if (risk) { wiz.risk = risk.getAttribute("data-risk"); return renderHome(); }
    if (t.closest("[data-calc]")) {
      wiz.busy = true; wiz.error = null; renderHome();
      return post("api/goal", profile).then(function (g) {
        wiz.goal = g; wiz.monthly = g.monthly_contribution_krw; wiz.busy = false; wiz.step = 3; renderHome();
      }).catch(function (err) {
        wiz.busy = false; wiz.error = "목표를 계산하지 못했습니다: " + err.message; renderHome();
      });
    }
    if (t.closest("[data-plan]")) {
      var g = wiz.goal, sc = g.scenarios[wiz.scenario];
      var q = new URLSearchParams({
        target: String(Math.round(sc.target_monthly_dividend)),
        initial: String(Math.round(g.current_assets_krw || 0)),
        monthly: String(Math.round(wiz.monthly == null ? g.monthly_contribution_krw : wiz.monthly)),
        horizon: String(g.horizon_years), risk: wiz.risk, us: String(wiz.us),
        etf: String(wiz.etf), bt: "10", scn: wiz.scenario
      });
      return go("plan?" + q.toString());
    }
    var retry = t.closest("[data-ai-retry]");
    if (retry && lastPlan) {
      var pid = retry.getAttribute("data-ai-retry");
      if (pid === "ai-strategy") return runAi(pid, "api/ai/strategy", lastPlan);
      if (pid === "ai-ticker" && window._tickerBody) return runAi(pid, window._tickerUrl, window._tickerBody);
    }
  });
  app.addEventListener("change", function (e) {
    var num = e.target.getAttribute && e.target.getAttribute("data-num");
    if (num) {
      var v = parseNum(e.target.value);
      if (v == null && (num === "age" || num === "retire_age")) v = DEFAULT_PROFILE[num];
      setProfile(num, v);
      // 입력 중에는 화면을 다시 그리지 않는다 — 포커스가 살아 있는 노드를 갈아치우면 터진다
      e.target.value = v == null ? "" : Number(v).toLocaleString("ko-KR");
      return;
    }
    if (e.target.hasAttribute && e.target.hasAttribute("data-monthly")) {
      wiz.monthly = Math.max(0, parseNum(e.target.value) || 0);
      e.target.value = Math.round(wiz.monthly).toLocaleString("ko-KR");
      var btn = app.querySelector("[data-plan]");
      if (btn && wiz.goal) {
        btn.textContent = F.wonShort(wiz.goal.scenarios[wiz.scenario].target_monthly_dividend) + " 목표로 계획 보기";
      }
      return;
    }
  });
  app.addEventListener("input", function (e) {
    var slider = e.target.hasAttribute && (e.target.hasAttribute("data-us") ? "us"
      : e.target.hasAttribute("data-etf") ? "etf" : null);
    if (slider) {
      wiz[slider] = Number(e.target.value) / 100;
      var out = e.target.parentNode.querySelector("b");
      if (out) out.textContent = Math.round(wiz[slider] * 100) + "%";
      return;
    }
  });

  /* ------------------------------------------------------------ 계획 화면 */
  function planQuery(params) {
    function n(key, dflt) {
      var v = Number(params.get(key));
      return params.get(key) !== null && isFinite(v) ? v : dflt;
    }
    return {
      target_monthly_dividend_krw: n("target", 3000000),
      initial_capital_krw: n("initial", 0),
      monthly_contribution_krw: n("monthly", 1000000),
      horizon_years: Math.max(1, Math.min(50, n("horizon", 20))),
      risk_preference: params.get("risk") || "balanced",
      us_ratio: n("us", 0.75),
      etf_ratio: n("etf", 0.6),
      drip: params.get("drip") !== "0",
      after_tax: params.get("tax") !== "0",
      backtest_years: Math.max(3, Math.min(maxBacktestYears(), n("bt", 10)))
    };
  }
  /* 타일 값이 "19년 7개월 후"처럼 한글과 공백이 섞이면 숫자 서체의 넓은 공백이 그대로
     드러난다. 그런 값은 본문 서체로 돌린다. 순수한 숫자·금액은 숫자 서체를 유지한다. */
  function valueClass(text) {
    return /\s/.test(text) && /[가-힣]/.test(text) ? "v text" : "v";
  }
  function tile(label, value, note) {
    return '<div class="tile"><div class="k">' + esc(label) + '</div><div class="' +
      valueClass(String(value)) + '">' + esc(value) + '</div><div class="n">' +
      esc(note) + "</div></div>";
  }
  function maxBacktestYears() {
    return (health && health.max_backtest_years) || 15;
  }
  /* 담긴 이유는 바구니마다 다르다. ETF는 점수가 높아서가 아니라 코어라서 담겼고,
     개별주는 점수 순서로 담겼다. 한 줄에 같은 라벨을 붙이면 그 차이가 지워진다. */
  function tiers(portfolio) {
    var map = {};
    portfolio.forEach(function (h) {
      if (h.kind === "etf") {
        map[h.symbol] = { key: "core", label: "코어", desc: "ETF 코어 — 자체 분산으로 기반을 맡는 자리" };
      }
    });
    var stocks = portfolio.filter(function (h) { return h.kind !== "etf"; })
      .sort(function (a, b) { return b.score - a.score; });
    var n = stocks.length;
    var topN = Math.max(1, Math.round(n * 0.3));
    var goodN = Math.max(topN + 1, Math.round(n * 0.7));
    stocks.forEach(function (h, i) {
      map[h.symbol] = i < topN
        ? { key: "top", label: "강력 추천", desc: "개별주 중 팩터 점수 상위 30%" }
        : i < goodN
          ? { key: "good", label: "추천", desc: "핵심 팩터에서 평균 이상" }
          : { key: "diversify", label: "분산 편입", desc: "섹터·현금흐름 분산을 위해 담은 종목" };
    });
    return map;
  }
  function setParam(patch) {
    var q = new URLSearchParams(location.search);
    q.delete("symbol");
    Object.keys(patch).forEach(function (k) { q.set(k, patch[k]); });
    go("plan?" + q.toString());
  }

  function renderPlan() {
    var params = new URLSearchParams(location.search);
    var req = planQuery(params);
    app.innerHTML = '<div class="center"><div class="spin"></div><div>포트폴리오를 계산하고 백테스트를 돌리는 중…</div></div>';
    post("api/plan", req).then(function (plan) {
      lastPlan = plan;
      paintPlan(plan, params);
    }).catch(function (err) {
      app.innerHTML = '<div class="wrap narrow"><div class="notice">계획을 불러오지 못했습니다: ' +
        esc(err.message) + '</div><p><a class="btn ghost" href="' + url("") + '" data-route="" data-link>처음으로</a></p></div>';
    });
  }

  function paintPlan(plan, params) {
    resetCharts();
    var d = plan.diagnosis, req = plan.request, a = plan.assumptions;
    var bt = plan.backtest.summary, pj = plan.projection;
    var tierMap = tiers(plan.portfolio);
    var maxW = Math.max.apply(null, plan.portfolio.map(function (h) { return h.weight; }));
    var now = new Date();
    var projPoints = pj.monthly.map(function (m) { return [m.month, m.dividend]; });
    var xLabels = pj.monthly.filter(function (m) { return m.month % 60 === 0; }).map(function (m) {
      return { x: m.month, label: (now.getFullYear() + Math.floor((now.getMonth() + m.month) / 12)) + "년" };
    });
    var btMonths = plan.backtest.monthly;
    var valuePoints = btMonths.map(function (m, i) { return [i, m.value]; });
    var contribPoints = btMonths.map(function (m, i) { return [i, m.contributed]; });
    var btLabels = btMonths.map(function (m, i) { return { i: i, m: m }; })
      .filter(function (x) { return x.i % 24 === 0; })
      .map(function (x) { return { x: x.i, label: x.m.date.slice(0, 4) }; });
    var divTtm = btMonths.map(function (m, i) { return [i, m.dividend_ttm / 12]; });

    var verdict =
      '<section class="verdict rise">' + C.dial(d.achievement_ratio) +
      "<div><h2>" + (d.achieved
        ? "목표를 채웁니다 — " + req.horizon_years + "년 뒤 월 " + F.wonFull(d.expected_monthly_dividend)
        : "지금 계획으로는 목표에 " + F.wonShort(d.shortfall) + " 부족합니다") + "</h2>" +
      "<p>" + (d.achieved
        ? "목표 " + F.wonFull(d.target) + "에 " + (d.achieve_text || "-") + " 도달합니다. 필요 총자산은 " +
          F.wonShort(d.required_assets) + "이고, 전망 기준 필요 월 적립액은 " +
          F.wonFull(d.required_monthly_contribution || 0) + "입니다."
        : (d.required_monthly_contribution == null
            ? "이 가정에서는 월 적립액을 아무리 올려도 " + req.horizon_years +
              "년 안에는 목표에 닿지 않습니다. 투자 기간을 늘리거나 목표를 낮춰 보세요."
            : "목표를 맞추려면 월 적립액을 " + F.wonFull(d.required_monthly_contribution) +
              "으로 (지금보다 " + F.wonFull(d.extra_monthly_needed) + " 증액) 올려야 합니다." +
              (d.achieve_text
                ? " 현 적립액을 유지한다면 " + d.achieve_text + "에 목표에 닿습니다."
                : " 현 적립액을 유지하면 50년 안에는 목표에 닿지 않습니다."))) + "</p>" +
      '<div class="row" style="margin-top:12px">' +
      '<span class="badge ' + (d.achieved ? "ok" : "warn") + '">' + (d.achieved ? "목표 달성 가능" : "목표 미달") + "</span>" +
      '<span class="badge">' + esc(plan.weight_profile.profile) + " 프로파일</span>" +
      '<span class="badge">세후 배당수익률 ' + F.pct(d.portfolio_yield_after_tax, 2) + "</span>" +
      '<span class="badge">ETF ' + F.pct(plan.weight_profile.etf_weight, 0) +
      " · 미국 " + F.pct(req.us_ratio, 0) + "</span>" +
      "</div></div></section>";

    var tiles = '<div class="tiles">' + [
      ["목표 월 배당", F.wonShort(d.target),
        SCENARIO_LABEL[params.get("scn")] ? SCENARIO_LABEL[params.get("scn")] + " 시나리오" : "직접 입력"],
      ["전망 월 배당", F.wonShort(d.expected_monthly_dividend), req.horizon_years + "년 후 · " + F.pct(d.achievement_ratio, 0)],
      ["필요 총자산", F.wonShort(d.required_assets), "세후 " + F.pct(d.portfolio_yield_after_tax, 2) + " 기준"],
      ["목표 도달 시점", d.achieve_text || "기간 내 미도달", d.achieve_month ? d.achieve_month + "개월" : "—"]
    ].map(function (t) { return tile(t[0], t[1], t[2]); }).join("") + "</div>";

    var controls =
      '<section class="card"><div class="card-title">조건 조정</div>' +
      '<div class="card-sub">바꾸면 즉시 다시 계산합니다. 현재 조건: 시드 ' + F.wonShort(req.initial_capital_krw) +
      " · 월 " + F.wonShort(req.monthly_contribution_krw) + " 적립 · " + req.horizon_years + "년</div>" +
      '<div class="ctrl" style="margin-top:14px">' +
      slider("monthly", "월 적립액", req.monthly_contribution_krw, 0, 10000000, 100000, F.wonShort) +
      slider("horizon", "투자 기간", req.horizon_years, 1, 40, 1, function (v) { return v + "년"; }) +
      slider("etf", "ETF 비중", req.etf_ratio, 0, 1, 0.05, function (v) { return F.pct(v, 0); }) +
      slider("us", "미국 비중", req.us_ratio, 0, 1, 0.05, function (v) { return F.pct(v, 0); }) +
      slider("bt", "백테스트 기간", req.backtest_years, 3, maxBacktestYears(), 1,
             function (v) { return v + "년"; }) +
      "</div>" +
      '<div class="ctrl-chips">' +
      '<div class="chips seg">' + RISKS.map(function (r) {
        return '<button class="chip' + (req.risk_preference === r[0] ? " on" : "") + '" data-set="risk" data-value="' + r[0] + '">' + esc(r[1]) + "</button>";
      }).join("") + "</div>" +
      '<div class="chips seg">' +
      '<button class="chip' + (req.drip ? " on" : "") + '" data-set="drip" data-value="' + (req.drip ? "0" : "1") + '">배당 재투자</button>' +
      '<button class="chip' + (req.after_tax ? " on" : "") + '" data-set="tax" data-value="' + (req.after_tax ? "0" : "1") + '">세후 기준</button>' +
      "</div></div></section>";

    var projCard =
      '<section class="card"><div class="card-title">' + req.horizon_years + '년 전망 · 월 배당금</div>' +
      '<div class="card-sub">시드 ' + F.wonShort(req.initial_capital_krw) + "으로 시작해 매월 " +
      F.wonShort(req.monthly_contribution_krw) + " 적립할 때의 전망입니다. 배당성장률 연 " +
      F.pct(a.div_growth, 2) + ", 주가상승률 연 " + F.pct(a.price_growth, 2) +
      (a.price_growth >= a.price_growth_cap - 1e-12
        ? " (상한 " + F.pct(a.price_growth_cap, 1) + " 적용)"
        : " (과거 " + F.pct(a.price_growth_raw, 2) + "에 " + a.haircut + "배 할인)") +
      "를 가정했습니다.</div>" +
      chart(function (w) {
        return C.plot([{ points: projPoints, color: "var(--brass)", fill: "color-mix(in srgb, var(--brass) 12%, transparent)", dash: "5 4" }], {
          width: w, height: 250, yFormat: F.won, xLabels: xLabels,
          hlines: [{ y: d.target, label: "목표 " + F.wonShort(d.target), color: "var(--rust)" }],
          markers: d.achieve_month && d.achieve_month <= req.horizon_years * 12
            ? [{ x: d.achieve_month, label: "목표 도달 " + d.achieve_text }] : [],
          yMin: 0
        });
      }, 250) +
      '<div class="legend"><span><i style="background:var(--brass)"></i>전망 월 배당 (보수적 가정 · 점선)</span>' +
      '<span><i style="background:var(--rust)"></i>목표</span></div></section>';

    var btCard =
      '<section class="card"><div class="card-title">전략의 과거 성적표</div>' +
      '<div class="card-sub">과거 ' + req.backtest_years + "년 동안 같은 비중으로 매월 " +
      F.wonShort(req.monthly_contribution_krw) + "씩 실제로 적립했다면 받았을 배당입니다. 실제 주가·배당 지급 이력과 " +
      "원달러 환율만 사용했고, 전망이 아니라 <b>실측 데이터</b>입니다.</div>" +
      '<div class="tiles" style="grid-template-columns:1fr 1fr;margin:14px 0 4px">' + [
        ["최근 12개월 월평균 배당", F.wonShort(bt.monthly_dividend_avg_12m), "기간 " + bt.period],
        ["총수익 CAGR", F.pct(bt.total_return_cagr, 1), "원금 " + F.wonShort(bt.contributed) + " → " + F.wonShort(bt.final_value)],
        ["투입 원금 대비 배당 (YoC)", F.pct(bt.yield_on_cost, 2), "현재가 대비 " + F.pct(bt.current_yield, 2)],
        ["최대 낙폭 (MDD)", F.pct(bt.mdd, 2), "이 정도 하락을 견뎌야 했습니다"]
      ].map(function (t) { return tile(t[0], t[1], t[2]); }).join("") + "</div>" +
      chart(function (w) {
        return C.plot([
          { points: contribPoints, color: "var(--ink-3)", dash: "3 3", width: 1.5 },
          { points: valuePoints, color: "var(--indigo)", fill: "color-mix(in srgb, var(--indigo) 14%, transparent)" }
        ], { width: w, height: 220, yFormat: F.won, xLabels: btLabels, yMin: 0 });
      }, 220) +
      '<div class="legend"><span><i style="background:var(--indigo)"></i>평가액</span>' +
      '<span><i style="background:var(--ink-3)"></i>투입 원금</span></div></section>';

    var ledgerCard =
      '<section class="card"><div class="card-title">배당 입금 달력</div>' +
      '<div class="card-sub">칸 하나가 그 달 통장에 실제로 들어온 배당입니다. 진할수록 많이 들어온 달 — ' +
      "미국 종목의 3·6·9·12월 분기 배당과 국내 종목의 결산 배당이 어디에 몰리는지 그대로 보입니다.</div>" +
      '<div style="margin-top:14px">' +
      C.ledger(btMonths.map(function (m) { return { date: m.date, value: m.dividend }; }),
        { note: "세" + (req.after_tax ? "후" : "전") + " 기준" }) + "</div>" +
      '<div style="margin-top:18px">' + chart(function (w) {
        return C.plot([{ points: divTtm, color: "var(--brass)", fill: "color-mix(in srgb, var(--brass) 12%, transparent)" }],
          { width: w, height: 170, yFormat: F.won, xLabels: btLabels, yMin: 0 });
      }, 170) + '<div class="legend"><span><i style="background:var(--brass)"></i>월평균 배당 (12개월 이동합계 ÷ 12)</span></div></div></section>';

    var rows = plan.portfolio.slice().sort(function (x, y) { return y.weight - x.weight; }).map(function (h) {
      var tier = tierMap[h.symbol];
      return '<tr data-symbol="' + esc(h.symbol) + '">' +
        '<td><span class="tier tier-' + tier.key + '" title="' + esc(tier.desc) + '">' + esc(tier.label) + "</span></td>" +
        '<td><div class="sym"><b>' + esc(h.name) + "</b>" +
        (h.div_cut_count_10y > 0 ? '<span class="badge warn mini">삭감 ' + h.div_cut_count_10y + "회</span>" : "") +
        "<small>" + esc(h.symbol) + " · " + (h.market === "US" ? "미국" : "국내") + " · " + esc(h.sector) +
        " · 연 " + h.payout_frequency + "회</small></div></td>" +
        '<td class="num"><b>' + F.pct(h.weight, 2) + '</b><span class="wbar-track"><span class="wbar" style="width:' +
        (h.weight / maxW * 100).toFixed(1) + '%"></span></span></td>' +
        '<td class="num">' + F.pct(h.ttm_yield, 2) + "</td>" +
        '<td class="num ' + ((h.div_cagr || 0) < 0 ? "neg" : "pos") + '">' + F.pct(h.div_cagr, 1) + "</td>" +
        '<td class="num">' + h.div_growth_streak + "년</td>" +
        '<td class="num"><b>' + F.wonShort(h.monthly_dividend_contribution) + "</b></td>" +
        '<td class="num">' + h.score.toFixed(0) + "</td></tr>";
    }).join("");

    var portCard =
      '<section class="card"><div class="card-title">추천 포트폴리오 ' + plan.portfolio.length + "종목</div>" +
      '<div class="card-sub">' + etfNote(plan) + " 그 안에서 비중은 " +
      "유니버스 36종목 기준 백분위 점수에 프로파일 가중치를 곱한 종합 점수의 제곱에 비례합니다. " +
      "행을 클릭하면 그 종목이 선택된 근거가 나옵니다.</div>" +
      '<div class="table-scroll" style="margin-top:12px"><table><thead><tr>' +
      "<th>역할</th><th>종목</th><th>비중</th><th>배당수익률</th><th>배당성장률</th>" +
      "<th>연속 증가</th><th>월 배당 기여</th><th>점수</th></tr></thead><tbody>" + rows + "</tbody></table></div>" +
      (plan.excluded.length ? '<div class="notice" style="margin-top:16px"><b>유니버스에서 제외된 종목 ' +
        plan.excluded.length + "개</b>" + plan.excluded.map(function (x) {
          return "<div>· " + esc(x.name) + " (" + esc(x.symbol) + ") — " + esc(x.reason) + "</div>";
        }).join("") + "</div>" : "") + "</section>";

    var sectorCard =
      '<section class="card"><div class="card-title">섹터 배분</div>' +
      '<div class="card-sub">한 섹터가 30%를 넘지 않도록 제약을 걸었습니다 (ETF는 자체 분산되어 제외).</div>' +
      '<div style="margin-top:14px">' + plan.sector_allocation.map(function (s) {
        return '<div class="factor"><div class="top"><span>' + esc(s.sector) + "</span><span>" +
          F.pct(s.weight, 2) + '</span></div><div class="track"><div class="fill" style="width:' +
          (s.weight / 0.30 * 100).toFixed(1) + '%"></div></div></div>';
      }).join("") + "</div></section>";

    var assumeCard =
      '<section class="card"><div class="card-title">계산에 쓴 가정</div>' +
      '<div class="card-sub">모든 전망은 아래 가정에 따른 추정이며, 가정이 바뀌면 결과도 바뀝니다. ' +
      '성장률은 과거 값 → 할인·상한을 적용한 값 순서로 적었습니다.</div>' +
      '<div class="grid g2" style="margin-top:12px">' +
      kvBlock([
        ["시작 배당수익률 (가중)", F.pct(a.start_yield, 2)],
        ["배당성장률", F.pct(a.div_growth_raw, 2) + " → " + F.pct(a.div_growth, 2)],
        ["주가상승률", F.pct(a.price_growth_raw, 2) + " → " + F.pct(a.price_growth, 2)],
        ["성장률 할인 계수", a.haircut + "배"],
        ["ETF 비중 목표 → 실제",
          F.pct(plan.weight_profile.etf_ratio, 0) + " → " + F.pct(plan.weight_profile.etf_weight, 1)]
      ]) +
      kvBlock([
        ["주가상승률 상한", F.pct(a.price_growth_cap, 1)],
        ["배당세 (미국 / 국내)", F.pct(a.tax_us, 0) + " / " + F.pct(a.tax_kr, 1)],
        ["매수 수수료", F.pct(a.buy_fee, 2)],
        ["원달러 환율", a.fx_rate
          ? Math.round(a.fx_rate).toLocaleString("ko-KR") + "원 (" + sourceLabel(a.fx_source) + ")"
          : "지수만 보유 — 상대 변화만 반영"],
        ["리밸런싱", '<span title="' + esc(a.rebalance) + '">연 1회 · 1월</span>']
      ]) + "</div></section>";

    mount('<div class="wrap rise">' +
      '<div class="row" style="margin-bottom:14px"><a class="btn ghost" href="' + url("") + '" data-route="" data-link>← 조건 다시 입력</a>' +
      '<span class="eyebrow">데이터 기준월 ' + esc(plan.as_of) + (plan.cached ? " · 캐시" : "") + "</span></div>" +
      verdict + tiles + controls +
      '<div class="split">' + projCard + btCard + "</div>" +
      ledgerCard + portCard +
      aiPanel("ai-strategy", "이 전략을 왜 이렇게 짰는가",
        "위 표의 계산된 수치만 근거로 서술합니다. 숫자를 새로 만들지 않으며, 같은 조건이면 저장된 분석을 즉시 보여줍니다.") +
      '<div class="split">' + sectorCard + assumeCard + "</div>" +
      '<div class="disclaimer"><b>면책 고지</b> — 본 서비스는 공개 시장 데이터를 이용한 <b>시뮬레이션 도구</b>이며, ' +
      "투자 권유나 자문이 아닙니다. 과거 성과는 미래 수익을 보장하지 않습니다. 전망치는 과거 배당성장률과 " +
      "주가상승률에 보수적 할인을 적용한 <b>가정에 기반한 추정</b>으로 읽으세요. 세금은 배당 원천징수율만 단순 " +
      "적용했습니다. 금융소득종합과세·건강보험료·거래세는 반영하지 않았으므로 실제 수령액은 더 적을 수 있습니다. " +
      "종목 추천은 사전에 정의된 36개 유니버스 안에서 공개 지표를 기계적으로 점수화한 결과이며, 기업의 사업 " +
      "내용이나 최근 공시를 반영하지 않습니다.</div></div>");
    runAi("ai-strategy", "api/ai/strategy", plan);
  }

  function etfNote(plan) {
    var wp = plan.weight_profile, req = plan.request;
    if (!wp.etf_count) {
      return "ETF 비중 " + F.pct(req.etf_ratio, 0) + "로 설정했지만 이 조건에서는 담기지 않았습니다 — " +
        "유니버스의 ETF는 모두 미국 상장이라 미국 비중 " + F.pct(req.us_ratio, 0) +
        "이 ETF 비중의 상한입니다.";
    }
    var note = "ETF " + wp.etf_count + "종목이 비중 " + F.pct(wp.etf_weight, 1) +
      "로 코어를 맡고, 나머지를 개별주가 채웁니다.";
    if (wp.etf_ratio != null && wp.etf_ratio_effective != null &&
        wp.etf_ratio - wp.etf_ratio_effective > 1e-9) {
      note += " 설정한 ETF 비중 " + F.pct(wp.etf_ratio, 0) + "는 미국 비중 " +
        F.pct(req.us_ratio, 0) + "까지만 적용됩니다 (ETF는 모두 미국 상장).";
    }
    return note;
  }
  function slider(key, label, value, min, max, step, fmt) {
    return '<div class="sl"><label><span>' + esc(label) + "</span><b>" + esc(fmt(value)) + "</b></label>" +
      '<input type="range" min="' + min + '" max="' + max + '" step="' + step + '" value="' + value +
      '" data-slider="' + key + '"></div>';
  }
  function kvBlock(pairs) {
    return "<div>" + pairs.map(function (p) {
      return '<div class="factor"><div class="top"><span>' + esc(p[0]) + "</span><span>" + p[1] + "</span></div></div>";
    }).join("") + "</div>";
  }

  app.addEventListener("change", function (e) {
    var sl = e.target.getAttribute && e.target.getAttribute("data-slider");
    if (sl) return setParam(pairFor(sl, e.target.value));
    var set = e.target.closest && e.target.closest("[data-set]");
    if (set) return setParam(pairFor(set.getAttribute("data-set"), set.getAttribute("data-value")));
  });
  app.addEventListener("click", function (e) {
    var set = e.target.closest("[data-set]");
    if (set) {
      var patch = {};
      patch[set.getAttribute("data-set")] = set.getAttribute("data-value");
      return setParam(patch);
    }
    var row = e.target.closest("tr[data-symbol]");
    if (row) {
      var q = new URLSearchParams(location.search);
      q.set("symbol", row.getAttribute("data-symbol"));
      return go("ticker?" + q.toString());
    }
  });
  function pairFor(key, value) {
    var patch = {};
    patch[key] = value;
    return patch;
  }

  /* ------------------------------------------------------------ 종목 화면 */
  function renderTicker() {
    var params = new URLSearchParams(location.search);
    var symbol = params.get("symbol") || "";
    app.innerHTML = '<div class="center"><div class="spin"></div><div>종목 근거를 불러오는 중…</div></div>';
    Promise.all([post("api/plan", planQuery(params)), get("api/tickers/" + encodeURIComponent(symbol))])
      .then(function (r) {
        lastPlan = r[0];
        paintTicker(symbol, r[0], r[1]);
      }).catch(function (err) {
        app.innerHTML = '<div class="wrap narrow"><div class="notice">' + esc(err.message) +
          '</div><p><a class="btn ghost" href="' + url("") + '" data-route="" data-link>처음으로</a></p></div>';
      });
  }

  function paintTicker(symbol, plan, detail) {
    resetCharts();
    var m = detail.metrics;
    var holding = plan.portfolio.filter(function (h) { return h.symbol === symbol; })[0];
    var tier = holding ? tiers(plan.portfolio)[symbol] : null;
    var cur = m.currency;
    var thisYear = Number(plan.as_of.slice(0, 4));
    var annual = detail.dividend_annual.filter(function (x) { return x.dividend > 0; });
    // 진행 중인 올해는 아직 지급이 남아 성장률을 왜곡한다 — 완결된 연도만으로 계산한다
    var complete = annual.filter(function (x) { return x.year < thisYear; });
    var recent = complete.slice(-11);
    var growth = recent.length >= 2 && recent[0].dividend > 0
      ? Math.pow(recent[recent.length - 1].dividend / recent[0].dividend, 1 / (recent.length - 1)) - 1 : null;
    var priceLabels = detail.price_series.map(function (p, i) { return { i: i, p: p }; })
      .filter(function (x) { return x.p.date.slice(5) === "01" && Number(x.p.date.slice(0, 4)) % 3 === 0; })
      .map(function (x) { return { x: x.i, label: x.p.date.slice(0, 4) }; });

    var fx = (plan.assumptions || {}).fx_rate;
    var krwNote = (cur === "USD" && fx)
      ? "≈ " + Math.round(m.price * fx).toLocaleString("ko-KR") + "원 (환율 " +
        Math.round(fx).toLocaleString("ko-KR") + "원)"
      : m.first_date + " 상장";
    var stats = '<div class="tiles">' + [
      ["현재가", F.money(m.price, cur), krwNote],
      ["현재 배당수익률", F.pct(m.ttm_yield, 2), "주당 연 " + F.money(m.ttm_dividend, cur, 4)],
      ["5년 배당성장률 (연평균)", F.pct(m.div_cagr_5y, 1), m.div_cut_count_10y ? "10년간 배당 삭감 " + m.div_cut_count_10y + "회" : "10년간 배당 삭감 없음"],
      ["변동성 / 최대낙폭", F.pct(m.volatility, 1) + " / " + F.pct(m.mdd, 1),
        "데이터 " + (m.data_years || m.history_years) + "년 · 가격 " + sourceLabel(m.price_source) +
        " · 배당 " + sourceLabel(m.dividend_source)]
    ].map(function (t) { return tile(t[0], t[1], t[2]); }).join("") + "</div>";

    var factorCard = '<section class="card"><div class="card-title">추천 근거: 팩터 점수 분해</div>' +
      '<div class="card-sub">유니버스 36종목 내 백분위 점수에 프로파일 가중치를 곱한 값이 그대로 이 종목의 비중을 ' +
      "결정했습니다. 화면의 숫자와 계산에 쓰인 숫자가 같습니다.</div>";
    if (holding) {
      var keys = ["yield", "growth", "stability", "quality", "smooth"];
      factorCard += '<div style="margin-top:14px">' + keys.map(function (k) {
        var f = holding.factors[k];
        return '<div class="factor"><div class="top"><span>' + esc(f.label) + "</span><span>" +
          f.score.toFixed(1) + "점 × 가중치 " + F.pct(f.weight, 1) + " = 기여 " + f.contribution.toFixed(1) +
          '</span></div><div class="track"><div class="fill" style="width:' + f.score.toFixed(1) + '%"></div>' +
          '<div class="fill contrib" style="width:' + (f.contribution).toFixed(1) + '%;opacity:.9"></div></div></div>';
      }).join("") +
        '<div class="factor"><div class="top"><span><b>종합 점수</b></span><span><b>' + holding.score.toFixed(1) +
        "점</b> · 비중 " + F.pct(holding.weight, 2) + "</span></div></div>" +
        '<div class="legend"><span><i style="background:var(--indigo)"></i>백분위 점수</span>' +
        '<span><i style="background:var(--brass)"></i>가중 기여</span></div></div>';
    } else {
      factorCard += '<div class="notice" style="margin-top:14px">이 종목은 현재 시나리오의 추천 포트폴리오에 포함되지 않았습니다.</div>';
    }
    factorCard += "</section>";

    var divCard = '<section class="card"><div class="card-title">연도별 주당 배당금</div>' +
      '<div class="card-sub">' + (growth != null
        ? "완결된 최근 " + recent.length + "년간 연평균 " + (growth >= 0 ? "증가" : "감소") + " " +
          F.pct(Math.abs(growth), 1) + " — 막대가 계단처럼 오르는 종목이 배당성장주입니다. " +
          thisYear + "년 막대는 아직 지급이 진행 중입니다."
        : "연간 배당 이력") + "</div>" +
      chart(function (w) {
        return C.bars(annual.map(function (x) { return { label: String(x.year).slice(2), value: x.dividend }; }), {
          width: w, height: 190, yFormat: function (v) { return F.money(v, cur, cur === "USD" ? 2 : 0); },
          tip: function (v) { return F.money(v, cur, 4); }
        });
      }, 190) + "</section>";

    var priceCard = '<section class="card"><div class="card-title">월말 주가 추이</div>' +
      '<div class="card-sub">배당은 주가가 유지될 때만 의미가 있습니다 — 배당수익률이 높은 이유가 주가 하락은 아닌지 확인하세요.</div>' +
      chart(function (w) {
        return C.plot([{
          points: detail.price_series.map(function (p, i) { return [i, p.close]; }),
          color: "var(--indigo)", fill: "color-mix(in srgb, var(--indigo) 12%, transparent)"
        }], {
          width: w, height: 190, xLabels: priceLabels,
          yFormat: function (v) { return F.money(v, cur, 0); }
        });
      }, 190) + "</section>";

    var html = '<div class="wrap rise">' +
      '<div class="row" style="margin-bottom:14px"><a class="btn ghost" href="' + url("plan" + planSearch()) + '" data-route="plan' +
      planSearch() + '" data-link>← 계획으로 돌아가기</a>' +
      (tier ? '<span class="badge">' + esc(tier.label) + " · " + esc(tier.desc) + "</span>" : "") + "</div>" +
      "<h1 style=\"font-size:26px;letter-spacing:-.03em\">" + esc(m.name) + "</h1>" +
      '<p class="card-sub" style="margin:4px 0 18px">' + esc(m.symbol) + " · " + (m.market === "US" ? "미국" : "국내") +
      " · " + esc(m.sector) + " · " + (m.kind === "etf" ? "ETF" : "개별주") + " · 연 " + m.payout_frequency + "회 지급</p>" +
      stats + '<div class="split">' + factorCard + divCard + "</div>" + priceCard +
      (holding ? aiPanel("ai-ticker", esc(m.name) + " 선택 근거",
        "위 팩터 점수와 배당 지표만 근거로 서술합니다. 숫자를 새로 만들지 않습니다.") : "") +
      "</div>";
    mount(html);
    if (holding) {
      window._tickerUrl = "api/ai/tickers/" + encodeURIComponent(symbol);
      window._tickerBody = { holding: holding, tier: tier ? tier.key : null };
      runAi("ai-ticker", window._tickerUrl, window._tickerBody);
    }
  }

  /* ------------------------------------------------------------------ 진입 */
  function render() {
    var route = currentRoute();
    if (route === "plan") return renderPlan();
    if (route === "ticker") return renderTicker();
    if (route) history.replaceState({}, "", url(""));
    wiz.step = wiz.goal ? wiz.step : 0;
    return renderHome();
  }

  /* --------------------------------------------------- 데이터 신선도와 갱신 */
  var SOURCE_LABEL = { yahoo: "야후", nasdaq: "나스닥", naver: "네이버", frankfurter: "ECB",
    alphavantage: "알파밴티지", bundle: "번들", cache: "캐시" };
  function sourceLabel(key) {
    // "nasdaq+cache" 처럼 두 출처를 이어 붙인 값도 그대로 읽히게 쪼갠다
    if (!key) return "-";
    return String(key).split("+").map(function (part) {
      return SOURCE_LABEL[part] || part;
    }).join("+");
  }

  function paintFreshness() {
    var el = document.getElementById("asof");
    if (!el || !health) return;
    var cls = health.refreshing ? "busy" : (health.stale ? "stale" : "");
    var prov = health.provenance || {};
    var prices = Object.keys(prov.prices || {}).map(function (k) {
      return sourceLabel(k) + " " + prov.prices[k];
    }).join(" · ");
    var title = ["가격 " + prices,
      "배당 " + Object.keys(prov.dividends || {}).map(function (k) {
        return sourceLabel(k) + " " + prov.dividends[k];
      }).join(" · "),
      "환율 " + sourceLabel(prov.fx) + (prov.fx_rate ? " " + prov.fx_rate.toFixed(2) + "원" : " (지수)"),
      health.fetched_at ? "받은 시각 " + health.fetched_at : "번들 스냅샷"].join("\n");
    el.innerHTML = '<span class="dot ' + cls + '"></span>' +
      "데이터 " + esc(health.as_of) + (health.refreshing ? " · 갱신 중" : (health.stale ? " · 갱신 필요" : "")) +
      " · 설명 " + (health.narrative_engine === "claude" ? "Claude" : "로컬");
    el.title = title;
  }
  function loadHealth() {
    return get("api/health").then(function (h) {
      health = h;
      paintFreshness();
      return h;
    }).catch(function () { return null; });
  }

  function refreshPanel() {
    var el = document.getElementById("refresh-panel");
    if (el) return el;
    el = document.createElement("aside");
    el.id = "refresh-panel";
    el.className = "refresh-panel";
    el.innerHTML = '<header><b class="card-title">시장 데이터 갱신</b>' +
      '<button class="close" data-close-refresh aria-label="닫기" title="닫기">&times;</button></header>' +
      '<div class="refresh-bar"><i style="width:0"></i></div>' +
      '<div class="refresh-log"></div>';
    document.body.appendChild(el);
    return el;
  }
  var refreshing = false;
  function startRefresh() {
    if (refreshing) return;
    refreshing = true;
    var panel = refreshPanel();
    var bar = panel.querySelector(".refresh-bar i");
    var log = panel.querySelector(".refresh-log");
    log.innerHTML = "요청 전송 중…";
    var rows = [], rowIndex = {};      // 종목별로 한 줄만 쓰고 그 줄을 갱신한다
    function put(key, html) {
      if (key != null && rowIndex[key] != null) rows[rowIndex[key]] = html;
      else {
        if (key != null) rowIndex[key] = rows.length;
        rows.push(html);
      }
      log.innerHTML = rows.slice(-90).join("<br>");
      log.scrollTop = log.scrollHeight;
    }
    fetch(url("api/refresh"), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}"
    }).then(function (res) {
      var reader = res.body.getReader(), dec = new TextDecoder(), buf = "";
      function pump() {
        return reader.read().then(function (r) {
          if (r.done) return;
          buf += dec.decode(r.value, { stream: true });
          var parts = buf.split("\n\n");
          buf = parts.pop() || "";
          parts.forEach(function (raw) {
            var ev = /^event: (.+)$/m.exec(raw), da = /^data: (.+)$/m.exec(raw);
            if (!ev || !da) return;
            var d = JSON.parse(da[1]);
            if (ev[1] === "progress") {
              bar.style.width = (d.step / d.total * 100).toFixed(1) + "%";
              var failed = d.note && d.note.indexOf("실패") >= 0;
              put(d.symbol, "<b>" + esc(d.symbol) + "</b> " +
                (d.source ? sourceLabel(d.source) : "받는 중…") +
                (d.months ? " " + d.months + "개월" : "") +
                (d.note ? (failed ? ' <span class="warn">' : " <span>") + esc(d.note) + "</span>" : ""));
            } else if (ev[1] === "final") {
              bar.style.width = "100%";
              var rep = d.report;
              put(null, '<b>완료</b> 기준월 ' + esc(rep.as_of) + " · 종목 " + rep.ticker_count + "개" +
                (rep.failed.length ? ' · <span class="warn">실패 ' + rep.failed.length + "개</span>" : ""));
              (rep.warnings || []).slice(0, 4).forEach(function (w) {
                put(null, '<span class="warn">· ' + esc(w) + "</span>");
              });
            } else if (ev[1] === "error") {
              put(null, '<span class="warn">갱신 실패 — ' + esc(d.message) + "</span>");
            }
          });
          return pump();
        });
      }
      return pump();
    }).catch(function (err) {
      put(null, '<span class="warn">갱신 실패 — ' + esc(err.message) + "</span>");
    }).then(function () {
      refreshing = false;
      loadHealth().then(function () {
        if (currentRoute()) render();                // 새 데이터로 다시 계산
      });
    });
  }
  document.getElementById("refresh").onclick = startRefresh;
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-close-refresh]")) {
      var panel = document.getElementById("refresh-panel");
      if (panel) panel.remove();
    }
  });

  loadHealth();
  render();
})();
