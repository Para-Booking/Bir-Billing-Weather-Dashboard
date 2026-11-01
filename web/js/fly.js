'use strict';
/* Flight mode: one station per screen, refreshed every 20 s, screen kept on,
   values spoken on request. Works from the last saved data when offline. */
(() => {
  const B = BBW;
  const $ = (id) => document.getElementById(id);
  const REFRESH_MS = 20000;
  const OLD_S = 180;          // data older than this gets the full-width warning
  const AUTO_SPEAK_MS = 120000;
  const UNIT_ORDER = ['kmh', 'kt', 'ms'];

  let data = null;            // last /api/v1/fly response
  let offline = false;
  let index = 0;
  let autoTimer = null;

  try { data = JSON.parse(B.store('bbw.flyCache') || 'null'); } catch (e) { data = null; }

  function stations() { return data ? data.stations : []; }

  function pickInitial() {
    const list = stations();
    if (!list.length) return;
    const wanted = decodeURIComponent(location.hash.slice(1)) || B.store('bbw.flyStation');
    let i = list.findIndex((s) => s.slug === wanted);
    if (i < 0) i = list.findIndex((s) => s.kind === 'landing');
    index = Math.max(0, i);
  }

  function go(delta) {
    const list = stations();
    if (list.length < 2) return;
    index = (index + delta + list.length) % list.length;
    B.store('bbw.flyStation', list[index].slug);
    history.replaceState(null, '', '#' + encodeURIComponent(list[index].slug));
    render();
  }

  function arrowSvg(dir) {
    let s = '<svg class="arrow" viewBox="0 0 100 100" aria-hidden="true"><circle class="rim" cx="50" cy="50" r="45"/><text class="n" x="50" y="15">N</text>';
    if (dir != null) {
      s += `<g transform="rotate(${dir} 50 50)"><path class="shape" d="M43,18 L57,18 L57,58 L72,58 L50,88 L28,58 L43,58 Z"/></g>`;
    }
    return s + '</svg>';
  }

  function stripSvg(st, nowS) {
    const strip = st.strip;
    const W = 300, H = 60, start = nowS - 30 * 60;
    const strongMs = st.bands_kmh ? st.bands_kmh.strong / 3.6 : null;
    const top = Math.max(1, ...strip.gust.filter((x) => x != null), ...strip.avg.filter((x) => x != null),
      strongMs ? strongMs * 1.05 : 0) * 1.08;
    const y = (v) => H - (H - 2) * (v / top);
    const bw = W / 30 - 2;
    let s = `<svg class="strip" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">`;
    strip.ts.forEach((t, i) => {
      if (t < start) return;
      const x = ((t - start) / 1800) * (W - bw);
      const g = strip.gust[i], a = strip.avg[i];
      if (g != null) s += `<rect class="gustbar" x="${x.toFixed(1)}" y="${y(g).toFixed(1)}" width="${bw}" height="${(H - y(g)).toFixed(1)}" vector-effect="non-scaling-stroke"/>`;
      if (a != null) s += `<rect class="avgbar" x="${x.toFixed(1)}" y="${y(a).toFixed(1)}" width="${bw}" height="${(H - y(a)).toFixed(1)}"/>`;
    });
    if (strongMs) s += `<line class="limit" x1="0" x2="${W}" y1="${y(strongMs).toFixed(1)}" y2="${y(strongMs).toFixed(1)}" vector-effect="non-scaling-stroke"/>`;
    return s + '</svg>';
  }

  function current() {
    const st = stations()[index];
    if (!st) return null;
    const v = st.values;
    return {
      st,
      avg: v.wind_avg_ms, gust: v.wind_gust_ms,
      dir: v.wind_dir_deg && !B.isStale(v.wind_dir_deg.ts) ? v.wind_dir_deg.v : null,
    };
  }

  function render() {
    const list = stations();
    if (!list.length) {
      $('main').innerHTML = `<div class="nodata">${offline ? 'OFFLINE · no saved data yet' : 'No stations yet'}</div>`;
      return;
    }
    const nowS = Date.now() / 1000;
    const c = current();
    const { st, avg, gust, dir } = c;
    const u = B.unit();
    const label = B.UNITS[u].label;

    $('name').textContent = st.name;
    $('dots').textContent = list.length > 1 ? list.map((_, i) => (i === index ? '●' : '○')).join('') : '';
    $('prev').disabled = $('next').disabled = list.length < 2;
    $('unit').textContent = label;

    // age / warnings
    const age = avg ? B.ageSeconds(avg.ts, nowS) : Infinity;
    const ageMin = Math.round(age / 60);
    if (offline) {
      $('age').innerHTML = `<div class="alert">OFFLINE · data ${Number.isFinite(age) ? (age < 60 ? Math.round(age) + ' s' : ageMin + ' min') : ''} old</div>`;
    } else if (age > OLD_S) {
      $('age').innerHTML = `<div class="alert">OLD DATA · ${Number.isFinite(age) ? ageMin + ' min' : 'none'}</div>`;
    } else {
      $('age').innerHTML = `<div class="age-row">${Math.round(age)} s ago</div>`;
    }

    if (!avg) {
      $('main').innerHTML = '<div class="nodata">NO WIND DATA</div>';
      $('strip-wrap').innerHTML = '';
      return;
    }

    const band = B.band(avg.v, gust?.v, st.bands_kmh);
    const bandWord = band ? B.BAND_INFO[band].word : '';
    const trend = st.trend ? `${{ rising: '↑', falling: '↓', steady: '→' }[st.trend.trend]} ${st.trend.trend.toUpperCase()}` : '';
    $('main').innerHTML = `
      <div class="speed"><span class="num">${B.fmtSpeed(avg.v)}</span><span class="unit">${label}</span></div>
      <div class="gust"><small>GUST </small>${B.fmtSpeed(gust?.v)}</div>
      <div class="lower">
        ${arrowSvg(dir)}
        <div class="facts">
          <div class="dirtext">${dir != null ? `FROM ${B.compassPoint(dir)} <span class="deg">${Math.round(dir)}°</span>` : 'NO DIRECTION'}</div>
          ${band ? `<div><span class="bandblock ${band}"><span>${bandWord}</span></span></div>` : ''}
          <div class="trendword">${trend}</div>
        </div>
      </div>`;

    const limit = st.bands_kmh ? `- - - ${B.fmtSpeed(st.bands_kmh.strong / 3.6)} ${label} strong` : '';
    $('strip-wrap').innerHTML = stripSvg(st, nowS) +
      `<div class="strip-label"><span>30 min ago</span><span>${limit}</span><span>now</span></div>`;
  }

  async function load() {
    try {
      const fresh = await B.getJSON('/api/v1/fly');
      const first = !data;
      data = fresh;
      offline = false;
      B.store('bbw.flyCache', JSON.stringify(fresh));
      if (first || index >= stations().length) pickInitial();
    } catch (e) {
      offline = true;
    }
    render();
  }

  /* ---------- speech ---------- */
  function speak() {
    if (!('speechSynthesis' in window)) return;
    const c = current();
    if (!c) return;
    const { st, avg, gust, dir } = c;
    const u = B.unit();
    let text = `${st.name}. `;
    if (!avg) text += 'No wind data.';
    else {
      const age = B.ageSeconds(avg.ts);
      if (offline || age > OLD_S) text += `Warning, data is ${Math.round(age / 60)} minutes old. `;
      text += `Wind ${B.fmtSpeed(avg.v)}`;
      if (gust) text += `, gusting ${B.fmtSpeed(gust.v)}`;
      text += ` ${B.UNITS[u].spoken}.`;
      if (dir != null) text += ` From ${B.spokenPoint(dir)}.`;
      if (st.trend) text += ` ${st.trend.trend}.`;
    }
    speechSynthesis.cancel();
    const msg = new SpeechSynthesisUtterance(text);
    msg.lang = 'en-IN';
    msg.rate = 0.95;
    speechSynthesis.speak(msg);
  }
  $('speak').addEventListener('click', speak);
  $('auto').addEventListener('click', () => {
    const on = !autoTimer;
    $('auto').setAttribute('aria-pressed', String(on));
    if (on) { speak(); autoTimer = setInterval(speak, AUTO_SPEAK_MS); }
    else { clearInterval(autoTimer); autoTimer = null; }
  });

  /* ---------- controls ---------- */
  $('prev').addEventListener('click', () => go(-1));
  $('next').addEventListener('click', () => go(1));
  $('unit').addEventListener('click', () => {
    B.setUnit(UNIT_ORDER[(UNIT_ORDER.indexOf(B.unit()) + 1) % UNIT_ORDER.length]);
    render();
  });
  $('invert').addEventListener('click', () => {
    const on = document.documentElement.classList.toggle('invert');
    B.store('bbw.flyInvert', on ? '1' : '0');
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft') go(-1);
    if (e.key === 'ArrowRight') go(1);
  });

  // swipe left/right to change station
  let touch = null;
  document.addEventListener('touchstart', (e) => { const t = e.touches[0]; touch = [t.clientX, t.clientY]; }, { passive: true });
  document.addEventListener('touchend', (e) => {
    if (!touch) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - touch[0], dy = t.clientY - touch[1];
    touch = null;
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) go(dx < 0 ? 1 : -1);
  }, { passive: true });

  // hold to exit, so a bump doesn't close it
  const exitBtn = $('exit');
  let holdTimer = null;
  const startHold = (e) => {
    e.preventDefault();
    exitBtn.classList.add('holding');
    holdTimer = setTimeout(() => {
      const back = document.referrer && new URL(document.referrer).origin === location.origin;
      if (back) history.back(); else location.href = '/';
    }, 800);
  };
  const endHold = () => { exitBtn.classList.remove('holding'); clearTimeout(holdTimer); };
  exitBtn.addEventListener('pointerdown', startHold);
  ['pointerup', 'pointerleave', 'pointercancel'].forEach((ev) => exitBtn.addEventListener(ev, endHold));
  exitBtn.addEventListener('contextmenu', (e) => e.preventDefault());

  /* ---------- keep the screen on ---------- */
  let wakeLock = null;
  async function keepAwake() {
    if (!('wakeLock' in navigator)) {
      $('wake').textContent = 'Screen may turn off: set auto-lock to "Never" while flying';
      return;
    }
    try {
      wakeLock = await navigator.wakeLock.request('screen');
      $('wake').textContent = '● Screen stays on';
      wakeLock.addEventListener('release', () => { $('wake').textContent = 'Screen may turn off'; });
    } catch (e) {
      $('wake').textContent = 'Screen may turn off: tap anywhere to keep it on';
    }
  }
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') keepAwake(); });
  document.addEventListener('click', () => { if (!wakeLock || wakeLock.released) keepAwake(); });

  pickInitial();
  render();
  keepAwake();
  B.poll(load, REFRESH_MS);
  setInterval(render, 5000); // keep the age current between refreshes
})();
