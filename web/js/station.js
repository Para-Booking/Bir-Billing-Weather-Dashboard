'use strict';
(() => {
  const B = BBW;
  const slug = decodeURIComponent(location.pathname.split('/').filter(Boolean).pop() || '');
  const $ = (id) => document.getElementById(id);
  const RANGES = [[30, '30 m'], [60, '1 h'], [180, '3 h'], [360, '6 h'], [1440, '24 h'], [10080, '7 d']];
  let range = Number(B.store('bbw.range')) || 60;
  if (!RANGES.some(([m]) => m === range)) range = 60;

  let st = null, tz = 'Asia/Kolkata';
  let windSeries = null, airSeries = null, frames = [];
  const charts = {};

  /* ---------- small builders ---------- */
  const tile = (label, value, note = '', cls = '') =>
    `<div class="tile ${cls}"><div class="label">${label}</div><div class="value">${value}</div>${note ? `<div class="note">${note}</div>` : ''}</div>`;
  const fresh = (v, now) => v && !B.isStale(v.ts, now);

  /* ---------- wind now ---------- */
  function renderWindNow(now) {
    const v = st.values, d = st.derived;
    const avg = v.wind_avg_ms, gust = v.wind_gust_ms, lull = v.wind_lull_ms, dir = v.wind_dir_deg;
    const ok = fresh(avg, now);
    const dirOk = fresh(dir, now);
    const u = B.UNITS[B.unit()].label;
    $('wind-age').textContent = avg ? B.ageText(avg.ts, now) : '';
    if (!avg) {
      $('wind-body').innerHTML = '<p class="empty">No wind readings in the last 24 hours.</p>';
      return;
    }
    const band = ok ? B.band(avg.v, gust?.v, st.bands_kmh) : null;
    const varText = dirOk && fresh(v.wind_dir_var_deg, now)
      ? ` · varies ±${Math.round(v.wind_dir_var_deg.v)}°` : '';
    const launchNote = st.launch ? `<div class="age">Green sector: good launch directions ${Math.round(st.launch.from)}°–${Math.round(st.launch.to)}°</div>` : '';
    $('wind-body').innerHTML = `
      ${ok ? '' : `<p class="stale-note">Old reading (${B.ageText(avg.ts, now)}). The wind sensor may be offline.</p>`}
      <div class="hero-row${ok ? '' : ' stale'}">
        <div class="hero">${B.fmtSpeed(avg.v)}<span class="unit">${u}</span></div>
        <div class="subvals">
          <div><span>Gust</span><b>${B.fmtSpeed(gust?.v)}</b></div>
          <div><span>Lull</span><b>${B.fmtSpeed(lull?.v)}</b></div>
        </div>
        ${B.compassSvg(dirOk ? dir.v : null, st.launch)}
        <div class="subvals">
          <div>${dirOk ? `<b>${B.compassPoint(dir.v)}</b> ${Math.round(dir.v)}°${varText}` : '<span>No direction</span>'}</div>
          <div>${B.bandHtml(band)}</div>
          <div class="trend">${ok && d.wind_trend ? B.trendText(d.wind_trend) + ' <span class="age">vs 15 min ago</span>' : ''}</div>
        </div>
      </div>
      ${launchNote}`;
  }

  /* ---------- chart helpers ---------- */
  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  function axes(yLabelFmt) {
    const stroke = css('--muted'), grid = { stroke: css('--grid'), width: 1 };
    return [
      { stroke, grid, ticks: { show: false }, font: '12px system-ui', values: (u_, splits) => splits.map(xLabel) },
      { stroke, grid, ticks: { show: false }, size: 44, font: '12px system-ui', values: yLabelFmt },
    ];
  }

  function xLabel(ts) {
    const d = new Date(ts * 1000);
    if (range > 1440) return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: tz });
    return B.timeText(ts, tz);
  }

  function makeChart(key, el, opts, data) {
    if (charts[key]) charts[key].destroy();
    const width = Math.max(260, el.clientWidth);
    charts[key] = new uPlot({ width, height: opts.height || 220, ...opts }, data, el);
  }

  new ResizeObserver(() => {
    for (const [key, c] of Object.entries(charts)) {
      const el = c.root.parentElement;
      if (el && el.clientWidth) c.setSize({ width: el.clientWidth, height: c.height });
    }
  }).observe(document.querySelector('main'));

  const tzDate = (ts) => uPlot.tzDate(new Date(ts * 1e3), tz);

  /* ---------- wind history ---------- */
  function renderWindChart() {
    if (!windSeries) return;
    const s = windSeries.series;
    const u = B.unit();
    const conv = (arr) => arr.map((x) => B.speed(x, u));
    const avg = conv(s.wind_avg_ms), gust = conv(s.wind_gust_ms);
    const hasLull = s.wind_lull_ms.some((x) => x !== null);
    const low = hasLull ? conv(s.wind_lull_ms) : avg;
    const label = B.UNITS[u].label;
    const blue = css('--series-1'), wash = css('--series-1-wash');

    $('wind-legend').innerHTML =
      `<span><i class="key" style="background:${blue}"></i>Average</span>` +
      `<span><i class="key band-key"></i>${hasLull ? 'Gust–lull range' : 'Average–gust range'}</span>`;

    const readout = (i) => {
      if (i == null || i < 0 || windSeries.ts[i] == null) i = windSeries.ts.length - 1;
      if (i < 0) { $('wind-readout').textContent = 'No wind readings in this range.'; return; }
      const dir = s.wind_dir_deg[i];
      $('wind-readout').textContent =
        `${B.timeText(windSeries.ts[i], tz)} · average ${B.fmt(avg[i], B.UNITS[u].digits)} · gust ${B.fmt(gust[i], B.UNITS[u].digits)}` +
        (hasLull ? ` · lull ${B.fmt(low[i], B.UNITS[u].digits)}` : '') + ` ${label}` +
        (dir != null ? ` · from ${B.compassPoint(dir)}` : '');
    };

    makeChart('wind', $('wind-chart'), {
      tzDate,
      legend: { show: false },
      cursor: { points: { size: 8, width: 2, stroke: css('--surface') }, drag: { x: false, y: false } },
      scales: { x: { time: true }, y: { range: (u_, min, max) => [0, Math.max(10, Math.ceil((max || 0) * 1.15))] } },
      axes: axes((u_, vals) => vals.map((v) => v + '')),
      series: [
        {},
        { label: 'Low', stroke: 'transparent', width: 0, points: { show: false } },
        { label: 'Gust', stroke: 'transparent', width: 0, points: { show: false } },
        { label: 'Average', stroke: blue, width: 2, points: { show: false }, spanGaps: false },
      ],
      bands: [{ series: [2, 1], fill: wash }],
      hooks: { setCursor: [(c) => readout(c.cursor.idx)] },
    }, [windSeries.ts, low, gust, avg]);
    readout(null);

    // direction: dots only, on their own 0–360 axis (never a second y-axis)
    const hasDir = s.wind_dir_deg.some((x) => x !== null);
    $('dir-wrap').hidden = !hasDir;
    if (hasDir) {
      makeChart('dir', $('dir-chart'), {
        tzDate, height: 150, legend: { show: false }, cursor: { drag: { x: false, y: false } },
        scales: { x: { time: true }, y: { range: [0, 360] } },
        axes: [
          axes()[0],
          { ...axes()[1], splits: () => [0, 90, 180, 270, 360], values: (u_, v) => v.map((d) => ['N', 'E', 'S', 'W', 'N'][d / 90]) },
        ],
        series: [{}, {
          label: 'Direction', stroke: blue, width: 0, paths: () => null,
          points: { show: true, size: 6, fill: blue, stroke: blue },
        }],
      }, [windSeries.ts, s.wind_dir_deg]);
    } else if (charts.dir) { charts.dir.destroy(); delete charts.dir; }

    // table view (newest first, up to 60 rows)
    const rows = [];
    for (let i = windSeries.ts.length - 1; i >= 0 && rows.length < 60; i--) {
      rows.push(`<tr><td>${B.timeText(windSeries.ts[i], tz)}</td><td>${B.fmt(avg[i], B.UNITS[u].digits)}</td><td>${B.fmt(gust[i], B.UNITS[u].digits)}</td><td>${hasLull ? B.fmt(low[i], B.UNITS[u].digits) : '–'}</td><td>${s.wind_dir_deg[i] != null ? B.compassPoint(s.wind_dir_deg[i]) : '–'}</td></tr>`);
    }
    $('wind-table').innerHTML = `<table class="data"><thead><tr><th>Time</th><th>Average</th><th>Gust</th><th>Lull</th><th>From</th></tr></thead><tbody>${rows.join('')}</tbody></table>
      <div class="age">Speeds in ${label}${windSeries.step_s > 60 ? ', 10-minute summaries' : ''}.</div>`;
  }

  /* ---------- temperature, humidity, pressure ---------- */
  function pressureChange(ch) {
    if (ch == null) return '';
    return `<span class="${ch <= -1 ? 'delta-down' : ''}">${ch < -0.3 ? '↓' : ch > 0.3 ? '↑' : '→'} ${B.signed(ch)} hPa in 3 h</span>` +
      (ch <= -2 ? ' · falling fast' : '');
  }

  function renderAirTiles(now) {
    const v = st.values, d = st.derived;
    const t = v.temp_c, rh = v.rh, p = v.pressure_pa;
    const newest = Math.max(t?.ts || 0, rh?.ts || 0, p?.ts || 0);
    $('tph-age').textContent = newest ? B.ageText(newest, now) : '';
    const rate = d.temp_change_1h_c;
    $('tph-tiles').innerHTML =
      tile('Temperature', t ? `${B.fmt(t.v, 1)}<small> °C</small>` : '–',
        rate != null ? `${B.signed(rate)} °C in the last hour` : '', fresh(t, now) ? '' : 'stale') +
      tile('Humidity', rh ? `${B.fmt(rh.v)}<small> %</small>` : '–',
        d.dew_point_c != null ? `Dew point ${B.fmt(d.dew_point_c, 1)} °C` : '', fresh(rh, now) ? '' : 'stale') +
      tile('Pressure (sea level)', d.pressure_sl_hpa != null ? `${B.fmt(d.pressure_sl_hpa)}<small> hPa</small>` : '–',
        pressureChange(d.pressure_change_3h_hpa) + (p ? `<br>At station ${B.fmt(p.v / 100)} hPa` : ''),
        fresh(p, now) ? '' : 'stale');

    // thermals card: what the air means for flying
    $('thermal-age').textContent = t ? B.ageText(t.ts, now) : '';
    const spread = t && d.dew_point_c != null ? t.v - d.dew_point_c : null;
    $('thermal-tiles').innerHTML =
      tile('Cloud base (estimate)', d.cloud_base_amsl_m != null ? `${d.cloud_base_amsl_m.toLocaleString('en-IN')}<small> m</small>` : '–',
        d.cloud_base_agl_m != null ? `≈ ${d.cloud_base_agl_m.toLocaleString('en-IN')} m above the station` : '') +
      tile('Temp / dew point spread', spread != null ? `${B.fmt(spread, 1)}<small> °C</small>` : '–',
        spread != null ? (spread < 3 ? 'Moist air: low cloud or fog likely' : 'Each 1 °C ≈ 125 m of cloud base') : '') +
      tile('Warming', rate != null ? `${B.signed(rate)}<small> °C/h</small>` : '–',
        rate != null ? (rate >= 1.5 ? 'Warming fast: thermals building' : rate <= -1 ? 'Cooling' : 'Steady') : '');
  }

  function seaLevel(paArr, tArr) {
    const h = st && st.elevation_m != null ? st.elevation_m : null;
    return paArr.map((pa, i) => {
      if (pa == null) return null;
      if (h == null) return Math.round(pa) / 100;
      const tc = tArr[i] != null ? tArr[i] : 15;
      return Math.round(pa / 100 / Math.pow(1 - 0.0065 * h / (tc + 0.0065 * h + 273.15), 5.257) * 10) / 10;
    });
  }

  function lineChart(key, elId, readoutId, series, yFmt, readoutText, height = 170, yRange = undefined) {
    const ts = airSeries.ts;
    const readout = (i) => {
      if (i == null || i < 0) i = ts.length - 1;
      $(readoutId).textContent = i < 0 ? 'No readings in this range.' : `${B.timeText(ts[i], tz)} · ${readoutText(i)}`;
    };
    makeChart(key, $(elId), {
      tzDate, height, legend: { show: false },
      cursor: { points: { size: 8, width: 2, stroke: css('--surface') }, drag: { x: false, y: false } },
      scales: { x: { time: true }, y: yRange ? { range: yRange } : {} },
      axes: axes((u_, vals) => vals.map(yFmt)),
      series: [{}, ...series.map((x) => ({ label: x.label, stroke: x.color, width: 2, points: { show: false } }))],
      hooks: { setCursor: [(c) => readout(c.cursor.idx)] },
    }, [ts, ...series.map((x) => x.data)]);
    readout(null);
  }

  function renderAirCharts() {
    if (!airSeries) return;
    const s = airSeries.series;
    const dew = airSeries.ts.map((_, i) => {
      const tc = s.temp_c[i], rh = s.rh[i];
      if (tc == null || rh == null || rh <= 0) return null;
      const g = Math.log(rh / 100) + 17.62 * tc / (243.12 + tc);
      return Math.round(243.12 * g / (17.62 - g) * 10) / 10;
    });
    const press = seaLevel(s.pressure_pa, s.temp_c);
    const c1 = css('--series-1'), c2 = css('--series-2');
    $('temp-legend').innerHTML =
      `<span><i class="key" style="background:${c1}"></i>Temperature</span><span><i class="key" style="background:${c2}"></i>Dew point</span>`;
    lineChart('temp', 'temp-chart', 'temp-readout',
      [{ label: 'Temperature', color: c1, data: s.temp_c }, { label: 'Dew point', color: c2, data: dew }],
      (x) => x + '°', (i) => `temperature ${B.fmt(s.temp_c[i], 1)} °C · dew point ${B.fmt(dew[i], 1)} °C`, 190);
    lineChart('rh', 'rh-chart', 'rh-readout', [{ label: 'Humidity', color: c1, data: s.rh }],
      (x) => x + '%', (i) => `humidity ${B.fmt(s.rh[i])} %`);
    lineChart('press', 'press-chart', 'press-readout', [{ label: 'Pressure', color: c1, data: press }],
      (x) => (Number.isInteger(x) ? x.toFixed(0) : x.toFixed(1)), (i) => `${B.fmt(press[i], 1)} hPa (sea level)`, 170,
      // at least 4 hPa tall, so sensor noise doesn't look like a weather change
      (u_, min, max) => {
        if (min == null || max == null) return [1000, 1020];
        const mid = (min + max) / 2, half = Math.max((max - min) / 2 * 1.1, 2);
        return [mid - half, mid + half];
      });
  }

  /* ---------- rain, sun, air quality, status ---------- */
  function renderSmallCards(now) {
    const v = st.values, d = st.derived;

    const r = v.rain_mm;
    $('rain-card').hidden = !r;
    if (r) {
      $('rain-age').textContent = B.ageText(r.ts, now);
      $('rain-tiles').innerHTML =
        tile('Now', d.raining ? 'Raining' : 'Dry', '', B.isStale(r.ts, now, 40 * 60) ? 'stale' : '') +
        tile('Last hour', `${B.fmt(d.rain_1h_mm, 1)}<small> mm</small>`) +
        tile('Today', `${B.fmt(d.rain_today_mm, 1)}<small> mm</small>`);
    }

    $('sun-card').hidden = !st.sun || !st.sun.sunrise;
    if (st.sun && st.sun.sunrise) {
      const left = st.sun.sunset - now;
      const leftText = left > 0
        ? `${Math.floor(left / 3600)} h ${Math.floor((left % 3600) / 60)} min of light left`
        : 'After sunset';
      $('sun-tiles').innerHTML =
        tile('Sunrise', B.timeText(st.sun.sunrise, tz)) +
        tile('Sunset', B.timeText(st.sun.sunset, tz), now < st.sun.sunrise ? '' : leftText);
    }

    const pm = v.pm25;
    const pmOk = pm && !B.isStale(pm.ts, now, 30 * 60);
    $('air-card').hidden = !pmOk;
    if (pmOk) {
      const x = pm.v;
      const word = x <= 30 ? 'Clear' : x <= 60 ? 'Slight haze' : x <= 90 ? 'Hazy' : 'Very hazy';
      $('air-age').textContent = B.ageText(pm.ts, now);
      $('air-tiles').innerHTML = tile('PM2.5', `${B.fmt(x)}<small> µg/m³</small>`, word);
    }

    const sensors = [
      ['Wind speed', v.wind_avg_ms?.ts],
      ['Wind direction', v.wind_dir_deg?.ts],
      ['Temperature & humidity', v.temp_c?.ts],
      ['Pressure', v.pressure_pa?.ts],
      ['Rain gauge', v.rain_mm?.ts, 40 * 60],
      ['Air quality', v.pm25?.ts, 30 * 60],
      ['Camera', st.frame?.ts],
    ];
    $('status').innerHTML = sensors.map(([name, ts, limit]) => {
      const bad = B.isStale(ts, now, limit || B.STALE_S);
      return `<span>${name}</span><span class="${bad ? 'bad' : ''}">${ts ? B.ageText(ts, now) : 'no data (24 h)'}</span>`;
    }).join('');
  }

  /* ---------- camera ---------- */
  function renderCamera(now) {
    const latest = st.frame;
    $('cam-age').textContent = latest ? B.ageText(latest.ts, now) : '';
    if (!latest) { $('cam-body').innerHTML = '<p class="empty">No camera photos yet.</p>'; return; }
    const list = frames.length ? frames : [latest];
    if (!$('cam-img')) {
      $('cam-body').innerHTML = `<img id="cam-img" alt="">
        <input id="cam-slider" type="range" min="0" step="1" aria-label="Scroll back through the last 2 hours">
        <div class="when" id="cam-when"></div>`;
      $('cam-slider').addEventListener('input', () => showFrame(Number($('cam-slider').value), false));
    }
    const slider = $('cam-slider');
    const atEnd = Number(slider.value) >= Number(slider.max || 0);
    slider.max = String(list.length - 1);
    if (atEnd || !slider.value) slider.value = String(list.length - 1);
    showFrame(Number(slider.value), true);
  }
  function showFrame(i, keep) {
    const list = frames.length ? frames : [st.frame];
    const f = list[Math.min(i, list.length - 1)];
    if (!f) return;
    const img = $('cam-img');
    if (!keep || img.dataset.url !== f.url) {
      img.src = f.url;
      img.dataset.url = f.url;
    }
    img.alt = `Camera view at ${st.name}, ${B.timeText(f.ts, tz)}`;
    const isLatest = i >= list.length - 1;
    $('cam-when').textContent = `${B.timeText(f.ts, tz)}${isLatest ? ' · latest' : ' · drag right for the latest'}`;
  }

  /* ---------- loading ---------- */
  function renderAll() {
    if (!st) return;
    const now = Date.now() / 1000;
    document.title = `${st.name} · Bir Billing Wind`;
    $('name').textContent = st.name;
    const kind = st.kind[0].toUpperCase() + st.kind.slice(1);
    $('meta').textContent = [kind, st.elevation_m != null ? `${Math.round(st.elevation_m).toLocaleString('en-IN')} m` : null,
      st.last_seen ? `updated ${B.ageText(st.last_seen, now)}` : 'no readings yet'].filter(Boolean).join(' · ');
    $('fly-link').href = `/fly#${encodeURIComponent(st.slug)}`;
    renderWindNow(now);
    renderAirTiles(now);
    renderSmallCards(now);
    renderCamera(now);
  }

  async function loadNow() {
    try {
      const r = await B.getJSON(`/api/v1/stations/${encodeURIComponent(slug)}`);
      const first = !st;
      st = r.station; tz = r.tz || tz;
      renderAll();
      if (first) renderAirCharts(); // sea-level pressure needs the station's elevation
    } catch (e) {
      if (!st) $('name').textContent = 'Station not found';
    }
  }

  async function loadSeries() {
    try {
      const base = `/api/v1/stations/${encodeURIComponent(slug)}`;
      const [w, t, f] = await Promise.all([
        B.getJSON(`${base}/series?minutes=${range}&metrics=wind_avg_ms,wind_gust_ms,wind_lull_ms,wind_dir_deg`),
        B.getJSON(`${base}/series?minutes=${range}&metrics=temp_c,rh,pressure_pa`),
        B.getJSON(`${base}/frames?minutes=120`),
      ]);
      windSeries = w; airSeries = t; frames = f.frames;
      renderWindChart();
      renderAirCharts();
      if (st) renderCamera(Date.now() / 1000);
    } catch (e) { /* keep what we have; next poll retries */ }
  }

  function rangeChips() {
    const el = $('range');
    el.innerHTML = RANGES.map(([m, l]) => `<button type="button" data-m="${m}" aria-pressed="${m === range}">${l}</button>`).join('');
    el.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-m]');
      if (!b) return;
      range = Number(b.dataset.m);
      B.store('bbw.range', String(range));
      el.querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', x === b));
      loadSeries();
    });
  }

  /* ---------- daily timelapse ---------- */
  let timelapses = [];
  function showTimelapse(i) {
    const t = timelapses[i];
    if (!t) return;
    $('tl-video').src = t.url;
    const links = [];
    if (t.youtube) links.push(`<a href="${B.esc(t.youtube)}" target="_blank" rel="noopener">YouTube</a>`);
    if (t.vimeo) links.push(`<a href="${B.esc(t.vimeo)}" target="_blank" rel="noopener">Vimeo</a>`);
    $('tl-links').innerHTML = links.join(' · ');
  }
  async function loadTimelapses() {
    try {
      const r = await B.getJSON(`/api/v1/stations/${encodeURIComponent(slug)}/timelapses`);
      timelapses = r.timelapses || [];
      if (!timelapses.length) return;
      const sel = $('tl-day');
      sel.innerHTML = timelapses.map((t, i) => {
        const label = new Date(t.day + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
        return `<option value="${i}">${B.esc(label)}</option>`;
      }).join('');
      sel.onchange = () => showTimelapse(Number(sel.value));
      showTimelapse(0);
      $('tl-card').hidden = false;
    } catch (e) { /* no timelapse card */ }
  }

  B.unitToggle($('units'), () => { renderAll(); renderWindChart(); });
  rangeChips();
  B.poll(loadNow, 30000);
  B.poll(loadSeries, 60000);
  loadTimelapses();
  setInterval(() => renderAll(), 10000);
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { renderWindChart(); renderAirCharts(); });
})();
