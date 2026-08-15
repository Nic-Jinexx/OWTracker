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
    // The hero half of this is worth stating as a number. It is the one library
    // that grows while you use the app, and "18 heroes" tells you the column
    // will mostly fill itself in a way "loaded" never could.
    const heroes = status.heroes_known
      ? `Heroes known: ${status.heroes_known}`
        + (status.heroes_learned ? ` (${status.heroes_learned} learned from your matches)` : '')
        + '. Any hero you pick is remembered for next time.'
      : 'No hero portraits known yet — pick each hero once and it is remembered.';
    if (status.glyph_atlas_ready) {
      extractorNote.textContent =
        `Screenshots are read automatically. ${heroes}`;
    } else {
      extractorNote.innerHTML =
        'The digit atlas is not built, so statistics still need typing in. '
        + `Screenshots are archived with the match. ${heroes}`;
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
        <button type="button" class="chip danger" data-role="remove">Remove</button>
      </figure>`).join('');
  }

  attachedBox.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-role="remove"]');
    if (!button) return;
    const sha = button.closest('figure').dataset.sha;
    // Say what goes and what stays. The distinction is not guessable from a
    // button labelled Remove, and getting it wrong either way is expensive.
    if (!confirm('Remove this screenshot?\n\n'
                 + 'Everything it read is cleared, so a replacement fills the grid '
                 + 'cleanly. Anything you typed yourself is kept.')) return;
    try {
      const response = await api.del(`/api/drafts/${draft.id}/files/${sha}`);
      payload = response.payload;
      paintAttached();
      paintResult(); paintMap(); paintBans(); paintTeamSize(); paintAllRows();
      renderProblems(response.problems);
      showWarnings([]);
      toast('Screenshot removed.');
    } catch (error) { toast(error.message); }
  });

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

  /* --- paste -------------------------------------------------------------
   *
   * Snipping Tool (Win+Shift+S) puts the capture on the clipboard and nowhere
   * else. Without this the only way in is to save it to a file first and then
   * go and find that file; with it, Ctrl+V anywhere on this page is the whole
   * gesture.
   *
   * A pasted image arrives with bytes but no useful name — browsers call every
   * one of them "image.png". The name is invented here rather than left alone
   * because it is load-bearing downstream: the server's `guess_kind` reads it,
   * and the archived file is named from it, so a draft folder full of
   * image.png would be impossible to tell apart afterwards. */

  const PASTE_SUFFIX = {
    'image/png': '.png', 'image/jpeg': '.jpg',
    'image/bmp': '.bmp', 'image/webp': '.webp',
  };

  // Named so `guess_kind` reads it as an endgame report, which is what a paste
  // almost always is. A Tab shot is still correctable on the attached
  // thumbnail, exactly as a dropped one is.
  function namePasted(blob, type, index) {
    const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    return new File([blob], `pasted-${stamp}-${index + 1}${PASTE_SUFFIX[type]}`, { type });
  }

  function pastedImages(event) {
    const images = [];
    for (const item of [...(event.clipboardData?.items || [])]) {
      if (item.kind !== 'file' || !PASTE_SUFFIX[item.type]) continue;
      const blob = item.getAsFile();
      if (blob) images.push(namePasted(blob, item.type, images.length));
    }
    return images;
  }

  /* The button and the keystroke are not the same mechanism, which is why the
   * button is mostly a signpost. Ctrl+V fires a `paste` event that hands the
   * image over with no permission of any kind — but it is invisible, and a
   * feature whose only entrance is an unmentioned shortcut may as well not
   * exist. A click has no event to read, so reading the clipboard from one
   * needs the `clipboard-read` permission.
   *
   * **It must never ask for it.** Measured here: calling `clipboard.read()`
   * without the permission raises Chrome's prompt, and that prompt is a modal
   * that stops the page dead — strictly worse than the keystroke it was trying
   * to save. So the Permissions API is queried first, which answers without
   * prompting, and the clipboard is only read when the answer is already
   * 'granted'. Otherwise the button says the gesture and flags the drop zone,
   * which is all the operator actually needed. */
  const pasteButton = root.querySelector('[data-role="paste"]');
  pasteButton?.addEventListener('click', async () => {
    let granted = false;
    try {
      granted = navigator.clipboard?.read
        && (await navigator.permissions.query({ name: 'clipboard-read' })).state === 'granted';
    } catch { granted = false; }

    if (!granted) {
      toast('Press Ctrl+V now to paste your snip.', 4000);
      dropzone.classList.add('awaiting-paste');
      setTimeout(() => dropzone.classList.remove('awaiting-paste'), 4000);
      return;
    }

    let images = [];
    try {
      for (const item of await navigator.clipboard.read()) {
        const type = item.types.find((t) => PASTE_SUFFIX[t]);
        if (type) images.push(namePasted(await item.getType(type), type, images.length));
      }
    } catch (error) {
      toast(`Could not read the clipboard (${error.name}). Press Ctrl+V instead.`);
      return;
    }
    if (!images.length) {
      toast('No image on the clipboard. Snip one with Win+Shift+S first.');
      return;
    }
    await upload(images);
  });

  async function onPaste(event) {
    // Self-removing. The router has no teardown hook — it just replaces the
    // view's innerHTML — so a listener left behind by a previous draft would
    // otherwise sit on `document` forever, uploading to a draft that is no
    // longer on screen.
    if (!document.body.contains(dropzone)) {
      document.removeEventListener('paste', onPaste);
      return;
    }
    const images = pastedImages(event);
    // No image on the clipboard means this is an ordinary text paste into a
    // name box or the notes field. Leave it entirely alone.
    if (!images.length) return;
    event.preventDefault();
    await upload(images);
  }

  document.addEventListener('paste', onPaste);

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
    /* The portrait is shown whether or not it was recognized, and that is the
     * point of showing it at all. A wrong auto-match is otherwise just a hero
     * name sitting in a dropdown with nothing to check it against — and picking
     * a hero now teaches the matcher, so a confirmation has to be a look. */
    const portrait = row.portrait_crop
      ? `<img class="portrait-crop" src="${screenshotUrl(
           { url: row.portrait_crop_url, path: row.portrait_crop })}"
           alt="portrait" title="Pick the hero once — it fills itself in next time.">`
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
        <div class="hero-cell">${portrait}<select data-field="hero_id">${heroOptions}</select></div>
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
    return ['ally', 'enemy'].every((team) => {
      const elements = [...root.querySelectorAll(
        `[data-role="rows-${team}"] .roster-row[data-team]`)];
      const rows = rowsFor(team);
      if (elements.length !== rows.length) return false;
      /* The crops are baked into the row markup by `rowMarkup`, and they only
       * exist after the first upload — by which time the roster was already
       * built from a blank draft. Comparing row *counts* alone missed that
       * entirely: a 6v6 screenshot dropped on a 6v6 draft leaves the count
       * unchanged, so nothing rebuilt and the crops never appeared at all.
       * `paintRow` cannot cover for it either, since it only assigns values to
       * inputs that already exist. */
      return rows.every((row, index) =>
        !!elements[index].querySelector('.nameplate-crop') === !!row.nameplate_crop
        && !!elements[index].querySelector('.portrait-crop') === !!row.portrait_crop);
    });
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
      const editing = payload.editing_match_id;
      toast(editing ? `Match #${result.match_id} updated.`
                    : `Saved as match #${result.match_id}`);
      location.hash = editing ? `#/match/${result.match_id}` : '#/matches';
    } catch (error) {
      toast(error.message);
    }
  });

  // Editing an existing match rather than entering a new one. Say so loudly:
  // the grid is identical either way, and saving overwrites a real match.
  if (payload.editing_match_id) {
    const save = root.querySelector('[data-role="save"]');
    if (save) save.textContent = 'Save changes';
    root.insertAdjacentHTML('afterbegin', `
      <div class="editing-banner">
        Editing <a href="#/match/${payload.editing_match_id}">match #${
          payload.editing_match_id}</a>. Saving replaces it. Leaving this page
        changes nothing, and the screenshots stay attached either way.
      </div>`);
  }

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
                 : '<span class="muted">no name logged</span>'}${p.is_me ? '</b>' : ''}</td>
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

  node.querySelector('[data-role="edit"]').href = `#/match/${id}/edit`;

  node.querySelector('[data-role="delete"]').addEventListener('click', async () => {
    const what = `${m.result} on ${m.map_name || 'an unknown map'}`;
    if (!confirm(`Delete this match (${what})? The screenshots stay on disk.`)) return;
    try {
      await api.del('/api/matches/' + id);
      toast('Match deleted.');
      location.hash = '#/matches';
    } catch (error) { toast(error.message); }
  });

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
    <div class="table-scroll"><table class="data-table">
      <thead><tr>${AVERAGE_LABELS.map(([, l]) => `<th class="num">${l}</th>`).join('')}</tr></thead>
      <tbody><tr>${AVERAGE_LABELS.map(([k]) =>
        `<td class="num">${averages[k] === null ? '—' : Math.round(averages[k]).toLocaleString()}</td>`).join('')}
      </tr></tbody>
    </table></div>`;
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

  // --- seasons ----------------------------------------------------------
  const seasonList = node.querySelector('[data-role="seasons"]');
  async function showSeasons() {
    const seasons = await api.get('/api/seasons');
    const real = seasons.filter((s) => s.id !== null);
    const unassigned = seasons.find((s) => s.id === null);
    seasonList.innerHTML = `
      <table class="data-table">
        <thead><tr><th>Season</th><th>From</th><th>To</th>
          <th class="num">Matches</th><th></th></tr></thead>
        <tbody>
          ${real.map((s) => `
            <tr>
              <td>${esc(s.name)}</td>
              <td>${s.starts_on}</td>
              <td>${s.ongoing ? '<span class="muted">ongoing</span>' : s.ends_on}</td>
              <td class="num">${s.matches}</td>
              <td><button type="button" class="chip danger" data-season="${s.id}"
                          data-name="${esc(s.name)}">Delete</button></td>
            </tr>`).join('')
          || '<tr><td colspan="5" class="muted">No seasons yet.</td></tr>'}
          ${unassigned ? `<tr><td colspan="3" class="muted">Not in any season</td>
            <td class="num">${unassigned.matches}</td><td></td></tr>` : ''}
        </tbody>
      </table>`;
    for (const button of seasonList.querySelectorAll('button[data-season]')) {
      button.addEventListener('click', async () => {
        if (!confirm(`Delete the season "${button.dataset.name}"? `
                     + 'Its matches are kept and become unassigned.')) return;
        try {
          const result = await api.del('/api/seasons/' + button.dataset.season);
          toast(`Season deleted. ${result.matches_released} matches unassigned.`);
          showSeasons();
        } catch (error) { toast(error.message); }
      });
    }
  }

  node.querySelector('[data-role="season-add"]').addEventListener('click', async () => {
    const body = {
      name: node.querySelector('[data-role="season-name"]').value.trim(),
      starts_on: node.querySelector('[data-role="season-start"]').value,
      ends_on: node.querySelector('[data-role="season-end"]').value,
    };
    try {
      const created = await api.post('/api/seasons', body);
      node.querySelector('[data-role="season-name"]').value = '';
      toast(created.matches_reassigned
        ? `Added ${created.name}. ${created.matches_reassigned} matches refiled.`
        : `Added ${created.name}.`);
      showSeasons();
    } catch (error) { toast(error.message); }
  });

  // --- learned hero portraits -------------------------------------------
  const portraitBox = node.querySelector('[data-role="portraits"]');
  async function showPortraits() {
    const data = await api.get('/api/reference/hero-portraits');
    const chips = data.learned.map((h) => `
      <span class="chip">${esc(h.hero_name)}
        <span class="muted">${h.portraits} view${h.portraits === 1 ? '' : 's'}</span>
        <button type="button" class="link-button" data-forget="${h.hero_id}"
                data-name="${esc(h.hero_name)}" title="Forget this hero's portraits">×</button>
      </span>`).join('');
    portraitBox.innerHTML = `
      <p class="hint">Recognizing ${data.heroes_known} of ${data.heroes_total} heroes
        — ${data.shipped.length} shipped, ${data.learned.length} learned from
        ${data.learned_portraits} portrait${data.learned_portraits === 1 ? '' : 's'}.</p>
      ${data.learned.length
        ? `<div class="portrait-library">${chips}</div>`
        : '<p class="hint">Nothing learned yet. Pick a hero on your next match and it lands here.</p>'}`;
    for (const button of portraitBox.querySelectorAll('button[data-forget]')) {
      button.addEventListener('click', async () => {
        if (!confirm(`Forget the learned portraits for ${button.dataset.name}? `
                     + 'Your saved matches are not affected — only the recognition.')) return;
        try {
          const result = await api.del('/api/reference/hero-portraits/' + button.dataset.forget);
          toast(`Forgot ${result.forgotten} portrait(s) for ${result.hero}.`);
          showPortraits();
        } catch (error) { toast(error.message); }
      });
    }
  }

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

  // Clearing everything asks for the phrase to be typed rather than using a
  // confirm() dialog. A dialog is dismissed by reflex; typing is not.
  const confirmBox = node.querySelector('[data-role="clear-confirm"]');
  const clearResult = node.querySelector('[data-role="clear-result"]');
  node.querySelector('[data-role="clear-data"]').addEventListener('click', async () => {
    try {
      const result = await api.post('/api/export/clear', { confirm: confirmBox.value.trim() });
      confirmBox.value = '';
      const rows = Object.entries(result.cleared)
        .map(([table, n]) => `${n} ${table.replace(/_/g, ' ')}`).join(', ');
      clearResult.innerHTML = result.total_rows
        ? `Deleted ${rows}. Backed up first to <code>${esc(result.backup.path)}</code>. ` +
          `${result.screenshots_left_on_disk} screenshot files are still in ` +
          `<code>${esc(result.screenshots_path)}</code>; delete them by hand if you want them gone.`
        : 'There was nothing to delete.';
      toast(result.total_rows ? `Cleared ${result.total_rows} rows.` : 'Already empty.');
      showBackups();
      showPortraits();
    } catch (error) {
      clearResult.textContent = '';
      toast(error.message);
    }
  });

  view().innerHTML = '';
  view().appendChild(node);
  showBackups();
  showSeasons();
  showPortraits();
}

/* ------------------------------------------------------------------ trends
 *
 * Win rate over time, drawn as inline SVG. No chart library: the app ships
 * offline with no build step, and a line chart is a path element.
 *
 * The dot series are the reason this page exists. A table can already tell you
 * your win rate with a given player; only a timeline shows whether the games
 * with your pink-dotted teammates sit above or below the rest of your history.
 * Dots aggregate by colour, never by person, because the colour is the label
 * the operator invented and the person is incidental to it.
 */

const CHART = {
  width: 900, height: 320,
  pad: { top: 16, right: 96, bottom: 34, left: 44 },
};

// Dashes are not decoration. Several dot colours sit close together by
// definition (red and pink are both red-ish and always will be), so identity
// never rests on hue alone: every series also has its own stroke pattern, a
// legend entry, and a direct label at the right-hand end.
const SERIES_DASH = {
  overall: '', tank: '6 3', damage: '2 4', support: '10 4',
  red: '4 3 1 3', pink: '8 3 2 3', white: '1 4',
};

const TAG_INK = {
  tank: 'var(--accent)', damage: 'var(--ally)', support: 'var(--win)',
  red: 'var(--enemy)', pink: 'var(--pink)', white: 'var(--text)',
};

function trendChart(data, options) {
  const { measure, hidden } = options;
  const { width, height, pad } = CHART;
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const n = Math.max(data.points.length, 2);

  const x = (i) => pad.left + ((i - 1) / (n - 1)) * plotW;
  const y = (v) => pad.top + (1 - v) * plotH;

  const series = [
    { code: 'overall', label: 'All matches', color: 'var(--text)',
      points: data.points, width: 2.5 },
    ...data.series.map((s) => ({
      code: s.code, label: s.label, color: TAG_INK[s.code] || 'var(--muted)',
      points: s.points, width: 2, games: s.games, win_rate: s.win_rate,
    })),
  ].filter((s) => !hidden.has(s.code));

  const line = (points) => points
    .map((p, i) => `${i ? 'L' : 'M'}${x(p.x).toFixed(1)} ${y(p[measure]).toFixed(1)}`)
    .join(' ');

  // Gridlines every 25%, with the 50% line picked out: on a win-rate chart it
  // is the only value that means anything on its own.
  const grid = [0, 0.25, 0.5, 0.75, 1].map((v) => `
    <line class="grid ${v === 0.5 ? 'grid-mid' : ''}"
          x1="${pad.left}" x2="${pad.left + plotW}" y1="${y(v)}" y2="${y(v)}"></line>
    <text class="axis" x="${pad.left - 8}" y="${y(v) + 4}" text-anchor="end">${v * 100}%</text>`
  ).join('');

  // Win/loss ticks along the bottom, so a flat line still shows what happened.
  const ticks = data.points.map((p) => `
    <rect class="tick tick-${p.outcome}" x="${x(p.x) - 2}" y="${pad.top + plotH + 6}"
          width="4" height="6" rx="1"></rect>`).join('');

  const paths = series.map((s) => `
    <path class="series" d="${line(s.points)}" stroke="${s.color}"
          stroke-width="${s.width}" stroke-dasharray="${SERIES_DASH[s.code] || ''}"></path>
    ${s.points.length === 1 ? `<circle cx="${x(s.points[0].x)}"
          cy="${y(s.points[0][measure])}" r="4" fill="${s.color}"></circle>` : ''}`
  ).join('');

  // Direct labels at the right end, so the common case needs no legend lookup.
  //
  // Series that end at similar win rates put their labels on top of each other,
  // and on this chart that is the normal case rather than the unlucky one:
  // every line is a percentage and they cluster around the middle. Six series
  // ending within ten points of each other stacked six labels 5px apart. So
  // the labels are spread to a legible minimum gap, keeping their vertical
  // order, and dropped entirely when there is not enough room for all of them
  // to be honest about which line they belong to. The legend never goes away,
  // so nothing is lost when they do.
  const GAP = 13;
  const wanted = series
    .filter((s) => s.points && s.points.length)
    .map((s) => ({ s, y: y(s.points[s.points.length - 1][measure]) + 4 }))
    .sort((a, b) => a.y - b.y);

  // All or nothing, at their true heights. Nudging them apart was the obvious
  // fix and the wrong one: every line on this chart is a percentage, so they
  // cluster around the middle, and a label moved far enough to be legible ends
  // up level with a *different* series. A label that points at the wrong line
  // is worse than no label, so when they do not fit, none are drawn and the
  // legend carries identity on its own. It is always present.
  const fits = wanted.length <= 4 && wanted.every(
    (entry, i) => i === 0 || entry.y - wanted[i - 1].y >= GAP);
  const labels = !fits ? '' : wanted
    .map(({ s, y: ly }) => `<text class="series-label" x="${pad.left + plotW + 8}"
                  y="${ly.toFixed(1)}" fill="${s.color}">${esc(s.label)}</text>`)
    .join('');

  return `
    <svg viewBox="0 0 ${width} ${height}" class="chart-svg" role="img"
         aria-label="Win rate over ${data.points.length} matches">
      ${grid}${ticks}${paths}${labels}
      <line class="crosshair" data-role="crosshair" y1="${pad.top}"
            y2="${pad.top + plotH}" style="display:none"></line>
      <rect data-role="hit" x="${pad.left}" y="${pad.top}" width="${plotW}"
            height="${plotH}" fill="transparent"></rect>
    </svg>`;
}

async function renderTrends() {
  const node = template('trends');
  // Every element this view touches later must be grabbed now. `node` is a
  // DocumentFragment and appending it empties it, so a querySelector after the
  // append returns null — which is exactly how the page came up blank with
  // "Cannot set properties of null". The rest of the app hits the same trap and
  // handles it the same way; see renderMatch.
  const intro = node.querySelector('[data-role="intro"]');
  const plot = node.querySelector('[data-role="plot"]');
  const legendBox = node.querySelector('[data-role="legend"]');
  const tableBox = node.querySelector('[data-role="table"]');
  const emptyBox = node.querySelector('[data-role="empty"]');
  const tableWrap = node.querySelector('[data-role="table-wrap"]');
  const figure = node.querySelector('[data-role="figure"]');
  const windowSelect = node.querySelector('[data-role="window"]');
  const seasonSelect = node.querySelector('[data-role="season"]');
  const measureSelect = node.querySelector('[data-role="measure"]');

  const hidden = new Set();
  let data = null;

  for (const season of await api.get('/api/seasons')) {
    const option = document.createElement('option');
    option.value = season.id === null ? 'none' : season.id;
    option.textContent = `${season.name} (${season.matches})`;
    seasonSelect.appendChild(option);
  }

  function paint() {
    const measure = measureSelect.value;
    if (!data || !data.points.length) {
      figure.style.display = 'none';
      tableWrap.style.display = 'none';
      emptyBox.textContent = 'No matches yet. Add one and the trend appears here.';
      return;
    }
    figure.style.display = '';
    tableWrap.style.display = '';
    emptyBox.textContent = data.tags_in_use ? '' :
      'No endorsement dots are in play yet. Put a dot on a player from their '
      + 'page and their games appear here as their own line.';

    plot.innerHTML = trendChart(data, { measure, hidden });

    const entries = [{ code: 'overall', label: 'All matches', color: 'var(--text)',
                       games: data.games, win_rate: data.points.length
                         ? data.points[data.points.length - 1].cumulative : null }]
      .concat(data.series.map((s) => ({
        code: s.code, label: s.label, color: TAG_INK[s.code] || 'var(--muted)',
        games: s.games, win_rate: s.win_rate })));

    legendBox.innerHTML = entries.map((e) => `
      <button type="button" class="legend-item ${hidden.has(e.code) ? 'off' : ''}"
              data-series="${e.code}">
        <svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4"
          stroke="${e.color}" stroke-width="2"
          stroke-dasharray="${SERIES_DASH[e.code] || ''}"></line></svg>
        ${esc(e.label)}
        <span class="muted">${e.win_rate === null ? '' :
          `${Math.round(e.win_rate * 100)}% of ${e.games}`}</span>
      </button>`).join('');

    for (const button of legendBox.querySelectorAll('[data-series]')) {
      button.addEventListener('click', () => {
        const code = button.dataset.series;
        hidden.has(code) ? hidden.delete(code) : hidden.add(code);
        paint();
      });
    }

    tableBox.innerHTML = `
      <table class="data-table">
        <thead><tr><th>#</th><th>Played</th><th>Result</th>
          <th class="num">Rolling</th><th class="num">To date</th></tr></thead>
        <tbody>${data.points.map((p) => `
          <tr><td>${p.n}</td>
            <td>${p.played_at ? new Date(p.played_at).toLocaleDateString() : ''}</td>
            <td><span class="pill ${p.outcome}">${p.outcome}</span></td>
            <td class="num">${Math.round(p.rolling * 100)}%</td>
            <td class="num">${Math.round(p.cumulative * 100)}%</td></tr>`).join('')}
        </tbody>
      </table>`;

    wireCrosshair(plot, data, measure, hidden);
  }

  async function load() {
    const params = new URLSearchParams({ window: windowSelect.value });
    if (seasonSelect.value) params.set('season_id', seasonSelect.value);
    data = await api.get('/api/stats/trend?' + params);
    intro.textContent =
      `${data.games} matches, win rate over a rolling window of ${data.window}.`;
    paint();
  }

  for (const control of [windowSelect, seasonSelect]) {
    control.addEventListener('change', load);
  }
  measureSelect.addEventListener('change', paint);

  view().innerHTML = '';
  view().appendChild(node);
  await load();
}

/* A line chart with no hover is a picture. The crosshair snaps to the nearest
 * match rather than the nearest pixel, so the readout always describes a real
 * game. */
function wireCrosshair(plot, data, measure, hidden) {
  const svg = plot.querySelector('svg');
  const hit = svg.querySelector('[data-role="hit"]');
  const crosshair = svg.querySelector('[data-role="crosshair"]');
  const { width, pad } = CHART;
  const plotW = width - pad.left - pad.right;
  const n = Math.max(data.points.length, 2);

  let tip = plot.querySelector('.chart-tip');
  if (!tip) {
    tip = document.createElement('div');
    tip.className = 'chart-tip';
    plot.appendChild(tip);
  }

  function hide() {
    crosshair.style.display = 'none';
    tip.style.display = 'none';
  }

  hit.addEventListener('mouseleave', hide);
  hit.addEventListener('mousemove', (event) => {
    const box = svg.getBoundingClientRect();
    const scale = width / box.width;
    const svgX = (event.clientX - box.left) * scale;
    const index = Math.round(((svgX - pad.left) / plotW) * (n - 1)) + 1;
    const point = data.points[Math.max(0, Math.min(index, data.points.length) - 1)];
    if (!point) return hide();

    const cx = pad.left + ((point.x - 1) / (n - 1)) * plotW;
    crosshair.setAttribute('x1', cx);
    crosshair.setAttribute('x2', cx);
    crosshair.style.display = '';

    const lines = [`<b>Match ${point.n}</b> · ${point.outcome}`];
    if (!hidden.has('overall')) {
      lines.push(`All matches <b>${Math.round(point[measure] * 100)}%</b>`);
    }
    for (const s of data.series) {
      if (hidden.has(s.code)) continue;
      const at = s.points.find((p) => p.x === point.x);
      if (at) lines.push(`${esc(s.label)} <b>${Math.round(at[measure] * 100)}%</b>`);
    }
    tip.innerHTML = lines.join('<br>');
    tip.style.display = 'block';
    tip.style.left = `${Math.min((cx / width) * 100, 78)}%`;
  });
}

/* Editing is not a second entry screen. The match is unpacked back into a
 * draft and handed to the grid that already exists, so there is one place that
 * knows how a match is shaped and one code path that writes one. */
async function reopenMatchForEditing(matchId) {
  try {
    const { draft_id: draftId, reused } = await api.post(`/api/matches/${matchId}/reopen`);
    if (reused) toast('Reopened the edit already in progress for this match.');
    location.hash = '#/new/' + draftId;
  } catch (error) {
    toast(error.message);
    location.hash = '#/match/' + matchId;
  }
}

// ------------------------------------------------------------------ router

const ROUTES = [
  [/^#\/new\/(\d+)$/, (m) => renderEntry(m[1])],
  [/^#\/new$/, () => renderEntry(null)],
  [/^#\/matches$/, () => renderMatches()],
  [/^#\/trends$/, () => renderTrends()],
  [/^#\/match\/(\d+)\/edit$/, (m) => reopenMatchForEditing(m[1])],
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
