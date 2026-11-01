'use strict';
(() => {
  const B = BBW;
  const box = document.getElementById('stations');
  let data = null;

  // Takeoffs first, then landings, then the rest; keeps admin sort order within each.
  const KIND_ORDER = { takeoff: 0, landing: 1, other: 2 };

  function card(st, now, tz) {
    const v = st.values;
    const avg = v.wind_avg_ms, gust = v.wind_gust_ms, dir = v.wind_dir_deg;
    const windStale = !avg || B.isStale(avg.ts, now);
    const offline = B.isStale(st.last_seen, now, 30 * 60);
    const dirOk = dir && !B.isStale(dir.ts, now);
    const u = B.UNITS[B.unit()].label;
    const d = st.derived;
    const band = windStale ? null : B.band(avg?.v, gust?.v, st.bands_kmh);
    const temp = v.temp_c && !B.isStale(v.temp_c.ts, now) ? `${B.fmt(v.temp_c.v, 1)} °C` : '–';
    const base = d.cloud_base_amsl_m ? `Cloud base ≈ ${d.cloud_base_amsl_m.toLocaleString('en-IN')} m` : '';
    const elev = st.elevation_m != null ? ` · ${Math.round(st.elevation_m).toLocaleString('en-IN')} m` : '';

    return `<a class="card station-card${offline ? ' stale' : ''}" href="/s/${encodeURIComponent(st.slug)}">
      <div>
        <div class="name">${B.esc(st.name)}</div>
        <div class="meta">${B.esc(st.kind[0].toUpperCase() + st.kind.slice(1))}${elev} ·
          ${offline ? '<span class="stale-note">offline, last seen ' + B.ageText(st.last_seen, now) + '</span>' : 'updated ' + B.ageText(st.last_seen, now)}</div>
      </div>
      <div class="wind${windStale ? ' stale' : ''}">
        ${B.compassSvg(dirOk ? dir.v : null, st.launch, { small: true })}
        <div>
          <div class="big">${B.fmtSpeed(avg?.v)}<small> ${u}</small></div>
          <div class="gust">Gust ${B.fmtSpeed(gust?.v)}${dirOk ? ' · from ' + B.compassPoint(dir.v) : ''}</div>
        </div>
      </div>
      <div class="row">${B.bandHtml(band)}<span class="trend">${windStale ? '' : B.trendText(d.wind_trend)}</span></div>
      ${B.sparkSvg(st.wind_strip.ts, st.wind_strip.avg)}
      <div class="row"><span>${temp}</span><span>${base}</span></div>
      ${st.frame ? `<img class="thumb" loading="lazy" src="${B.esc(st.frame.url)}" alt="Latest camera view at ${B.esc(st.name)}, ${B.timeText(st.frame.ts, tz)}">` : ''}
    </a>`;
  }

  function render() {
    if (!data) return;
    const now = Date.now() / 1000;
    const list = [...data.stations].sort((a, b) => (KIND_ORDER[a.kind] ?? 3) - (KIND_ORDER[b.kind] ?? 3));
    box.innerHTML = list.length
      ? list.map((st) => card(st, now, data.tz)).join('')
      : '<p class="empty">No stations yet.</p>';
  }

  async function load() {
    try {
      data = await B.getJSON('/api/v1/stations');
      render();
    } catch (e) {
      if (!data) box.innerHTML = '<p class="empty">Could not load stations. Retrying…</p>';
    }
  }

  B.unitToggle(document.getElementById('units'), render);
  B.poll(load, 30000);
  setInterval(render, 10000); // keep "x s ago" current between loads
})();
