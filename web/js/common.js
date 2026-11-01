/* Shared helpers for every page. No build step: plain browser JS. */
'use strict';

const BBW = (() => {
  const KMH = 3.6;
  const UNITS = {
    kmh: { label: 'km/h', factor: 3.6, digits: 0, spoken: 'kilometres per hour' },
    kt: { label: 'kt', factor: 1.943844, digits: 0, spoken: 'knots' },
    ms: { label: 'm/s', factor: 1, digits: 1, spoken: 'metres per second' },
  };
  const STALE_S = 10 * 60;   // values older than this are greyed out
  const TZ_DEFAULT = 'Asia/Kolkata';

  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      localStorage.setItem(key, value);
    } catch (e) { /* private mode etc. */ }
    return value;
  }

  function unit() {
    const u = store('bbw.unit');
    return UNITS[u] ? u : 'kmh';
  }
  function setUnit(u) { store('bbw.unit', u); }

  /* m/s -> number in the chosen unit (null-safe) */
  function speed(ms, u = unit()) {
    if (ms === null || ms === undefined) return null;
    return ms * UNITS[u].factor;
  }
  function fmtSpeed(ms, u = unit()) {
    const v = speed(ms, u);
    return v === null ? '–' : v.toFixed(UNITS[u].digits);
  }

  function fmt(v, digits = 0) {
    return v === null || v === undefined || Number.isNaN(v) ? '–' : Number(v).toFixed(digits);
  }
  function signed(v, digits = 1) {
    if (v === null || v === undefined) return '–';
    const s = Number(v).toFixed(digits);
    return v > 0 ? '+' + s : s;
  }

  function ageSeconds(ts, now = Date.now() / 1000) {
    return ts ? Math.max(0, now - ts) : Infinity;
  }
  function ageText(ts, now) {
    const s = ageSeconds(ts, now);
    if (!Number.isFinite(s)) return 'no data';
    if (s < 60) return `${Math.round(s)} s ago`;
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400 * 2) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} days ago`;
  }
  function isStale(ts, now, limit = STALE_S) { return ageSeconds(ts, now) > limit; }

  function timeText(ts, tz = TZ_DEFAULT) {
    if (!ts) return '–';
    return new Date(ts * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: tz });
  }

  const POINTS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  const SPOKEN = { N: 'north', E: 'east', S: 'south', W: 'west' };
  function compassPoint(deg) {
    if (deg === null || deg === undefined) return null;
    return POINTS[Math.round(((deg % 360) / 22.5)) % 16];
  }
  function spokenPoint(deg) {
    const p = compassPoint(deg);
    if (!p) return null;
    // "SW" -> "south west", "WSW" -> "west south west"
    return p.split('').map((c) => SPOKEN[c]).join(' ');
  }

  /* Wind band from the gust (or the average if no gust), using station limits in km/h. */
  const BAND_INFO = {
    'light': { word: 'Light', symbol: '○' },
    'moderate': { word: 'Moderate', symbol: '◐' },
    'strong': { word: 'Strong', symbol: '▲' },
    'too-strong': { word: 'Too strong', symbol: '■' },
  };
  function band(avgMs, gustMs, bands) {
    const ms = gustMs ?? avgMs;
    if (ms === null || ms === undefined || !bands) return null;
    const kmh = ms * KMH;
    if (kmh < bands.light) return 'light';
    if (kmh < bands.moderate) return 'moderate';
    if (kmh < bands.strong) return 'strong';
    return 'too-strong';
  }
  function bandHtml(key) {
    if (!key) return '';
    const b = BAND_INFO[key];
    return `<span class="band ${key}"><span aria-hidden="true">${b.symbol}</span>${b.word}</span>`;
  }
  const TREND_ICON = { rising: '↑', falling: '↓', steady: '→' };
  function trendText(trend) {
    if (!trend) return '';
    return `${TREND_ICON[trend.trend]} ${trend.trend[0].toUpperCase()}${trend.trend.slice(1)}`;
  }

  /* Compass SVG. Arrow points the way the wind blows (downwind), drawn from
     the side it comes from. Optional green sector = good launch directions. */
  function compassSvg(dirDeg, launch, { small = false, nodirText = 'no direction' } = {}) {
    const c = 50, r = 44;
    const pt = (deg, rad) => {
      const a = (deg - 90) * Math.PI / 180;
      return [c + rad * Math.cos(a), c + rad * Math.sin(a)];
    };
    let s = `<svg class="compass${small ? ' small' : ''}" viewBox="0 0 100 100" role="img" aria-label="${
      dirDeg == null ? 'Wind direction unavailable' : 'Wind from ' + compassPoint(dirDeg) + ' ' + Math.round(dirDeg) + ' degrees'}">`;
    if (launch) {
      const span = (launch.to - launch.from + 360) % 360;
      const [x1, y1] = pt(launch.from, r);
      const [x2, y2] = pt(launch.to, r);
      s += `<path class="sector" d="M${c},${c} L${x1.toFixed(1)},${y1.toFixed(1)} A${r},${r} 0 ${span > 180 ? 1 : 0} 1 ${x2.toFixed(1)},${y2.toFixed(1)} Z"/>`;
    }
    s += `<circle class="rim" cx="${c}" cy="${c}" r="${r}"/>`;
    for (let d = 0; d < 360; d += 45) {
      const [a1, b1] = pt(d, r - 5), [a2, b2] = pt(d, r);
      s += `<line class="tick" x1="${a1}" y1="${b1}" x2="${a2}" y2="${b2}"/>`;
    }
    if (!small) {
      for (const [d, l] of [[0, 'N'], [90, 'E'], [180, 'S'], [270, 'W']]) {
        const [x, y] = pt(d, r - 13);
        s += `<text class="cardinal" x="${x}" y="${y}">${l}</text>`;
      }
    }
    if (dirDeg == null) {
      if (!small) s += `<text class="nodir" x="${c}" y="${c + 4}">${nodirText}</text>`;
    } else {
      // arrow: tail at the upwind edge, head toward the downwind side
      s += `<g transform="rotate(${dirDeg} ${c} ${c})"><path class="arrow" d="M${c - 4},${c - 30} L${c + 4},${c - 30} L${c + 4},${c + 14} L${c + 12},${c + 14} L${c},${c + 32} L${c - 12},${c + 14} L${c - 4},${c + 14} Z"/></g>`;
    }
    return s + '</svg>';
  }

  /* Simple sparkline: average line with a soft wash under it. */
  function sparkSvg(ts, values, { height = 44, min = 0 } = {}) {
    const pts = ts.map((t, i) => [t, values[i]]).filter((p) => p[1] !== null);
    if (pts.length < 2) return '<svg class="spark"></svg>';
    const w = 300, h = height, pad = 4;
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
    const vmax = Math.max(...pts.map((p) => p[1]), 1);
    const x = (t) => pad + (w - 2 * pad) * (t - t0) / Math.max(1, t1 - t0);
    const y = (v) => h - pad - (h - 2 * pad) * (v - min) / (vmax - min);
    const line = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(' ');
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
      <path class="wash" d="${line} L${x(t1)},${h} L${x(t0)},${h} Z"/>
      <path class="line" d="${line}" vector-effect="non-scaling-stroke"/>
    </svg>`;
  }

  async function getJSON(url) {
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) throw new Error(`${r.status} ${url}`);
    return r.json();
  }

  /* Run fn now, every `ms`, and right away when the tab comes back. */
  function poll(fn, ms) {
    fn();
    let timer = setInterval(fn, ms);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        clearInterval(timer);
        fn();
        timer = setInterval(fn, ms);
      }
    });
  }

  /* Units toggle (km/h · kt · m/s) inside `el`; calls onChange after a switch. */
  function unitToggle(el, onChange) {
    el.innerHTML = Object.entries(UNITS).map(([k, u]) =>
      `<button type="button" data-unit="${k}" aria-pressed="${k === unit()}">${u.label}</button>`).join('');
    el.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-unit]');
      if (!b) return;
      setUnit(b.dataset.unit);
      el.querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', x === b));
      onChange();
    });
  }

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
  }

  return {
    UNITS, STALE_S, unit, setUnit, speed, fmtSpeed, fmt, signed, ageSeconds, ageText, isStale,
    timeText, compassPoint, spokenPoint, band, bandHtml, BAND_INFO, trendText, compassSvg,
    sparkSvg, getJSON, poll, unitToggle, esc, store,
  };
})();
