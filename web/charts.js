/* 손으로 그린 SVG 차트. 컨테이너 실측 너비로 1:1 좌표를 써서 글자가 늘어나지 않는다. */
(function (global) {
  "use strict";

  var F = {
    won: function (v) {
      if (v == null || isNaN(v)) return "-";
      var a = Math.abs(v);
      if (a >= 1e8) { var n = v / 1e8; return (Math.abs(n) >= 100 ? n.toFixed(0) : n.toFixed(1)) + "억"; }
      if (a >= 1e4) return Math.round(v / 1e4).toLocaleString("ko-KR") + "만";
      return Math.round(v).toLocaleString("ko-KR");
    },
    wonFull: function (v) {
      if (v == null || isNaN(v)) return "-";
      return Math.round(v).toLocaleString("ko-KR") + "원";
    },
    wonShort: function (v) {
      if (v == null || isNaN(v)) return "-";
      var a = Math.abs(v);
      if (a >= 1e8) { var n = v / 1e8; return (Math.abs(n) >= 100 ? n.toFixed(0) : n.toFixed(1)) + "억원"; }
      if (a >= 1e4) return Math.round(v / 1e4).toLocaleString("ko-KR") + "만원";
      return F.wonFull(v);
    },
    pct: function (v, d) {
      if (v == null || isNaN(v)) return "-";
      return (v * 100).toFixed(d == null ? 1 : d) + "%";
    },
    money: function (v, currency, d) {
      if (v == null || isNaN(v)) return "-";
      if (currency === "USD") return "$" + v.toLocaleString("en-US", { maximumFractionDigits: d == null ? 2 : d });
      return Math.round(v).toLocaleString("ko-KR") + "원";
    }
  };

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function niceTicks(min, max, count) {
    if (max === min) { max = min + 1; }
    var span = max - min, step = Math.pow(10, Math.floor(Math.log(span / count) / Math.LN10));
    var err = span / count / step;
    if (err >= 7.5) step *= 10; else if (err >= 3) step *= 5; else if (err >= 1.5) step *= 2;
    var out = [], v = Math.ceil(min / step) * step;
    for (; v <= max + step * 0.001; v += step) out.push(v);
    return out;
  }

  /* series: [{points:[[x,y]...], color, fill, dash, width}]
     opts: {width, height, yFormat, xLabels:[{x,label}], hlines, markers, yMin} */
  function plot(series, opts) {
    var w = opts.width, h = opts.height || 220;
    var padL = opts.padL == null ? 52 : opts.padL, padR = 14, padT = 12, padB = 26;
    var xs = [], ys = [];
    series.forEach(function (s) {
      s.points.forEach(function (p) { xs.push(p[0]); ys.push(p[1]); });
    });
    (opts.hlines || []).forEach(function (l) { ys.push(l.y); });
    var xMin = Math.min.apply(null, xs), xMax = Math.max.apply(null, xs);
    var yMin = opts.yMin != null ? opts.yMin : Math.min.apply(null, ys);
    var yMax = Math.max.apply(null, ys);
    if (yMax === yMin) yMax = yMin + 1;
    yMax += (yMax - yMin) * 0.08;
    var X = function (v) { return padL + (v - xMin) / (xMax - xMin || 1) * (w - padL - padR); };
    var Y = function (v) { return h - padB - (v - yMin) / (yMax - yMin) * (h - padT - padB); };

    var out = ['<svg class="chart" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '" role="img">'];
    niceTicks(yMin, yMax, 4).forEach(function (t) {
      out.push('<line class="grid-line" x1="' + padL + '" y1="' + Y(t).toFixed(1) + '" x2="' + (w - padR) + '" y2="' + Y(t).toFixed(1) + '"/>');
      out.push('<text x="' + (padL - 7) + '" y="' + (Y(t) + 3).toFixed(1) + '" text-anchor="end">' + esc(opts.yFormat ? opts.yFormat(t) : t) + '</text>');
    });
    (opts.xLabels || []).forEach(function (t) {
      out.push('<text x="' + X(t.x).toFixed(1) + '" y="' + (h - 8) + '" text-anchor="middle">' + esc(t.label) + '</text>');
    });
    (opts.hlines || []).forEach(function (l) {
      out.push('<line x1="' + padL + '" y1="' + Y(l.y).toFixed(1) + '" x2="' + (w - padR) + '" y2="' + Y(l.y).toFixed(1) +
        '" stroke="' + (l.color || "var(--rust)") + '" stroke-width="1" stroke-dasharray="4 4"/>');
      if (l.label) out.push('<text x="' + (w - padR) + '" y="' + (Y(l.y) - 6).toFixed(1) + '" text-anchor="end" fill="' + (l.color || "var(--rust)") + '">' + esc(l.label) + '</text>');
    });
    (opts.markers || []).forEach(function (m) {
      out.push('<line x1="' + X(m.x).toFixed(1) + '" y1="' + padT + '" x2="' + X(m.x).toFixed(1) + '" y2="' + (h - padB) + '" stroke="var(--brass)" stroke-width="1" stroke-dasharray="2 3"/>');
      if (m.label) out.push('<text x="' + (X(m.x) + 5).toFixed(1) + '" y="' + (padT + 10) + '" fill="var(--brass)">' + esc(m.label) + '</text>');
    });
    series.forEach(function (s) {
      var d = s.points.map(function (p, i) {
        return (i ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1);
      }).join(" ");
      if (s.fill) {
        out.push('<path d="' + d + ' L' + X(s.points[s.points.length - 1][0]).toFixed(1) + ' ' + Y(yMin).toFixed(1) +
          ' L' + X(s.points[0][0]).toFixed(1) + ' ' + Y(yMin).toFixed(1) + ' Z" fill="' + s.fill + '" stroke="none"/>');
      }
      var len = Math.round((w - padL - padR) * 1.6);
      out.push('<path class="draw" style="--len:' + len + '" d="' + d + '" fill="none" stroke="' + (s.color || "var(--ink)") +
        '" stroke-width="' + (s.width || 2) + '" stroke-linejoin="round" stroke-linecap="round"' +
        (s.dash ? ' stroke-dasharray="' + s.dash + '"' : "") + "/>");
    });
    out.push('<line class="axis" x1="' + padL + '" y1="' + (h - padB) + '" x2="' + (w - padR) + '" y2="' + (h - padB) + '"/>');
    out.push("</svg>");
    return out.join("");
  }

  /* 세로 막대 — 연도별 주당 배당금처럼 계단이 보여야 하는 데이터에 쓴다. */
  function bars(items, opts) {
    var w = opts.width, h = opts.height || 180, padL = opts.padL == null ? 46 : opts.padL,
      padR = 10, padT = 10, padB = 24;
    var vals = items.map(function (i) { return i.value; });
    var yMax = Math.max.apply(null, vals) * 1.1 || 1;
    var innerW = w - padL - padR, bw = innerW / items.length;
    var out = ['<svg class="chart" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '" role="img">'];
    niceTicks(0, yMax, 3).forEach(function (t) {
      var y = h - padB - t / yMax * (h - padT - padB);
      out.push('<line class="grid-line" x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (w - padR) + '" y2="' + y.toFixed(1) + '"/>');
      out.push('<text x="' + (padL - 7) + '" y="' + (y + 3).toFixed(1) + '" text-anchor="end">' + esc(opts.yFormat ? opts.yFormat(t) : t) + "</text>");
    });
    items.forEach(function (it, i) {
      var bh = Math.max(1, it.value / yMax * (h - padT - padB));
      var x = padL + i * bw + bw * 0.16;
      out.push('<rect x="' + x.toFixed(1) + '" y="' + (h - padB - bh).toFixed(1) + '" width="' + (bw * 0.68).toFixed(1) +
        '" height="' + bh.toFixed(1) + '" rx="1.5" fill="' + (it.color || opts.color || "var(--brass)") + '"><title>' +
        esc(it.label + " · " + (opts.tip ? opts.tip(it.value) : it.value)) + "</title></rect>");
      if (items.length <= 20 || i % Math.ceil(items.length / 14) === 0) {
        out.push('<text x="' + (padL + i * bw + bw / 2).toFixed(1) + '" y="' + (h - 8) + '" text-anchor="middle">' + esc(it.label) + "</text>");
      }
    });
    out.push('<line class="axis" x1="' + padL + '" y1="' + (h - padB) + '" x2="' + (w - padR) + '" y2="' + (h - padB) + '"/>');
    out.push("</svg>");
    return out.join("");
  }

  /* 달성률 다이얼 — 목표 대비 전망 배당. 1.0을 한 바퀴로 본다. */
  function dial(ratio) {
    var r = 44, c = 2 * Math.PI * r, frac = Math.max(0, Math.min(1, ratio || 0));
    var color = frac >= 1 ? "var(--sage)" : "var(--brass)";
    return '<svg class="dial" viewBox="0 0 104 104" role="img" aria-label="달성률 ' + Math.round(frac * 100) + '%">' +
      '<circle cx="52" cy="52" r="' + r + '" fill="none" stroke="var(--rule-soft)" stroke-width="8"/>' +
      '<circle cx="52" cy="52" r="' + r + '" fill="none" stroke="' + color + '" stroke-width="8" stroke-linecap="round"' +
      ' stroke-dasharray="' + (c * frac).toFixed(1) + " " + c.toFixed(1) + '" transform="rotate(-90 52 52)"/>' +
      '<text x="52" y="49" text-anchor="middle" style="font-size:17px;font-weight:600;fill:var(--ink)">' +
      Math.round((ratio || 0) * 100) + "%</text>" +
      '<text x="52" y="64" text-anchor="middle" style="font-size:9px">목표 대비</text></svg>';
  }

  /* 시그니처: 배당 입금 달력. 연 x 월 격자에 그 달 실제 입금액을 잉크 농도로 찍는다. */
  function ledger(months, opts) {
    if (!months.length) return "";
    var max = Math.max.apply(null, months.map(function (m) { return m.value; })) || 1;
    var byYear = {};
    months.forEach(function (m) {
      var y = m.date.slice(0, 4), mo = parseInt(m.date.slice(5, 7), 10);
      (byYear[y] = byYear[y] || {})[mo] = m.value;
    });
    var head = ['<div class="ledger-head"><span></span>'];
    for (var i = 1; i <= 12; i++) head.push("<span>" + i + "</span>");
    head.push("</div>");
    var rows = Object.keys(byYear).sort().map(function (y) {
      var cells = ['<div class="ledger-row"><span class="ledger-y">' + y + "</span>"];
      for (var m = 1; m <= 12; m++) {
        var v = byYear[y][m];
        if (v == null) { cells.push('<span class="ledger-cell"></span>'); continue; }
        var a = 0.1 + 0.9 * Math.pow(v / max, 0.6);
        cells.push('<span class="ledger-cell" style="background:color-mix(in srgb, var(--brass) ' +
          (a * 100).toFixed(0) + '%, var(--rule-soft))" title="' + y + "-" +
          (m < 10 ? "0" + m : m) + " · " + F.wonFull(v) + '"></span>');
      }
      cells.push("</div>");
      return cells.join("");
    });
    return head.join("") + '<div class="ledger">' + rows.join("") + "</div>" +
      '<div class="ledger-legend">적음<i style="background:color-mix(in srgb, var(--brass) 12%, var(--rule-soft))"></i>' +
      '<i style="background:color-mix(in srgb, var(--brass) 45%, var(--rule-soft))"></i>' +
      '<i style="background:var(--brass)"></i>많음 · 최대 ' + F.wonFull(max) +
      (opts && opts.note ? " · " + esc(opts.note) : "") + "</div>";
  }

  global.C = { plot: plot, bars: bars, dial: dial, ledger: ledger, F: F, esc: esc };
})(window);
