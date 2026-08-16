/* Brawl Stars Counters — front end.
 *
 * Reads site/data/data.json, produced by code/compute.py. The data contract is
 * the only thing this file knows about the pipeline, so a better scoring method
 * upstream needs no change here.
 *
 * matchups[A] is a list of [opponentId, rawScore, n, ciHalfWidth, adjScore]
 * where a score is A's advantage over the opponent, in percentage points above
 * a 50% coin flip. Both scores are exactly antisymmetric, so one stored list
 * serves both views:
 *   score > 0  ->  A wins  ->  the opponent belongs under "A counters"
 *   score < 0  ->  A loses ->  the opponent belongs under "A is countered by"
 */

'use strict';

const $ = (id) => document.getElementById(id);

/* Positions in a matchup row. */
const RAW = 1, N = 2, CI = 3, ADJ = 4;

/* A brawler needs this many battles before it can appear in the top strip.
 * Without a floor, a brawler seen twice and lucky both times tops the chart. */
const STRIP_MIN_N = 500;
const STRIP_COUNT = 12;

const state = {
  base: null,        // the all-modes payload; also carries the mode manifest
  data: null,        // the payload currently on screen (all modes, or one mode)
  cache: new Map(),  // mode -> payload, so a mode is fetched at most once
  byId: new Map(),
  selected: null,
  mode: 'all',
  metric: 'raw',
  minN: 30,
  limit: 20,
  table: false,
};

const fmt = (n) => n.toLocaleString('en-US');

/* Which score column is on screen. Everything downstream reads this, so the
 * toggle never has to be threaded through individual render functions. */
const scoreOf = (row) => (state.metric === 'raw' ? row[RAW] : row[ADJ]);

/* ── Boot ─────────────────────────────────────────────────────────── */

async function boot() {
  let payload;
  try {
    const res = await fetch('data/data.json');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    payload = await res.json();
  } catch (err) {
    // The usual cause is opening index.html via file://, where fetch is blocked.
    $('subtitle').textContent =
      'Could not load data/data.json — serve this folder over HTTP ' +
      '(python -m http.server) rather than opening the file directly.';
    return;
  }

  state.base = payload;
  state.cache.set('all', payload);
  buildModeOptions(payload.modes || []);
  wireControls();
  applyPayload(payload);
}

/* The mode list comes from the data, not a hardcoded array: compute.py ships a
 * mode's file only once it has the coverage to justify one, so new modes appear
 * here on their own as collection accumulates. */
function buildModeOptions(modes) {
  const sel = $('mode');
  for (const m of modes) {
    const opt = document.createElement('option');
    opt.value = m.mode;
    opt.textContent = `${m.label} (${Math.round(m.coverage * 100)}% covered)`;
    sel.appendChild(opt);
  }
}

/* Swap in a payload and rebuild everything that depends on it. Win rates and
 * matchups are all mode-specific, so the strip and the grid change too. */
function applyPayload(payload) {
  state.data = payload;
  state.byId = new Map(payload.brawlers.map((b) => [b.id, b]));

  const s = payload.stats;
  const scope = payload.mode === 'all'
    ? 'across every mode'
    : `in ${payload.label}`;
  $('subtitle').textContent =
    `${fmt(s.battles)} battles ${scope} · ` +
    `${fmt(s.pairsReliable)} of ${fmt(s.pairsPossible)} matchups with ` +
    `${payload.minN}+ battles · updated ${payload.generated}`;

  buildStrip();
  buildGrid();

  // Keep the current brawler across a mode change when it has data there;
  // otherwise land on the best-sampled one so the view is never empty.
  const keep = state.selected != null && state.byId.has(state.selected);
  const best = payload.brawlers.slice().sort((a, b) => b.n - a.n)[0];
  select(keep ? state.selected : (best ? best.id : null));
}

async function setMode(mode) {
  if (mode === state.mode) return;

  if (!state.cache.has(mode)) {
    const entry = (state.base.modes || []).find((m) => m.mode === mode);
    if (!entry) return;
    document.body.classList.add('is-loading');
    try {
      const res = await fetch('data/' + entry.file);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      state.cache.set(mode, await res.json());
    } catch (err) {
      $('subtitle').textContent = `Could not load ${entry.label} data.`;
      $('mode').value = state.mode;   // put the control back where it was
      return;
    } finally {
      document.body.classList.remove('is-loading');
    }
  }

  state.mode = mode;
  applyPayload(state.cache.get(mode));
}

/* ── Portrait with a graceful fallback ────────────────────────────── */

function portrait(brawler, cls) {
  const img = document.createElement('img');
  img.src = brawler.img;
  img.alt = '';
  img.loading = 'lazy';
  if (cls) img.className = cls;
  // If the CDN ever drops an id, fall back to initials rather than a broken icon.
  img.addEventListener('error', () => {
    const ph = document.createElement('div');
    ph.className = (cls ? cls + ' ' : '') + 'ph';
    ph.textContent = brawler.name.slice(0, 2);
    img.replaceWith(ph);
  }, { once: true });
  return img;
}

/* ── Top brawlers strip ───────────────────────────────────────────── */

function buildStrip() {
  // The floor scales with the payload: a single mode holds a fraction of the
  // battles, so a fixed 500 would empty the strip on the thinner modes.
  const counts = state.data.brawlers.map((b) => b.n).sort((a, b) => a - b);
  const median = counts[Math.floor(counts.length / 2)] || 0;
  const floor = Math.max(100, Math.min(STRIP_MIN_N, Math.round(median / 4)));

  const eligible = state.data.brawlers
    .filter((b) => b.n >= floor)
    .sort((a, b) => b.winRate - a.winRate)
    .slice(0, STRIP_COUNT);

  $('strip-note').textContent =
    `Highest win rate ${state.data.mode === 'all' ? 'across every mode' :
      'in ' + state.data.label}, among brawlers with at least ` +
    `${fmt(floor)} battles. Click one to see its matchups.`;

  // Bars measure distance above a 50% coin flip, not distance from zero --
  // a bar starting at 0% would make a 52% and a 68% brawler look alike.
  const top = eligible.length ? eligible[0].winRate : 100;
  const span = Math.max(top - 50, 1);

  const ol = $('strip-list');
  ol.textContent = '';

  eligible.forEach((b, i) => {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.className = 'strip-item';
    btn.type = 'button';
    btn.title = `${b.name} — ${b.winRate}% win rate across ${fmt(b.n)} battles`;

    const rank = document.createElement('span');
    rank.className = 'strip-rank';
    rank.textContent = '#' + (i + 1);
    btn.appendChild(rank);

    btn.appendChild(portrait(b));

    const nm = document.createElement('span');
    nm.className = 'nm';
    nm.textContent = b.name;
    btn.appendChild(nm);

    const wr = document.createElement('span');
    wr.className = 'wr';
    wr.textContent = b.winRate.toFixed(1) + '%';
    btn.appendChild(wr);

    const track = document.createElement('div');
    track.className = 'track';
    const bar = document.createElement('div');
    bar.className = 'bar bar-good';
    bar.style.width = Math.max(2, ((b.winRate - 50) / span) * 100) + '%';
    track.appendChild(bar);
    btn.appendChild(track);

    btn.addEventListener('click', () => {
      select(b.id);
      $('selected-head').scrollIntoView({ behavior: 'smooth', block: 'center' });
    });

    li.appendChild(btn);
    ol.appendChild(li);
  });
}

/* ── Brawler picker ───────────────────────────────────────────────── */

function buildGrid() {
  const grid = $('grid');
  grid.textContent = '';
  const q = $('search').value.trim().toLowerCase();

  for (const b of state.data.brawlers) {
    if (q && !b.name.toLowerCase().includes(q)) continue;

    const tile = document.createElement('button');
    tile.className = 'tile';
    tile.type = 'button';
    tile.dataset.id = String(b.id);
    tile.setAttribute('role', 'option');
    tile.setAttribute('aria-selected', String(b.id === state.selected));
    tile.title = `${b.name} — ${b.winRate}% overall win rate (${fmt(b.n)} battles)`;
    tile.appendChild(portrait(b));

    const nm = document.createElement('span');
    nm.className = 'nm';
    nm.textContent = b.name;
    tile.appendChild(nm);

    tile.addEventListener('click', () => select(b.id));
    grid.appendChild(tile);
  }
}

function select(id) {
  state.selected = id;
  for (const tile of $('grid').children) {
    tile.setAttribute('aria-selected', String(Number(tile.dataset.id) === id));
  }
  render();
}

/* ── Rendering ────────────────────────────────────────────────────── */

function render() {
  const b = state.byId.get(state.selected);
  if (!b) return;

  $('empty').hidden = true;
  $('selected-head').hidden = false;

  const img = $('sel-img');
  img.src = b.img;
  img.alt = b.name;
  $('sel-name').textContent = b.name;
  $('sel-stats').textContent =
    `${b.winRate}% overall win rate across ${fmt(b.n)} battles`;
  $('ttl-a').textContent = b.name;
  $('ttl-b').textContent = b.name;

  $('metric-note').textContent = state.metric === 'raw'
    ? 'Raw head-to-head win rate. A brawler that is simply strong will appear ' +
      'to counter almost everyone, because it does — that is overall strength, ' +
      'not a matchup advantage.'
    : 'Adjusted for how strong each brawler is overall, so the number reflects ' +
      'the matchup itself. A dominant brawler now shows real weak points, and a ' +
      'weak brawler shows what it genuinely handles well.';

  // Re-sorted here rather than upstream, because the ordering depends on which
  // metric is selected and the stored order is by raw score.
  const all = (state.data.matchups[String(b.id)] || [])
    .filter((r) => r[N] >= state.minN)
    .slice()
    .sort((x, y) => scoreOf(y) - scoreOf(x));

  const good = all.filter((r) => scoreOf(r) > 0).slice(0, state.limit);
  const bad = all.filter((r) => scoreOf(r) < 0).reverse().slice(0, state.limit);

  $('panels').hidden = state.table;
  $('table-wrap').hidden = !state.table;

  if (state.table) {
    renderTable(b, all);
  } else {
    // One shared scale across both panels, so a bar's length means the same
    // thing on the left as on the right.
    const scale = Math.max(
      10,
      ...good.map((r) => Math.abs(scoreOf(r))),
      ...bad.map((r) => Math.abs(scoreOf(r)))
    );
    renderList($('list-good'), good, 'bar-good', scale, b);
    renderList($('list-bad'), bad, 'bar-bad', scale, b);
  }
}

function renderList(ol, rows, barClass, scale, subject) {
  ol.textContent = '';

  if (!rows.length) {
    const li = document.createElement('li');
    li.className = 'panel-note';
    li.textContent = 'No matchups here at the current settings.';
    ol.appendChild(li);
    return;
  }

  for (const r of rows) {
    const opp = state.byId.get(r[0]);
    if (!opp) continue;
    const score = scoreOf(r);

    const li = document.createElement('li');
    li.className = 'row' + (r[N] < state.data.minN ? ' thin' : '');
    li.appendChild(portrait(opp));

    const nm = document.createElement('div');
    nm.className = 'nm';
    nm.textContent = opp.name;
    li.appendChild(nm);

    const track = document.createElement('div');
    track.className = 'track';
    const bar = document.createElement('div');
    bar.className = 'bar ' + barClass;
    bar.style.width = Math.min(100, (Math.abs(score) / scale) * 100) + '%';
    track.appendChild(bar);
    li.appendChild(track);

    const val = document.createElement('div');
    val.className = 'val';
    // Magnitude only: the panel heading and bar color already carry direction.
    val.textContent = '+' + Math.abs(score).toFixed(1);
    li.appendChild(val);

    attachTooltip(li, opp, subject, r);
    ol.appendChild(li);
  }
}

function renderTable(subject, rows) {
  $('table-caption').textContent =
    `Every matchup for ${subject.name} with at least ${state.minN} battles, ` +
    `hardest opponent first. A positive score means ${subject.name} wins. ` +
    `Both scores are shown regardless of which one the panels are using.`;

  const tb = $('data-table').querySelector('tbody');
  tb.textContent = '';

  for (const r of rows.slice().sort((a, b) => scoreOf(a) - scoreOf(b))) {
    const opp = state.byId.get(r[0]);
    if (!opp) continue;

    const tr = document.createElement('tr');
    if (r[N] < state.data.minN) tr.className = 'thin';

    const signed = (v) => (v > 0 ? '+' : '') + v.toFixed(1);
    const cells = [
      opp.name,
      (50 + r[RAW]).toFixed(1) + '%',
      signed(r[RAW]),
      signed(r[ADJ]),
      '±' + r[CI].toFixed(1),
      fmt(r[N]),
    ];
    cells.forEach((text, i) => {
      const cell = document.createElement(i === 0 ? 'th' : 'td');
      if (i === 0) cell.scope = 'row';
      cell.textContent = text;
      tr.appendChild(cell);
    });
    tb.appendChild(tr);
  }
}

/* ── Tooltip ──────────────────────────────────────────────────────── */

function attachTooltip(el, opp, subject, row) {
  const tip = $('tooltip');
  const score = scoreOf(row);
  const winner = score > 0 ? subject : opp;
  const loser = score > 0 ? opp : subject;
  const wr = 50 + Math.abs(row[RAW]);

  const show = (ev) => {
    tip.textContent = '';

    const head = document.createElement('b');
    head.textContent = `${winner.name} beats ${loser.name}`;
    tip.appendChild(head);

    // Both numbers always appear, so switching the toggle never hides the one
    // you were just looking at.
    const lines = [
      `${wr.toFixed(1)}% raw win rate (±${row[CI].toFixed(1)} at 95%)`,
      `Raw score ${row[RAW] > 0 ? '+' : ''}${row[RAW].toFixed(1)} · ` +
        `adjusted ${row[ADJ] > 0 ? '+' : ''}${row[ADJ].toFixed(1)}`,
      `${fmt(row[N])} head-to-head battles`,
      row[N] < state.data.minN
        ? `Below the ${state.data.minN}-battle bar — treat as provisional`
        : null,
    ].filter(Boolean);

    for (const text of lines) {
      const div = document.createElement('div');
      div.className = 't-row';
      div.textContent = text;
      tip.appendChild(div);
    }

    tip.hidden = false;
    place(ev);
  };

  const place = (ev) => {
    const pad = 14;
    const r = tip.getBoundingClientRect();
    // Flip near the viewport edges so the tooltip never gets clipped.
    let x = ev.clientX + pad;
    let y = ev.clientY + pad;
    if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - pad;
    if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - pad;
    tip.style.left = Math.max(8, x) + 'px';
    tip.style.top = Math.max(8, y) + 'px';
  };

  el.addEventListener('mouseenter', show);
  el.addEventListener('mousemove', place);
  el.addEventListener('mouseleave', () => { tip.hidden = true; });
}

/* ── Controls ─────────────────────────────────────────────────────── */

function wireControls() {
  $('search').addEventListener('input', buildGrid);
  $('mode').addEventListener('change', (e) => setMode(e.target.value));
  $('metric').addEventListener('change', (e) => {
    state.metric = e.target.value;
    render();
  });
  $('min-n').addEventListener('change', (e) => {
    state.minN = Number(e.target.value);
    render();
  });
  $('limit').addEventListener('change', (e) => {
    state.limit = Number(e.target.value);
    render();
  });
  $('table-view').addEventListener('change', (e) => {
    state.table = e.target.checked;
    render();
  });
  $('theme-toggle').addEventListener('click', () => {
    const root = document.documentElement;
    const dark = getComputedStyle(root)
      .getPropertyValue('color-scheme').trim() === 'dark';
    root.setAttribute('data-theme', dark ? 'light' : 'dark');
  });
}

boot();
