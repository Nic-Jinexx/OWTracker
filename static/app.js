/* OWTracker frontend — vanilla JS, no build step, no framework.
 *
 * All match writes go through the draft endpoints, so manual entry exercises
 * the exact same commit path that extraction will use later.
 */

'use strict';

// ---------------------------------------------------------------- utilities

const api = {
  async request(method, url, body) {
    const options = { method, headers: {} };
    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    const text = await response.text();
    const data = text ? JSON.parse(text) : null;
    if (!response.ok) {
      const detail = data && data.detail;
      const problems = detail && detail.problems ? detail.problems.join(' ') : null;
      throw new Error(problems || (detail ? JSON.stringify(detail) : response.statusText));
    }
    return data;
  },
  get(url) { return this.request('GET', url); },
  post(url, body) { return this.request('POST', url, body); },
  patch(url, body) { return this.request('PATCH', url, body); },
  del(url) { return this.request('DELETE', url); },
};

const el = (id) => document.getElementById(id);
const view = () => el('view');

function template(name) {
  return el('tpl-' + name).content.cloneNode(true);
}

function toast(message, ms = 2600) {
  const node = el('toast');
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { node.hidden = true; }, ms);
}

/* Nothing in this file escaped anything until now, and nothing needed to:
 * every interpolated string came from seed data we wrote. Player names do not
 * — they are read off a screenshot or typed by the operator, and invariant 4
 * says they are recorded verbatim, punctuation and all. */
function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

/* Screenshot paths are stored root-relative (`data/screenshots/...`) because
 * that is what makes sense inside the database, but only `/screenshots` is
 * mounted. The server hands back a ready-made `url`; the fallback covers rows
 * written before it did. */
function screenshotUrl(entry) {
  if (entry.url) return entry.url;
  const stored = entry.path || entry.file_path || '';
  return '/screenshots/' + stored.replace(/^data\/screenshots\//, '');
}

const option = (value, label, selected) =>
  `<option value="${esc(value)}"${selected ? ' selected' : ''}>${esc(label)}</option>`;

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return '';
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function parseDuration(text) {
  const trimmed = (text || '').trim();
  if (!trimmed) return null;
  const parts = trimmed.split(':').map((p) => parseInt(p, 10));
  if (parts.some(Number.isNaN)) return null;
  return parts.length === 2 ? parts[0] * 60 + parts[1] : parts[0];
}

function localDateTimeValue(iso) {
  const date = iso ? new Date(iso) : new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
       + `T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// Cached seed lookups.
let reference = null;
async function loadReference() {
  if (!reference) reference = await api.get('/api/reference');
  return reference;
}

// ------------------------------------------------------------- entry / review

const STAT_COLUMNS = [
  ['eliminations', 'E'],
  ['assists', 'A'],
  ['deaths', 'D'],
  ['damage', 'DMG'],
  ['healing', 'H'],
  ['mitigation', 'MIT'],
];

async function renderEntry(draftId) {
  await loadReference();

  let draft;
  if (draftId) {
    draft = await api.get('/api/drafts/' + draftId);
    draft.id = Number(draftId);
  } else {
    draft = await api.post('/api/drafts', {});
    location.hash = '#/new/' + draft.id;
    return; // re-enters with the id in the URL
  }

  const node = template('entry');
  const root = node.querySelector('.entry');
  let payload = draft.payload;
  let threshold = 0.75;
  api.get('/api/settings').then((s) => { threshold = s.confidence_threshold; });

  // --- persistence ------------------------------------------------------

  let pending = null;
  async function patch(body) {
    const response = await api.patch('/api/drafts/' + draft.id, body);
    payload = response.payload;
    renderProblems(response.problems);
    return response;
  }

  function queuePatch(body) {
    // Coalesce rapid edits into one request.
    pending = pending ? mergeBodies(pending, body) : body;
    clearTimeout(queuePatch._timer);
    queuePatch._timer = setTimeout(async () => {
      const body = pending;
      pending = null;
      try { await patch(body); } catch (error) { toast(error.message); }
    }, 250);
  }

  function mergeBodies(a, b) {
    const merged = { meta: { ...(a.meta || {}), ...(b.meta || {}) } };
    const rows = [...(a.rows || [])];
    for (const row of b.rows || []) {
      const index = rows.findIndex((r) => r.team === row.team && r.row_index === row.row_index);
      if (index >= 0) rows[index] = { ...rows[index], ...row };
      else rows.push(row);
    }
    if (rows.length) merged.rows = rows;
    if (b.bans || a.bans) merged.bans = b.bans || a.bans;
    return merged;
  }

  function renderProblems(problems) {
    const box = root.querySelector('[data-role="problems"]');
    const save = root.querySelector('[data-role="save"]');
    if (!problems || !problems.length) {
      box.innerHTML = '<span class="muted">Ready to save.</span>';
      save.disabled = false;
      return;
    }
    box.innerHTML = '<ul>' + problems.map((p) => `<li>${p}</li>`).join('') + '</ul>';
    save.disabled = true;
  }

  // --- drop zone --------------------------------------------------------

  const dropzone = root.querySelector('[data-role="dropzone"]');
  const fileInput = root.querySelector('[data-role="file-input"]');
  const attachedBox = root.querySelector('[data-role="attached"]');
  const extractorNote = root.querySelector('[data-role="extractor-note"]');

  api.get(`/api/drafts/${draft.id}/extractor`).then((status) => {
    if (status.glyph_atlas_ready && status.hero_hashes_ready) {
      extractorNote.textContent =
        'Reference libraries loaded — dropped screenshots will be read automatically.';
    } else {
      const missing = [];
      if (!status.glyph_atlas_ready) missing.push('digit atlas');
      if (!status.hero_hashes_ready) missing.push('hero portraits');
      extractorNote.innerHTML =
        `Reference libraries not built yet (${missing.join(', ')}). Screenshots are ` +
        `archived with the match, but the values below still need typing in.`;
    }
  });

  function paintAttached() {
    const files = payload.files || [];
    attachedBox.innerHTML = files.map((f) => `
      <figure data-sha="${f.sha256}">
        <img src="${screenshotUrl(f)}" alt="${esc(f.filename)}">
        <figcaption>${esc(f.filename)}${f.width ? ` · ${f.width}×${f.height}` : ''}</figcaption>
        <select data-role="kind">
          <option value="endgame_report"${f.kind === 'endgame_report' ? ' selected' : ''}>Endgame report</option>
          <option value="in_game_scoreboard"${f.kind === 'in_game_scoreboard' ? ' selected' : ''}>Tab scoreboard</option>
        </select>
      </figure>`).join('');
  }

  attachedBox.addEventListener('change', async (event) => {
    const select = event.target.closest('[data-role="kind"]');
    if (!select) return;
    const sha = select.closest('figure').dataset.sha;
    try {
      const response = await api.patch(
        `/api/drafts/${draft.id}/files/${sha}`, { kind: select.value });
      payload = response.payload;
      renderProblems(response.problems);
      toast('Screenshot kind corrected.');
    } catch (error) { toast(error.message); }
  });

  function showWarnings(warnings) {
    const existing = root.querySelector('.warnings');
    if (existing) existing.remove();
    if (!warnings || !warnings.length) return;
    const box = document.createElement('div');
    box.className = 'warnings';
    box.innerHTML = '<strong>Notes</strong><ul>' +
      warnings.map((w) => `<li>${w}</li>`).join('') + '</ul>';
    dropzone.parentElement.appendChild(box);
  }

  async function upload(fileList, { override = false } = {}) {
    const files = [...fileList].filter((f) => f.type.startsWith('image/'));
    if (!files.length) { toast('Those files are not images.'); return; }

    const form = new FormData();
    for (const file of files) form.append('files', file, file.name);
    if (override) form.append('override_duplicate', 'true');

    dropzone.classList.add('busy');
    try {
      const response = await fetch(`/api/drafts/${draft.id}/files`,
                                   { method: 'POST', body: form });
      const data = await response.json();

      if (response.status === 409 && data.detail?.code === 'duplicate_screenshot') {
        // Invariant 5: an already-committed file needs an explicit override.
        if (confirm(data.detail.message + '\n\nAttach it anyway?')) {
          dropzone.classList.remove('busy');
          return upload(fileList, { override: true });
        }
        return;
      }
      if (!response.ok) throw new Error(data.detail?.message || JSON.stringify(data.detail));

      payload = data.payload;
      paintAttached();
      paintResult(); paintMap(); paintBans(); paintTeamSize(); paintAllRows();
      renderProblems(data.problems);
      showWarnings(data.warnings);
      toast(data.attached.length
        ? `Attached ${data.attached.length} screenshot(s).`
        : 'Nothing attached.');
    } catch (error) {
      toast(error.message);
    } finally {
      dropzone.classList.remove('busy');
    }
  }

  dropzone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    if (fileInput.files.length) upload(fileInput.files);
    fileInput.value = '';
  });
  ['dragenter', 'dragover'].forEach((name) =>
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.add('dragging');
    }));
  ['dragleave', 'drop'].forEach((name) =>
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.remove('dragging');
    }));
  dropzone.addEventListener('drop', (event) => {
    if (event.dataTransfer?.files?.length) upload(event.dataTransfer.files);
  });

  // --- result -----------------------------------------------------------

  const resultBox = root.querySelector('[data-role="result"]');
  function paintResult() {
    const current = payload.meta.result.value;
    resultBox.querySelectorAll('button').forEach((button) => {
      button.classList.toggle('selected', button.dataset.value === current);
    });
  }
  resultBox.addEventListener('click', async (event) => {
    const button = event.target.closest('button');
    if (!button) return;
    const value = payload.meta.result.value === button.dataset.value ? null : button.dataset.value;
    payload.meta.result.value = value;
    paintResult();
    try { await patch({ meta: { result: value } }); } catch (error) { toast(error.message); }
  });

  // --- map picker -------------------------------------------------------

  const mapBox = root.querySelector('[data-role="maps"]');
  mapBox.innerHTML = Object.entries(reference.maps_by_mode).map(([mode, maps]) => `
    <div class="mode-row">
      <span class="mode-name">${mode}</span>
      ${maps.map((m) => `<button type="button" class="chip" data-map="${m.id}" data-mode="${mode}">${m.name}</button>`).join('')}
    </div>`).join('');

  function paintMap() {
    const current = payload.meta.map_id.value;
    mapBox.querySelectorAll('[data-map]').forEach((chip) => {
      chip.classList.toggle('selected', Number(chip.dataset.map) === current);
    });
  }
  mapBox.addEventListener('click', (event) => {
    const chip = event.target.closest('[data-map]');
    if (!chip) return;
    const id = Number(chip.dataset.map);
    const next = payload.meta.map_id.value === id ? null : id;
    payload.meta.map_id.value = next;
    paintMap();
    queuePatch({ meta: { map_id: next, mode: next ? chip.dataset.mode : null } });
  });

  // --- bans -------------------------------------------------------------

  const banBox = root.querySelector('[data-role="bans"]');
  banBox.innerHTML = reference.heroes
    .map((h) => `<button type="button" class="chip" data-hero="${h.id}">${h.name}</button>`)
    .join('');

  function paintBans() {
    const banned = new Set((payload.bans || []).map((b) => b.hero_id));
    banBox.querySelectorAll('[data-hero]').forEach((chip) => {
      chip.classList.toggle('selected', banned.has(Number(chip.dataset.hero)));
    });
  }
  banBox.addEventListener('click', (event) => {
    const chip = event.target.closest('[data-hero]');
    if (!chip) return;
    const id = Number(chip.dataset.hero);
    const bans = (payload.bans || []).filter((b) => b.hero_id !== id);
    if (bans.length === (payload.bans || []).length) {
      bans.push({ hero_id: id, slot_index: bans.length });
    }
    payload.bans = bans;
    paintBans();
    queuePatch({ bans });
  });

  // --- simple meta fields ----------------------------------------------

  const playedAt = root.querySelector('#played-at');
  playedAt.value = localDateTimeValue(payload.meta.played_at.value);
  playedAt.addEventListener('change', () => {
    queuePatch({ meta: { played_at: new Date(playedAt.value).toISOString() } });
  });

  const duration = root.querySelector('[data-role="duration"]');
  duration.value = formatDuration(payload.meta.duration_seconds.value);
  duration.addEventListener('change', () => {
    queuePatch({ meta: { duration_seconds: parseDuration(duration.value) } });
  });

  const rankOptions = '<option value="">—</option>' +
    reference.ranks.map((r) => `<option value="${r.id}">${r.name}</option>`).join('');
  for (const key of ['rank_range_low', 'rank_range_high']) {
    const select = root.querySelector(`[data-meta="${key}"]`);
    select.innerHTML = rankOptions;
    select.value = payload.meta[key].value ?? '';
    select.addEventListener('change', () => {
      queuePatch({ meta: { [key]: select.value ? Number(select.value) : null } });
    });
  }

  const notes = root.querySelector('#notes');
  notes.value = payload.meta.notes.value || '';
  notes.addEventListener('change', () => queuePatch({ meta: { notes: notes.value || null } }));

  const teamSize = root.querySelector('[data-role="team-size"]');
  function paintTeamSize() {
    teamSize.value = String(payload.meta.team_size.value || 6);
  }
  paintTeamSize();
  teamSize.addEventListener('change', async () => {
    // Changing team size reshapes the roster, so start a fresh draft rather
    // than trying to add or drop rows underneath the operator.
    if (!confirm('Change team size? This starts a new blank draft.')) {
      teamSize.value = String(payload.meta.team_size.value || 6);
      return;
    }
    await api.del('/api/drafts/' + draft.id);
    const fresh = await api.post('/api/drafts', { team_size: Number(teamSize.value) });
    location.hash = '#/new/' + fresh.id;
  });

  // --- rosters ----------------------------------------------------------

  const heroOptions = '<option value="">—</option>' + ['tank', 'damage', 'support']
    .map((role) => `<optgroup label="${role}">` +
      reference.heroes_by_role[role].map((h) => `<option value="${h.id}">${h.name}</option>`).join('') +
      '</optgroup>').join('');

  function rowMarkup(row) {
    const isMe = row.is_me ? ' on' : '';
    /* The old `/${row.nameplate_crop}` produced `/data/screenshots/...`, which
     * is not mounted. The server now hands over a real URL. */
    const crop = row.nameplate_crop
      ? `<img class="nameplate-crop" src="${screenshotUrl(
           { url: row.nameplate_crop_url, path: row.nameplate_crop })}"
           alt="nameplate" title="Type the name once — it fills itself in next time.">`
      : '';
    return `
      <div class="roster-row" data-team="${row.team}" data-index="${row.row_index}">
        <button type="button" class="me-toggle${isMe}" data-field="is_me" title="This is me">ME</button>
        <select data-field="role">
          <option value="">—</option>
          <option value="tank">Tank</option>
          <option value="damage">Damage</option>
          <option value="support">Support</option>
        </select>
        <select data-field="hero_id">${heroOptions}</select>
        <div>${crop}<input type="text" data-field="player_name" placeholder="name"></div>
        ${STAT_COLUMNS.map(([field]) =>
          `<input type="text" inputmode="numeric" class="num" data-field="${field}">`).join('')}
      </div>`;
  }

  function headMarkup() {
    return `<div class="roster-row roster-head">
      <span></span><span>Role</span><span>Hero</span><span>Player</span>
      ${STAT_COLUMNS.map(([, label]) => `<span class="num">${label}</span>`).join('')}
    </div>`;
  }

  function rowsFor(team) {
    return payload.rows.filter((r) => r.team === team)
      .sort((a, b) => a.row_index - b.row_index);
  }

  function buildRosters() {
    for (const team of ['ally', 'enemy']) {
      const container = root.querySelector(`[data-role="rows-${team}"]`);
      container.innerHTML = headMarkup() + rowsFor(team).map(rowMarkup).join('');
    }
  }

  function rosterShapeMatches() {
    return ['ally', 'enemy'].every((team) =>
      root.querySelectorAll(`[data-role="rows-${team}"] .roster-row[data-team]`).length
        === rowsFor(team).length);
  }

  buildRosters();

  function findRow(team, index) {
    return payload.rows.find((r) => r.team === team && r.row_index === index);
  }

  function paintRow(element) {
    const row = findRow(element.dataset.team, Number(element.dataset.index));
    if (!row) return;
    element.querySelector('.me-toggle').classList.toggle('on', !!row.is_me);
    for (const input of element.querySelectorAll('[data-field]')) {
      const field = input.dataset.field;
      if (field === 'is_me') continue;
      const envelope = row[field] || {};
      if (input.tagName === 'SELECT') input.value = envelope.value ?? '';
      else input.value = envelope.value ?? '';
      // Invariant 3: anything the matcher wasn't sure about is visible.
      const low = envelope.source === 'template'
        && typeof envelope.confidence === 'number'
        && envelope.confidence < threshold;
      input.classList.toggle('low-confidence', !!low);
      if (field === 'hero_id') {
        input.classList.toggle('unknown-hero',
          envelope.source === 'template' && (envelope.value === null || envelope.value === undefined));
      }
    }
  }

  function paintAllRows() {
    // An extracted screenshot can carry more players than the draft was
    // created with — the row count is read off the image, not the setting,
    // and invariant 6 says the row count comes off the image. Rebuild before
    // painting, or the extra rows sit in the payload with no cell on screen:
    // committed, but never seen by the operator.
    if (!rosterShapeMatches()) buildRosters();
    root.querySelectorAll('.roster-row[data-team]').forEach(paintRow);
  }

  root.addEventListener('change', (event) => {
    const input = event.target.closest('[data-field]');
    if (!input) return;
    const rowElement = input.closest('.roster-row[data-team]');
    if (!rowElement) return;
    const team = rowElement.dataset.team;
    const index = Number(rowElement.dataset.index);
    const field = input.dataset.field;
    if (field === 'is_me') return;

    let value = input.value.trim();
    if (value === '') {
      value = null;
    } else if (field === 'hero_id') {
      value = Number(value);
    } else if (field !== 'player_name' && field !== 'role') {
      // Stat cells: tolerate the thousands separators the game renders.
      const cleaned = value.replace(/[,\s]/g, '');
      value = /^\d+$/.test(cleaned) ? Number(cleaned) : null;
      if (value === null) { toast('Stats must be whole numbers.'); input.value = ''; }
    }

    const row = findRow(team, index);
    row[field] = { value, source: 'manual', origin: 'manual', confidence: 1.0 };
    if (field === 'player_name') row.player_id = null;
    queuePatch({ rows: [{ team, row_index: index, [field]: value }] });
  });

  root.addEventListener('click', async (event) => {
    const toggle = event.target.closest('.me-toggle');
    if (!toggle) return;
    const rowElement = toggle.closest('.roster-row[data-team]');
    const team = rowElement.dataset.team;
    const index = Number(rowElement.dataset.index);
    const row = findRow(team, index);
    const next = !row.is_me;
    payload.rows.forEach((r) => { r.is_me = false; });
    row.is_me = next;
    paintAllRows();
    try { await patch({ rows: [{ team, row_index: index, is_me: next }] }); }
    catch (error) { toast(error.message); }
  });

  // --- keyboard flow ----------------------------------------------------
  //
  // Entering a match by hand is a column of numbers read off a screenshot.
  // Enter walks down a column the way a spreadsheet does, so you can keep your
  // eyes on the scoreboard instead of on the cursor.

  function cellsInColumn(field) {
    return [...root.querySelectorAll(`.roster-row[data-team] [data-field="${field}"]`)];
  }

  function moveWithin(input, delta) {
    const field = input.dataset.field;
    const column = cellsInColumn(field);
    const index = column.indexOf(input);
    const next = column[index + delta];
    if (next) { next.focus(); if (next.select) next.select(); return true; }
    return false;
  }

  function moveToNextColumn(input) {
    const order = ['role', 'hero_id', 'player_name', ...STAT_COLUMNS.map(([f]) => f)];
    const position = order.indexOf(input.dataset.field);
    const nextField = order[position + 1];
    if (!nextField) return false;
    const column = cellsInColumn(nextField);
    if (!column.length) return false;
    column[0].focus();
    if (column[0].select) column[0].select();
    return true;
  }

  root.addEventListener('keydown', (event) => {
    const input = event.target.closest('.roster-row [data-field]');
    if (!input) return;

    if (event.key === 'Enter') {
      event.preventDefault();
      input.dispatchEvent(new Event('change', { bubbles: true }));
      // Down the column; at the bottom, wrap to the top of the next one.
      if (!moveWithin(input, 1)) moveToNextColumn(input);
      return;
    }
    if (event.key === 'ArrowDown' && input.tagName !== 'SELECT') {
      event.preventDefault(); moveWithin(input, 1); return;
    }
    if (event.key === 'ArrowUp' && input.tagName !== 'SELECT') {
      event.preventDefault(); moveWithin(input, -1); return;
    }
    if (event.key === 'Escape') { input.blur(); }
  });

  // Global shortcuts, active only when not typing into a field.
  function typingInAField(target) {
    return target && (target.tagName === 'INPUT' || target.tagName === 'SELECT'
                      || target.tagName === 'TEXTAREA');
  }

  const shortcut = (event) => {
    if (!document.body.contains(root)) {
      document.removeEventListener('keydown', shortcut);
      return;
    }
    if (event.ctrlKey && event.key === 'Enter') {
      event.preventDefault();
      root.querySelector('[data-role="save"]')?.click();
      return;
    }
    if (typingInAField(event.target) || event.ctrlKey || event.altKey || event.metaKey) return;

    const byKey = { '1': 'win', '2': 'loss', '3': 'draw' };
    if (byKey[event.key]) {
      event.preventDefault();
      resultBox.querySelector(`button[data-value="${byKey[event.key]}"]`).click();
    }
  };
  document.addEventListener('keydown', shortcut);

  // --- save -------------------------------------------------------------

  root.querySelector('[data-role="save"]').addEventListener('click', async () => {
    clearTimeout(queuePatch._timer);
    if (pending) {
      const body = pending; pending = null;
      try { await patch(body); } catch (error) { toast(error.message); return; }
    }
    try {
      const result = await api.post(`/api/drafts/${draft.id}/commit`);
      toast(`Saved as match #${result.match_id}`);
      location.hash = '#/matches';
    } catch (error) {
      toast(error.message);
    }
  });

  view().innerHTML = '';
  view().appendChild(node);
  paintResult();
  paintMap();
  paintBans();
  paintAllRows();
  paintAttached();
  renderProblems(draft.problems);
}

// ------------------------------------------------------------- match list

async function renderMatches() {
  const data = await api.get('/api/matches');
  const node = template('matches');
  const body = node.querySelector('[data-role="rows"]');

  node.querySelector('[data-role="summary"]').textContent =
    data.total === 0 ? 'No matches yet.' : `${data.total} match${data.total === 1 ? '' : 'es'} logged.`;

  body.innerHTML = data.matches.map((m) => `
    <tr>
      <td>${m.played_at ? new Date(m.played_at).toLocaleString() : '<span class="muted">—</span>'}</td>
      <td><span class="pill ${m.result}">${m.result}</span></td>
      <td>${m.map_name ? esc(m.map_name) : '<span class="muted">—</span>'}</td>
      <td class="muted">${m.mode || ''}</td>
      <td>${m.my_hero ? esc(m.my_hero) : '<span class="muted">—</span>'}</td>
      <td class="num">${m.my_eliminations ?? ''}</td>
      <td class="num">${m.my_deaths ?? ''}</td>
      <td class="muted">${m.source_count || ''}</td>
      <td><a class="back" href="#/match/${m.id}">view</a></td>
    </tr>`).join('');

  view().innerHTML = '';
  view().appendChild(node);
}

// ------------------------------------------------------------ match detail

async function renderMatch(id) {
  const data = await api.get('/api/matches/' + id);
  const node = template('match');
  const root = node.querySelector('section');   // the fragment empties on append
  const m = data.match;

  node.querySelector('[data-role="title"]').innerHTML =
    `<span class="pill ${m.result}">${m.result}</span> ${esc(m.map_name || 'Unknown map')}`;

  const bits = [
    ['Played', m.played_at ? new Date(m.played_at).toLocaleString() : '—'],
    ['Mode', m.mode || m.map_mode || '—'],
    ['Format', m.team_size ? `${m.team_size}v${m.team_size}` : '—'],
    ['Duration', formatDuration(m.duration_seconds) || '—'],
    ['Rank range', m.rank_low ? `${m.rank_low} – ${m.rank_high || m.rank_low}` : '—'],
  ];
  node.querySelector('[data-role="meta"]').innerHTML =
    bits.map(([k, v]) => `<div>${k} <b>${v}</b></div>`).join('');

  const teams = node.querySelector('[data-role="teams"]');
  teams.innerHTML = ['ally', 'enemy'].map((team) => {
    const rows = data.players.filter((p) => p.team === team);
    if (!rows.length) return '';
    return `
      <h2>${team === 'ally' ? 'Your team' : 'Enemy team'}</h2>
      <table class="data-table">
        <thead><tr><th>Player</th><th>Role</th><th>Hero</th>
          ${STAT_COLUMNS.map(([, l]) => `<th class="num">${l}</th>`).join('')}</tr></thead>
        <tbody>${rows.map((p) => `
          <tr>
            <td>${p.is_me ? '<b>' : ''}${p.display_name
                 ? `<a class="back" href="#/player/${p.player_id}">${esc(p.display_name)}</a>`
                 : '<span class="muted">unidentified</span>'}${p.is_me ? '</b>' : ''}</td>
            <td class="muted">${p.role || p.hero_role || ''}</td>
            <td>${p.hero_name ? esc(p.hero_name) : '<span class="muted">—</span>'}</td>
            ${STAT_COLUMNS.map(([f]) =>
              `<td class="num">${p[f] === null || p[f] === undefined
                 ? '' : p[f].toLocaleString()}</td>`).join('')}
          </tr>`).join('')}</tbody>
      </table>`;
  }).join('');

  node.querySelector('[data-role="bans"]').innerHTML = data.bans.length
    ? `<h2>Bans</h2><p>${data.bans.map((b) => esc(b.hero_name)).join(' · ')}</p>` : '';

  /* The stored path is `data/screenshots/...`, which is not a URL — only
   * /static and /screenshots are mounted, so the old `/${s.file_path}` link
   * 404'd on every match ever committed. The server returns a real one. */
  node.querySelector('[data-role="sources"]').innerHTML = data.sources.length
    ? `<h2>Screenshots</h2>
       <div class="shots">${data.sources.map((s) => {
         const url = screenshotUrl(s);
         const label = s.kind === 'endgame_report' ? 'Endgame report' : 'Tab scoreboard';
         return `
           <figure>
             <a href="${url}" data-lightbox target="_blank" rel="noopener">
               <img src="${url}" alt="${label}" loading="lazy">
             </a>
             <figcaption>${label}<span class="muted"> · ${
               new Date(s.ingested_at).toLocaleDateString()}</span></figcaption>
           </figure>`;
       }).join('')}</div>`
    : '';

  if (m.notes) {
    node.querySelector('[data-role="sources"]').insertAdjacentHTML(
      'beforebegin', `<h2>Notes</h2><div class="note">${esc(m.notes)}</div>`);
  }

  view().innerHTML = '';
  view().appendChild(node);
  wireLightbox(root);
}

/* Full size without leaving the page. <dialog> is native: Escape closes it and
 * the backdrop is free. The <a> underneath still points at the real file, so
 * if any of this ever breaks it degrades to "opens in a new tab". */
function wireLightbox(root) {
  root.addEventListener('click', (event) => {
    const link = event.target.closest('a[data-lightbox]');
    if (!link) return;
    event.preventDefault();
    const box = el('lightbox');
    box.querySelector('img').src = link.getAttribute('href');
    box.showModal();
  });
}

// ------------------------------------------------------- rate presentation

/* A win rate from fewer than five games is not a signal. It renders greyed,
 * and always alongside its sample size, so it can't be mistaken for one. */
const RELIABLE_MINIMUM = 5;

function rateText(entry) {
  if (!entry.games) return '—';
  const pct = Math.round(entry.win_rate * 100) + '%';
  return entry.reliable ? pct : `${pct} <span class="rate-sub">(${entry.games} game${entry.games === 1 ? '' : 's'})</span>`;
}

function rateCard(label, entry, sublabel) {
  const thin = entry.games && !entry.reliable ? ' thin' : '';
  const record = entry.games
    ? `${entry.wins}W ${entry.losses}L${entry.draws ? ' ' + entry.draws + 'D' : ''}`
    : 'no games';
  return `
    <div class="rate-card${thin}">
      <div class="rate-label">${label}</div>
      <div class="rate-value">${rateText(entry)}</div>
      <div class="rate-sub">${record}${sublabel ? ' · ' + sublabel : ''}</div>
    </div>`;
}

function breakdownTable(title, rows, linkFor) {
  if (!rows.length) return '';
  return `
    <div class="breakdown">
      <h2>${title}</h2>
      <table class="data-table">
        <thead><tr><th>${title}</th><th class="num">Games</th><th class="num">W-L</th>
                   <th class="num">Win rate</th></tr></thead>
        <tbody>${rows.map((r) => `
          <tr>
            <td>${linkFor ? `<a class="back" href="${linkFor(r)}">${esc(r.name)}</a>`
                          : esc(r.name)}</td>
            <td class="num">${r.games}</td>
            <td class="num">${r.wins}-${r.losses}</td>
            <td class="num ${r.reliable ? '' : 'thin-rate'}">${rateText(r)}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

const AVERAGE_LABELS = [
  ['eliminations', 'Elims'], ['assists', 'Assists'], ['deaths', 'Deaths'],
  ['damage', 'Damage'], ['healing', 'Healing'], ['mitigation', 'Mitigated'],
];

function averagesTable(averages) {
  if (!averages) return '';
  return `
    <h2>Averages <span class="muted">(${averages.rows_with_stats} scored game${averages.rows_with_stats === 1 ? '' : 's'})</span></h2>
    <table class="data-table">
      <thead><tr>${AVERAGE_LABELS.map(([, l]) => `<th class="num">${l}</th>`).join('')}</tr></thead>
      <tbody><tr>${AVERAGE_LABELS.map(([k]) =>
        `<td class="num">${averages[k] === null ? '—' : Math.round(averages[k]).toLocaleString()}</td>`).join('')}
      </tr></tbody>
    </table>`;
}

// -------------------------------------------------------------------- bars

/* One row: a label, a track with a single fill, and a value.
 *
 * `max` is passed in rather than computed per row so every bar in a group
 * shares one scale. Bars that each normalise to their own maximum are a
 * picture of nothing. */
function bar(label, value, max, { tone = '', text = null, thin = false } = {}) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return `
    <div class="bar-row${thin ? ' thin' : ''}">
      <span class="bar-name" title="${esc(label)}">${esc(label)}</span>
      <span class="bar-track"><span class="bar-fill ${thin ? 'thin' : tone}"
            style="width:${pct.toFixed(1)}%"></span></span>
      <span class="bar-value">${text === null ? value : text}</span>
    </div>`;
}

/* "How much" — scaled to the biggest in the group. */
function countBars(rows, { tone = '' } = {}) {
  if (!rows.length) return '';
  const max = rows.reduce((n, r) => Math.max(n, r.value), 0);
  return `<div class="bars">${rows.map((r) =>
    bar(r.name, r.value, max, { tone, text: r.text ?? r.value })).join('')}</div>`;
}

/* "How good" — always the full 0-100 scale, so a bar means the same thing in
 * every panel. Below five games the fill goes flat grey and the record rides
 * in the value column: the low-sample rule, drawn instead of written. */
function rateBars(rows) {
  if (!rows.length) return '';
  return `<div class="bars">${rows.map((r) => bar(
    r.name, Math.round((r.win_rate || 0) * 100), 100,
    {
      tone: 'win',
      thin: !r.reliable,
      text: (r.games ? Math.round(r.win_rate * 100) + '%' : '&mdash;')
            + ` <span class="rate-sub">${r.wins}-${r.losses}</span>`,
    },
  )).join('')}</div>`;
}

/* Both at once: track length is the sample, the split is the outcome. This is
 * the honest shape for "played most" — a long red bar reads as "a lot, badly"
 * without needing a second chart beside it. */
function recordBars(rows) {
  if (!rows.length) return '';
  const max = rows.reduce((n, r) => Math.max(n, r.games), 0);
  return `<div class="bars">${rows.map((r) => {
    const share = (n) => (r.games ? (n / r.games) * 100 : 0);
    return `
      <div class="bar-row${r.reliable ? '' : ' thin'}">
        <span class="bar-name" title="${esc(r.name)}">${esc(r.name)}</span>
        <span class="bar-track split" style="width:${max ? (r.games / max) * 100 : 0}%">
          <i class="w" style="width:${share(r.wins)}%"></i>
          <i class="l" style="width:${share(r.losses)}%"></i>
          <i class="d" style="width:${share(r.draws)}%"></i>
        </span>
        <span class="bar-value">${r.wins}-${r.losses}${r.draws ? '-' + r.draws : ''}</span>
      </div>`;
  }).join('')}</div>`;
}

const KIND_LABEL = { map: 'map', hero: 'hero', role: 'role', ally: 'with', comp: 'comp' };

// ------------------------------------------------------------- endorsements

/* Two independent sets of three. Set A mirrors the role colours and its codes
 * are literally the role names, so "players I marked as tanks" can be joined
 * against what they actually play. Set B has no meaning the app knows about —
 * the operator decides what pink means and nothing here ever reads it back. */
function endorsementDots(playerId, codes, { interactive = true } = {}) {
  const on = new Set(codes || []);
  const sets = (reference && reference.tags_by_set) || { role: [], free: [] };
  const dot = (tag) =>
    `<button type="button" class="dot dot-${tag.code}${on.has(tag.code) ? ' on' : ''}"
             data-endorse="${tag.code}" title="${esc(tag.label)}"
             aria-label="${esc(tag.label)}" aria-pressed="${on.has(tag.code)}"
             ${interactive ? '' : 'disabled'}></button>`;
  return `<span class="dots" data-player="${playerId}">
      <span class="dot-set">${(sets.role || []).map(dot).join('')}</span>
      <span class="dot-set">${(sets.free || []).map(dot).join('')}</span>
    </span>`;
}

/* One delegated listener per view, so a table of forty players costs one
 * listener and re-rendering the rows beneath it changes nothing.
 *
 * Optimistic: the dot flips now and reverts on failure, because tagging a page
 * of players should not be forty round trips of waiting. The whole set is sent
 * rather than a delta, so the write is idempotent and two fast clicks cannot
 * interleave into a lost update. */
function wireEndorsements(root) {
  root.addEventListener('click', async (event) => {
    const dot = event.target.closest('[data-endorse]');
    if (!dot) return;
    const host = dot.closest('.dots');
    const next = !dot.classList.contains('on');
    dot.classList.toggle('on', next);
    dot.setAttribute('aria-pressed', String(next));
    const tags = [...host.querySelectorAll('.dot.on')].map((d) => d.dataset.endorse);
    try {
      await api.request('PUT', `/api/players/${host.dataset.player}/tags`, { tags });
    } catch (error) {
      dot.classList.toggle('on', !next);
      dot.setAttribute('aria-pressed', String(!next));
      toast(error.message);
    }
  });
}

// -------------------------------------------------------------------- notes

/* A note is invisible until it exists.
 *
 * No note: one muted "+ note" link, and nothing else. Has a note: the text
 * itself, which is also the edit target — click it, blur to save, empty it to
 * delete and the field disappears again. There is never an empty box anywhere,
 * which is the whole requirement. */
function wireNote(host, playerId, initial) {
  let value = (initial || '').trim();

  function paintRead() {
    host.innerHTML = value
      ? `<div class="note" data-role="note-read" title="Click to edit">${esc(value)}</div>`
      : `<a class="back note-add" href="#" data-role="note-add">note</a>`;
  }

  function paintEdit() {
    host.innerHTML = `<textarea class="note-edit" rows="3" data-role="note-edit"
        placeholder="Anything worth remembering. Empty it to remove the note."
        >${esc(value)}</textarea>`;
    const box = host.querySelector('[data-role="note-edit"]');
    box.focus();
    box.selectionStart = box.selectionEnd = box.value.length;
  }

  async function commit(text) {
    const next = text.trim();
    if (next === value) { paintRead(); return; }
    value = next;
    paintRead();
    try { await api.patch('/api/players/' + playerId, { notes: value || null }); }
    catch (error) { toast(error.message); }
  }

  host.addEventListener('click', (event) => {
    if (event.target.closest('[data-role="note-add"]')) { event.preventDefault(); paintEdit(); }
    else if (event.target.closest('[data-role="note-read"]')) paintEdit();
  });

  host.addEventListener('keydown', (event) => {
    const box = event.target.closest('[data-role="note-edit"]');
    if (!box) return;
    if (event.key === 'Escape') { event.preventDefault(); paintRead(); }
    if (event.key === 'Enter' && event.ctrlKey) { event.preventDefault(); box.blur(); }
  });

  host.addEventListener('focusout', (event) => {
    const box = event.target.closest('[data-role="note-edit"]');
    if (box) commit(box.value);
  });

  paintRead();
}

/* In a list there is no add affordance at all — only a marker on rows that
 * already have one. One place to edit keeps the "no empty field" promise
 * trivially true. */
const noteMark = (player) =>
  player.has_notes ? ` <span class="note-mark" title="Has a note">note</span>` : '';

/* The recent-matches table, shared by the player page and the Overall page's
 * player view. `result` is THEIR result; `my_result` rides along so that
 * "you lost, they won" reads as the interesting row it is. */
function recentTable(rows) {
  if (!rows.length) return '';
  return `<h2>Recent matches</h2>
    <table class="data-table">
      <thead><tr><th>Played</th><th>Result</th><th>Map</th><th>Their hero</th>
                 <th>Side</th><th></th></tr></thead>
      <tbody>${rows.map((r) => `
        <tr>
          <td>${r.played_at ? new Date(r.played_at).toLocaleDateString() : '—'}</td>
          <td><span class="pill ${r.result}">${r.result}</span></td>
          <td>${r.map_name ? esc(r.map_name) : '<span class="muted">—</span>'}</td>
          <td>${r.hero_name ? esc(r.hero_name) : '<span class="muted">—</span>'}</td>
          <td class="muted">${r.my_team
              ? (r.their_team === r.my_team ? 'with you' : 'against you') : '—'}</td>
          <td><a class="back" href="#/match/${r.id}">view</a></td>
        </tr>`).join('')}
      </tbody>
    </table>`;
}

// ----------------------------------------------------------------- players

async function renderPlayers() {
  await loadReference();
  const players = await api.get('/api/players');
  const node = template('players');
  const root = node.querySelector('section');
  node.querySelector('[data-role="rows"]').innerHTML = players.length
    ? players.map((p) => `
        <tr>
          <td>${esc(p.display_name)}${noteMark(p)}</td>
          <td>${endorsementDots(p.id, p.tags)}</td>
          <td class="num">${p.games_seen}</td>
          <td class="muted">${new Date(p.first_seen).toLocaleDateString()}</td>
          <td class="muted">${new Date(p.last_seen).toLocaleDateString()}</td>
          <td class="num muted">${p.nameplate_count}</td>
          <td><a class="back" href="#/player/${p.id}">view</a></td>
        </tr>`).join('')
    : '<tr><td colspan="7" class="muted">No players yet.</td></tr>';

  view().innerHTML = '';
  view().appendChild(node);
  wireEndorsements(root);
}

async function renderPlayer(id) {
  await loadReference();
  const data = await api.get('/api/players/' + id);
  const node = template('player');
  const root = node.querySelector('section');

  node.querySelector('[data-role="name"]').textContent = data.player.display_name;
  node.querySelector('[data-role="seen"]').innerHTML = [
    ['Games seen', data.player.games_seen],
    ['First seen', new Date(data.player.first_seen).toLocaleDateString()],
    ['Last seen', new Date(data.player.last_seen).toLocaleDateString()],
  ].map(([k, v]) => `<div>${k} <b>${v}</b></div>`).join('');

  node.querySelector('[data-role="dots"]').innerHTML =
    endorsementDots(data.player.id, data.tags);

  node.querySelector('[data-role="rates"]').innerHTML =
    rateCard('Win rate with them', data.with_me) +
    rateCard('Win rate against them', data.against_me);

  node.querySelector('[data-role="averages"]').innerHTML = averagesTable(data.averages);

  node.querySelector('[data-role="heroes"]').innerHTML = data.heroes.length
    ? `<h2>Most played</h2><p>${data.heroes
        .map((h) => `${esc(h.name)} <span class="muted">×${h.games}</span>`).join(' · ')}</p>`
    : '';

  node.querySelector('[data-role="recent"]').innerHTML = recentTable(data.recent);

  view().innerHTML = '';
  view().appendChild(node);
  wireEndorsements(root);
  wireNote(root.querySelector('[data-role="note"]'), data.player.id, data.player.notes);
}

// ----------------------------------------------------------------- overall

/* Everything derived from the data, on one page.
 *
 * `#/overall` is the operator; `#/overall/{id}` is anyone else. Routing rather
 * than an in-place repaint, so the state is linkable and the back button does
 * what it looks like it does — which is also how every other view here works.
 *
 * The right rail stays global whichever subject is selected: you can inspect a
 * teammate and still see your own top five without scrolling back. */
async function renderOverall(playerId) {
  await loadReference();
  const [data, players, mine] = await Promise.all([
    api.get('/api/stats/overview' + (playerId ? '?player_id=' + playerId : '')),
    api.get('/api/players'),
    playerId ? api.get('/api/stats/top') : null,
  ]);

  const node = template('overall');
  const root = node.querySelector('section');
  const pick = (name) => root.querySelector(`[data-role="${name}"]`);
  const global = mine || data.highlights;

  const panel = (title, body, more) =>
    `<div class="panel"><h2>${title}</h2>${
      body || '<p class="hint">Nothing yet.</p>'}${more || ''}</div>`;
  const more = (label, html) =>
    html ? `<details class="more"><summary>${label}</summary>${html}</details>` : '';
  const named = (rows) => rows.map((r) => ({ ...r, name: r.name ?? String(r.id ?? '—') }));

  // --- the rail: always the operator's, whoever is selected ---------------

  pick('top-rates').innerHTML = global.best.length
    ? rateBars(global.best.map((r) => ({ ...r, name: `${r.name} · ${KIND_LABEL[r.kind]}` })))
    : '<p class="hint">Nothing has five games behind it yet.</p>';

  pick('top-bans').innerHTML = global.most_banned.length
    ? countBars(global.most_banned.map((b) => ({ name: b.name, value: b.games })),
                { tone: 'loss' })
    : '<p class="hint">No bans recorded yet.</p>';

  pick('worst').innerHTML = global.worst.length
    ? rateBars(global.worst.map((r) => ({ ...r, name: `${r.name} · ${KIND_LABEL[r.kind]}` })))
    : '<p class="hint">Not enough games to say.</p>';

  // --- subject picker -----------------------------------------------------

  const select = pick('player-select');
  select.innerHTML = option('', 'Everything · my matches')
    + players.map((p) => option(p.id, `${p.display_name} · ${p.games_seen} seen`,
                                String(p.id) === String(playerId))).join('');
  select.addEventListener('change', () => {
    location.hash = select.value ? `#/overall/${select.value}` : '#/overall';
  });

  // --- head ---------------------------------------------------------------

  const subject = data.subject;
  const isMine = subject.player_id === null;
  pick('heading').textContent = isMine ? 'Overall' : subject.display_name;

  if (isMine) {
    pick('identity').innerHTML = '';
    pick('player-link').hidden = true;
    pick('headline').innerHTML =
      rateCard('All matches', data.overall) +
      rateCard('Current streak', { games: data.streaks.current.length, wins: 0, losses: 0,
                                   draws: 0, win_rate: null, reliable: true },
               data.streaks.current.outcome || 'nothing yet') +
      rateCard('Longest win streak',
               { games: data.streaks.longest_win.length, wins: 0, losses: 0, draws: 0,
                 win_rate: null, reliable: true }, 'in a row');
    // The streak cards want a count, not a percentage.
    pick('headline').querySelectorAll('.rate-card').forEach((card, index) => {
      if (index === 0) return;
      const n = index === 1 ? data.streaks.current.length : data.streaks.longest_win.length;
      card.querySelector('.rate-value').textContent = String(n);
    });
  } else {
    pick('identity').innerHTML = `
      <div class="identity-row">
        <div class="detail-meta">
          <div>Games seen <b>${subject.games_seen ?? '—'}</b></div>
          <div>First seen <b>${subject.first_seen
              ? new Date(subject.first_seen).toLocaleDateString() : '—'}</b></div>
          <div>Last seen <b>${subject.last_seen
              ? new Date(subject.last_seen).toLocaleDateString() : '—'}</b></div>
        </div>
        ${endorsementDots(subject.player_id, subject.tags)}
      </div>`;
    const link = pick('player-link');
    link.href = '#/player/' + subject.player_id;
    link.hidden = false;
    pick('headline').innerHTML = rateCard('Their record', data.overall);
  }

  const totals = data.totals;
  const gaps = [];
  if (totals.matches - totals.matches_with_map)
    gaps.push(`${totals.matches - totals.matches_with_map} with no map`);
  if (totals.matches - totals.matches_with_hero)
    gaps.push(`${totals.matches - totals.matches_with_hero} with no hero`);
  if (totals.matches - totals.matches_with_bans)
    gaps.push(`${totals.matches - totals.matches_with_bans} with no bans typed in`);
  pick('coverage').innerHTML = totals.matches
    ? `${totals.matches} match${totals.matches === 1 ? '' : 'es'}`
      + (gaps.length ? ` · <span class="muted">${gaps.join(' · ')}</span>` : '')
    : 'No matches yet. Log one and this page fills itself in.';

  // --- body ---------------------------------------------------------------

  const wonWith = [...data.by_ally].sort((a, b) => b.wins - a.wins).filter((r) => r.wins);
  const lostWith = [...data.by_ally].sort((a, b) => b.losses - a.losses)
    .filter((r) => r.losses);
  const counts = (rows, key) => rows.map((r) => ({ name: r.name, value: r[key] }));

  pick('sections').innerHTML = `<div class="overall-grid">
    ${panel('Maps played most', recordBars(data.by_map.slice(0, 7)),
       more(`See all ${data.by_map.length} maps`, breakdownTable('Map', data.by_map)))}

    ${panel(isMine ? 'Won with most' : 'They win with',
       countBars(counts(wonWith.slice(0, 7), 'wins'), { tone: 'win' }),
       more('Their win rates alongside', rateBars(wonWith.slice(0, 20))))}

    ${panel(isMine ? 'Lost with most' : 'They lose with',
       countBars(counts(lostWith.slice(0, 7), 'losses'), { tone: 'loss' }),
       more(`See all ${data.by_ally_total} teammates`,
            breakdownTable('Teammate', data.by_ally, (r) => `#/player/${r.id}`)))}

    ${panel(isMine ? 'My heroes' : 'Their heroes', rateBars(data.by_hero.slice(0, 7)),
       more(`See all ${data.by_hero.length} heroes`, breakdownTable('Hero', data.by_hero)))}

    ${panel('By role', recordBars(named(data.by_role)))}

    ${panel('Modes', recordBars(named(data.by_mode)))}

    ${panel('Format', recordBars(named(data.by_team_size)))}

    ${panel('Most-banned heroes',
       countBars(data.bans.heroes.slice(0, 8).map((b) => ({
         name: b.name, value: b.games,
         text: `${b.games} <span class="rate-sub">${
           Math.round((b.ban_rate || 0) * 100)}%</span>`,
       })), { tone: 'loss' }),
       more(`See all ${data.bans.heroes.length} banned heroes`,
            breakdownTable('Banned hero', data.bans.heroes)))}

    ${panel('Comps won with', recordBars(data.comps.shapes.slice(0, 7))
       + (data.comps.unclassified_games
          ? `<p class="coverage">${data.comps.unclassified_games} match${
              data.comps.unclassified_games === 1 ? '' : 'es'} had an incomplete roster
              and could not be shaped.</p>` : ''),
       more('Hero duos and exact lineups',
            '<div data-role="comp-detail"><p class="hint">Loading…</p></div>'))}

    ${panel('Allied heroes', rateBars(data.by_ally_hero.slice(0, 7)),
       more(`See all ${data.by_ally_hero.length}`,
            breakdownTable('Allied hero', data.by_ally_hero)))}

    ${panel('Heroes faced', rateBars(data.by_opponent_hero.slice(0, 7)),
       more(`See all ${data.by_opponent_hero.length}`,
            breakdownTable('Opposing hero', data.by_opponent_hero)))}

    ${panel('Opponents',
       countBars(counts([...data.by_opponent].sort((a, b) => b.games - a.games).slice(0, 7),
                        'games')),
       more(`See all ${data.by_opponent_total}`,
            breakdownTable('Opponent', data.by_opponent, (r) => `#/player/${r.id}`)))}

    ${panel('Averages', averagesTable(data.averages)
       + (data.averages_by_role.length > 1
          ? `<p class="coverage">Averaging healing across a tank game and a support game
             means nothing, so the split is below.</p>`
            + data.averages_by_role.map((a) =>
                `<h2>${a.role}</h2>${averagesTable(a)}`).join('')
          : ''))}

    ${panel('Hero by map', '<p class="hint">Which hero actually works where.</p>',
       more('Show the cross-tab', '<div data-role="crosstab"><p class="hint">Loading…</p></div>'))}
  </div>`;

  view().innerHTML = '';
  view().appendChild(node);
  wireEndorsements(root);
  if (!isMine) {
    wireNote(root.querySelector('.overall-head [data-role="note"]'),
             subject.player_id, subject.notes);
  }

  /* The two heaviest queries load only when their <details> is opened. Nothing
   * else on the page waits for them. */
  lazyDetails(root, '[data-role="comp-detail"]', async (host) => {
    const c = await api.get('/api/stats/comps?exact=true'
      + (playerId ? '&player_id=' + playerId : ''));
    host.innerHTML =
      (c.pairs.length ? breakdownTable('Hero duo', c.pairs) : '')
      + (c.exact && c.exact.length
         ? breakdownTable('Exact lineup', c.exact)
           + '<p class="coverage">Exact lineups rarely repeat, so most of these are'
           + ' a single game. Read them as a record, not a ranking.</p>'
         : '<p class="hint">No complete lineups recorded yet.</p>');
  });

  lazyDetails(root, '[data-role="crosstab"]', async (host) => {
    const x = await api.get('/api/stats/crosstab'
      + (playerId ? '?player_id=' + playerId : ''));
    host.innerHTML = x.cells.length
      ? breakdownTable('Hero on map', x.cells.map((c) => ({
          ...c, name: `${c.hero_name} · ${c.map_name}` })))
      : '<p class="hint">Not enough hero data yet.</p>';
  });
}

/* Fetch once, the first time a <details> is actually opened. */
function lazyDetails(root, selector, load) {
  const host = root.querySelector(selector);
  if (!host) return;
  const details = host.closest('details');
  details.addEventListener('toggle', async () => {
    if (!details.open || details.dataset.loaded) return;
    details.dataset.loaded = '1';
    try { await load(host); }
    catch (error) { host.innerHTML = `<p class="hint">${esc(error.message)}</p>`; }
  });
}

// ------------------------------------------------------------------- stats

async function renderStats() {
  await loadReference();
  const players = await api.get('/api/players');
  const node = template('stats');
  const root = node.querySelector('section');

  function fillSelect(name, options) {
    root.querySelector(`[data-filter="${name}"]`).innerHTML =
      option('', 'Any') + options;
  }

  fillSelect('hero_id', ['tank', 'damage', 'support'].map((role) =>
    `<optgroup label="${role}">` +
    reference.heroes_by_role[role].map((h) => option(h.id, h.name)).join('') +
    '</optgroup>').join(''));
  fillSelect('map_id', Object.entries(reference.maps_by_mode).map(([mode, maps]) =>
    `<optgroup label="${mode}">` + maps.map((m) => option(m.id, m.name)).join('') +
    '</optgroup>').join(''));
  fillSelect('mode', Object.keys(reference.maps_by_mode).map((m) => option(m, m)).join(''));
  fillSelect('teammate_id', players.map((p) => option(p.id, p.display_name)).join(''));
  fillSelect('rank_min', reference.ranks.map((r) => option(r.ordinal, r.name)).join(''));
  fillSelect('rank_max', reference.ranks.map((r) => option(r.ordinal, r.name)).join(''));

  async function refresh() {
    const params = new URLSearchParams();
    for (const control of root.querySelectorAll('[data-filter]')) {
      if (control.value) params.set(control.dataset.filter, control.value);
    }
    const data = await api.get('/api/stats?' + params.toString());

    root.querySelector('[data-role="overall"]').innerHTML =
      rateCard('Overall', data.overall);
    root.querySelector('[data-role="averages"]').innerHTML =
      averagesTable(data.my_averages);
    root.querySelector('[data-role="breakdowns"]').innerHTML =
      breakdownTable('Map', data.by_map) +
      breakdownTable('Mode', data.by_mode.map((r) => ({ ...r, name: r.mode }))) +
      breakdownTable('My hero', data.by_hero) +
      breakdownTable('Teammate', data.by_teammate, (r) => `#/player/${r.id}`) +
      breakdownTable('Opposing hero', data.by_opponent_hero);
  }

  root.addEventListener('change', (event) => {
    if (event.target.closest('[data-filter]')) refresh();
  });
  root.querySelector('[data-role="clear"]').addEventListener('click', () => {
    root.querySelectorAll('[data-filter]').forEach((c) => { c.value = ''; });
    refresh();
  });

  view().innerHTML = '';
  view().appendChild(node);
  await refresh();
}

// ---------------------------------------------------------------- settings

async function renderSettings() {
  const current = await api.get('/api/settings');
  const node = template('settings');
  const form = node.querySelector('[data-role="form"]');

  const fields = [
    ['my_display_name', 'Your display name', 'text',
     'Exactly as it appears on the scoreboard. Used to pre-mark your row.'],
    ['confidence_threshold', 'Confidence threshold', 'number',
     'Extracted values scoring below this are highlighted for review.'],
    ['default_team_size', 'Default team size', 'number', '6 for role/open queue, 5 for 5v5.'],
    ['hero_hash_max_distance', 'Hero match tolerance', 'number',
     'Higher accepts looser portrait matches (0–64).'],
    ['nameplate_hash_max_distance', 'Nameplate match tolerance', 'number',
     'Higher accepts looser nameplate matches (0–64).'],
  ];

  form.innerHTML = fields.map(([key, label, type, hint]) => `
    <label>
      <span class="meta-label">${label}</span>
      <input type="${type}" step="any" name="${key}" value="${current[key] ?? ''}">
      <span class="hint">${hint}</span>
    </label>`).join('');

  node.querySelector('[data-role="save"]').addEventListener('click', async () => {
    const changes = {};
    for (const input of form.querySelectorAll('input')) changes[input.name] = input.value;
    try {
      await api.patch('/api/settings', changes);
      toast('Settings saved.');
    } catch (error) { toast(error.message); }
  });

  const backupList = node.querySelector('[data-role="backups"]');
  async function showBackups() {
    const backups = await api.get('/api/export/backups');
    backupList.innerHTML = backups.length
      ? 'Existing backups: ' + backups.map((b) =>
          `${b.name} <span class="muted">(${Math.round(b.bytes / 1024)} KB)</span>`).join(' · ')
      : 'No backups yet.';
  }

  node.querySelector('[data-role="backup"]').addEventListener('click', async () => {
    try {
      const result = await api.post('/api/export/backup');
      toast(`Backed up to ${result.path}`);
      showBackups();
    } catch (error) { toast(error.message); }
  });

  view().innerHTML = '';
  view().appendChild(node);
  showBackups();
}

// ------------------------------------------------------------------ router

const ROUTES = [
  [/^#\/new\/(\d+)$/, (m) => renderEntry(m[1])],
  [/^#\/new$/, () => renderEntry(null)],
  [/^#\/matches$/, () => renderMatches()],
  [/^#\/match\/(\d+)$/, (m) => renderMatch(m[1])],
  [/^#\/players$/, () => renderPlayers()],
  [/^#\/player\/(\d+)$/, (m) => renderPlayer(m[1])],
  [/^#\/overall\/(\d+)$/, (m) => renderOverall(m[1])],
  [/^#\/overall$/, () => renderOverall(null)],
  // Renamed from #/stats: "Stats" and "Overall" side by side in the nav were
  // indistinguishable. This one is the filtered, exploratory view.
  [/^#\/explore$/, () => renderStats()],
  [/^#\/stats$/, () => renderStats()],
  [/^#\/settings$/, () => renderSettings()],
];

async function route() {
  const hash = location.hash || '#/matches';
  for (const link of document.querySelectorAll('.topbar nav a')) {
    link.classList.toggle('active', hash.startsWith(link.getAttribute('href')));
  }
  for (const [pattern, handler] of ROUTES) {
    const match = hash.match(pattern);
    if (match) {
      try { await handler(match); }
      catch (error) { view().innerHTML = `<div class="panel">${error.message}</div>`; }
      return;
    }
  }
  location.hash = '#/matches';
}

window.addEventListener('hashchange', route);
window.addEventListener('DOMContentLoaded', () => {
  el('status').textContent = 'local · offline';
  route();
});
