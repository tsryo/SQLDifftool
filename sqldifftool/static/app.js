/* SQL Difftool UI. Talks to the local API, or runs offline from an embedded window.__REPORT__. */
(function () {
  'use strict';

  const REPORT = window.__REPORT__ || null;
  const CATEGORIES = ['Table', 'View', 'Procedure', 'Function', 'Trigger', 'Type', 'Sequence', 'Synonym', 'Schema'];
  const CATEGORY_TITLES = {
    Table: 'Tables', View: 'Views', Procedure: 'Stored procedures', Function: 'Functions', Trigger: 'Triggers',
    Type: 'Types', Sequence: 'Sequences', Synonym: 'Synonyms', Schema: 'Schemas',
  };
  const OPTIONS = [
    { key: 'ignore_whitespace', short: 'Whitespace', long: 'Ignore whitespace',
      hint: 'Indentation, spacing, line breaks, tabs vs spaces', def: true },
    { key: 'ignore_system_names', short: 'System names', long: 'Ignore system-generated constraint names',
      hint: 'Auto-names like DF__Orders__Statu__3B75D760 differ per environment', def: true },
    { key: 'ignore_case', short: 'Case', long: 'Ignore case', hint: 'Upper/lower case outside string literals', def: false },
    { key: 'ignore_comments', short: 'Comments', long: 'Ignore comments', hint: '-- line and /* block */ comments', def: false },
  ];
  const STATUSES = ['different', 'only_left', 'only_right', 'identical'];
  const CONTEXT = 3;
  const STORAGE_KEY = 'sqldifftool.form.v1';

  const state = {
    cmp: null,
    byKey: new Map(),
    selected: null,
    detail: null,
    detailSeq: 0,
    view: 'split',
    expandAll: false,
    expanded: new Set(),
    filters: { different: true, only_left: true, only_right: true, identical: false },
    search: '',
    collapsed: new Set(),
    profiles: [],
    files: { left: [], right: [] },
  };

  // ------------------------------------------------------------------ utils

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ESC[c]);

  async function api(method, url, body) {
    const form = body instanceof FormData;
    const res = await fetch(url, {
      method,
      headers: body === undefined || form ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined || form ? body : JSON.stringify(body),
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* not JSON */ }
    if (!res.ok) {
      const err = new Error((data && data.error) || `${res.status} ${res.statusText}`);
      err.data = data;
      throw err;
    }
    return data;
  }

  let toastTimer = null;
  function toast(message, kind) {
    const el = $('#toast');
    el.textContent = message;
    el.className = 'toast' + (kind === 'error' ? ' error' : '');
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, kind === 'error' ? 8000 : 2600);
  }

  let busyTimer = null;
  function showBusy(text) {
    const started = Date.now();
    $('#busy-text').textContent = text;
    $('#busy-time').textContent = '';
    $('#busy').hidden = false;
    busyTimer = setInterval(() => {
      $('#busy-time').textContent = `${Math.round((Date.now() - started) / 1000)} s`;
    }, 500);
  }
  function hideBusy() { clearInterval(busyTimer); $('#busy').hidden = true; }

  function showView(name) {
    $('#connect-view').hidden = name !== 'connect';
    $('#diff-view').hidden = name !== 'diff';
    document.body.dataset.view = name;
  }

  function storageGet() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null'); } catch (e) { return null; }
  }
  function storageSet(value) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(value)); } catch (e) { /* storage unavailable */ }
  }

  function writeHash() {
    const p = new URLSearchParams();
    if (state.cmp && !REPORT) p.set('compare', state.cmp.id);
    if (state.selected) p.set('obj', state.selected);
    if (state.view !== 'split') p.set('view', state.view);
    try { history.replaceState(null, '', '#' + p.toString()); } catch (e) { /* e.g. sandboxed */ }
  }

  async function copyText(text, what) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    toast(`Copied ${what} to the clipboard`);
  }

  function formatTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  }

  const leftLabel = () => (state.cmp ? state.cmp.left.label : 'left');
  const rightLabel = () => (state.cmp ? state.cmp.right.label : 'right');
  function statusText(status) {
    return {
      different: 'Different',
      only_left: `Only in ${leftLabel()}`,
      only_right: `Only in ${rightLabel()}`,
      identical: 'Identical',
    }[status];
  }

  // ------------------------------------------------------------ connect view

  function buildSideCard(side) {
    const card = $(`#card-${side}`);
    card.appendChild($('#side-template').content.cloneNode(true));
    const list = $('datalist', card);
    list.id = `dbs-${side}`;
    $('[data-f=database]', card).setAttribute('list', list.id);
    $('.side-hint', card).textContent = side === 'left' ? 'left side' : 'right side';
    $$('[data-auth]', card).forEach(b => b.addEventListener('click', () => { setAuth(side, b.dataset.auth); saveForm(); }));
    $$('[data-source]', card).forEach(b => b.addEventListener('click', () => { setSource(side, b.dataset.source); saveForm(); }));
    $$('[data-pick]', card).forEach(input => input.addEventListener('change', () => {
      const picked = Array.from(input.files).filter(f => /\.sql$/i.test(f.name));
      if (input.files.length && !picked.length) toast('No .sql files in the selection', 'error');
      state.files[side] = picked;
      input.value = '';
      renderFiles(side);
    }));
    $('[data-act=clear-files]', card).addEventListener('click', () => { state.files[side] = []; renderFiles(side); });
    $('[data-act=test]', card).addEventListener('click', () => testConnection(side));
    $('[data-act=dbs]', card).addEventListener('click', () => loadDatabases(side));
    card.addEventListener('input', saveForm);
    card.addEventListener('change', saveForm);
    card.addEventListener('submit', e => e.preventDefault());
  }

  function setAuth(side, auth) {
    const card = $(`#card-${side}`);
    card.dataset.auth = auth;
    $$('[data-auth]', card).forEach(b => b.setAttribute('aria-pressed', String(b.dataset.auth === auth)));
  }

  function setSource(side, source) {
    const card = $(`#card-${side}`);
    card.dataset.source = source;
    $$('[data-source]', card).forEach(b => b.setAttribute('aria-pressed', String(b.dataset.source === source)));
  }

  const fileName = f => f.webkitRelativePath || f.name;

  function renderFiles(side) {
    const files = state.files[side];
    const out = $(`#card-${side} .file-summary`);
    $(`#card-${side} .src-files .test-result`).textContent = '';
    if (!files.length) {
      out.textContent = 'No files chosen.';
      return;
    }
    const size = files.reduce((n, f) => n + f.size, 0);
    const kb = size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} KB` : `${(size / 1048576).toFixed(1)} MB`;
    const names = files.map(fileName).sort();
    const shown = names.slice(0, 5).map(n => `<li>${esc(n)}</li>`).join('');
    out.innerHTML = `${files.length} .sql file${files.length === 1 ? '' : 's'} &middot; ${kb}` +
      `<ul>${shown}${names.length > 5 ? `<li>… and ${names.length - 5} more</li>` : ''}</ul>`;
  }

  const resultEl = side => $(`#card-${side} .src-${$(`#card-${side}`).dataset.source === 'files' ? 'files' : 'db'} .test-result`);

  function readSide(side) {
    const card = $(`#card-${side}`);
    const spec = { auth: card.dataset.auth || 'windows', source: card.dataset.source || 'db' };
    $$('[data-f]', card).forEach(el => {
      const f = el.dataset.f;
      spec[f] = el.type === 'checkbox' ? el.checked : (f === 'password' ? el.value : el.value.trim());
    });
    if (!spec.label) spec.label = side === 'left' ? 'Left' : 'Right';
    return spec;
  }

  function writeSide(side, spec) {
    const card = $(`#card-${side}`);
    spec = spec || {};
    $$('[data-f]', card).forEach(el => {
      const f = el.dataset.f;
      if (el.type === 'checkbox') el.checked = !!spec[f];
      else el.value = spec[f] == null ? '' : spec[f];
    });
    if (!$('[data-f=label]', card).value) $('[data-f=label]', card).value = side === 'left' ? 'Left' : 'Right';
    setAuth(side, spec.auth || 'windows');
    setSource(side, spec.source === 'files' ? 'files' : 'db');
    $$('.test-result', card).forEach(el => { el.textContent = ''; el.className = 'test-result'; });
    renderFiles(side);
  }

  function buildOptionCheckboxes() {
    $('#connect-options').innerHTML = OPTIONS.map(o => `
      <label class="option">
        <input type="checkbox" id="opt-${o.key}" ${o.def ? 'checked' : ''}>
        <span><b>${esc(o.long)}</b>${esc(o.hint)}</span>
      </label>`).join('');
    $('#connect-options').addEventListener('change', saveForm);
  }

  function readOptions() {
    const out = {};
    OPTIONS.forEach(o => { out[o.key] = $(`#opt-${o.key}`).checked; });
    return out;
  }

  function writeOptions(opts) {
    OPTIONS.forEach(o => { $(`#opt-${o.key}`).checked = opts && o.key in opts ? !!opts[o.key] : o.def; });
  }

  const readExcludes = () => $('#excludes').value.split(/[,;\n]/).map(s => s.trim()).filter(Boolean);

  function withoutPassword(spec) {
    const copy = Object.assign({}, spec);
    delete copy.password;
    return copy;
  }

  function saveForm() {
    storageSet({
      left: withoutPassword(readSide('left')),
      right: withoutPassword(readSide('right')),
      options: readOptions(),
      excludes: $('#excludes').value,
      profile: $('#profile-select').value,
    });
  }

  function restoreForm() {
    const saved = storageGet() || {};
    writeSide('left', saved.left || { label: 'Left' });
    writeSide('right', saved.right || { label: 'Right' });
    writeOptions(saved.options);
    $('#excludes').value = saved.excludes || '';
    return saved;
  }

  async function testConnection(side) {
    const out = $(`#card-${side} .test-result`);
    out.className = 'test-result pending';
    out.textContent = 'Connecting…';
    try {
      const r = await api('POST', '/api/test-connection', readSide(side));
      out.className = 'test-result ok';
      out.innerHTML = `&#10003; ${esc(r.version)}<br>${esc(r.server)} / ${esc(r.database)} &middot; ` +
        `${r.objectCount} objects &middot; compat ${esc(r.compatLevel)}` +
        (r.warnings || []).map(w => `<span class="warn-line">&#9888; ${esc(w.text)}</span>`).join('');
    } catch (e) {
      out.className = 'test-result err';
      out.textContent = e.message;
    }
  }

  async function loadDatabases(side) {
    const card = $(`#card-${side}`);
    const out = $('.test-result', card);
    out.className = 'test-result pending';
    out.textContent = 'Listing databases…';
    try {
      const r = await api('POST', '/api/databases', readSide(side));
      $('datalist', card).innerHTML = r.databases.map(d => `<option value="${esc(d)}">`).join('');
      out.className = 'test-result ok';
      out.textContent = `${r.databases.length} databases found. Pick one in the Database field.`;
      $('[data-f=database]', card).focus();
    } catch (e) {
      out.className = 'test-result err';
      out.textContent = e.message;
    }
  }

  function swapSides() {
    const l = readSide('left');
    const r = readSide('right');
    state.files = { left: state.files.right, right: state.files.left };
    writeSide('left', r);
    writeSide('right', l);
    saveForm();
  }

  async function runCompare() {
    const body = {
      left: readSide('left'),
      right: readSide('right'),
      options: readOptions(),
      excludes: readExcludes(),
    };
    $$('.test-result').forEach(el => { el.textContent = ''; el.className = 'test-result'; });
    let request = body;
    if (body.left.source === 'files' || body.right.source === 'files') {
      request = new FormData();
      request.append('payload', JSON.stringify(body));
      ['left', 'right'].forEach(side => {
        if (body[side].source === 'files') state.files[side].forEach(f => request.append(`${side}_files`, f, fileName(f)));
      });
    }
    showBusy(`Reading schemas from ${body.left.label} and ${body.right.label}…`);
    try {
      const cmp = await api('POST', '/api/compare', request);
      openComparison(cmp);
    } catch (e) {
      const sideErrors = (e.data && e.data.sideErrors) || {};
      Object.keys(sideErrors).forEach(side => {
        const out = resultEl(side);
        out.className = 'test-result err';
        out.textContent = sideErrors[side];
      });
      toast(e.message, 'error');
    } finally {
      hideBusy();
    }
  }

  // ---------------------------------------------------------------- profiles

  function renderProfiles(selected) {
    const sel = $('#profile-select');
    sel.innerHTML = '<option value="">(none)</option>' +
      state.profiles.map(p => `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join('');
    sel.value = state.profiles.some(p => p.name === selected) ? selected : '';
    $('#profile-delete').disabled = !sel.value;
  }

  async function loadProfiles(selected) {
    try {
      state.profiles = (await api('GET', '/api/profiles')).profiles;
    } catch (e) {
      state.profiles = [];
    }
    renderProfiles(selected);
  }

  function applyProfile(name) {
    const p = state.profiles.find(x => x.name === name);
    $('#profile-delete').disabled = !p;
    if (!p) { saveForm(); return; }
    writeSide('left', p.left);
    writeSide('right', p.right);
    writeOptions(p.options);
    $('#excludes').value = (p.excludes || []).join(', ');
    saveForm();
  }

  async function saveProfile() {
    const l = readSide('left');
    const r = readSide('right');
    const current = $('#profile-select').value;
    const name = prompt('Save these connections as profile (passwords are not stored):',
      current || `${l.database || 'db'}: ${l.label} vs ${r.label}`);
    if (!name || !name.trim()) return;
    try {
      state.profiles = (await api('POST', '/api/profiles', {
        name: name.trim(), left: l, right: r, options: readOptions(), excludes: readExcludes(),
      })).profiles;
      renderProfiles(name.trim());
      saveForm();
      toast('Profile saved');
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  async function deleteProfile() {
    const name = $('#profile-select').value;
    if (!name || !confirm(`Delete profile "${name}"?`)) return;
    try {
      state.profiles = (await api('DELETE', `/api/profiles/${encodeURIComponent(name)}`)).profiles;
      renderProfiles('');
      saveForm();
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  // --------------------------------------------------------------- diff view

  function openComparison(cmp, preferredKey) {
    const previous = state.selected;
    state.cmp = cmp;
    state.byKey = new Map(cmp.objects.map(o => [o.key, o]));
    showView('diff');
    renderHeader();
    renderSummary();
    renderWarnings();
    renderTree();
    const want = preferredKey || previous;
    if (want && state.byKey.has(want)) {
      selectObject(want);
    } else {
      const first = filteredObjects().find(o => o.status !== 'identical') || filteredObjects()[0];
      if (first) selectObject(first.key);
      else { state.selected = null; renderEmptyDetail(); writeHash(); }
    }
  }

  function envHtml(side) {
    return `<span class="env-label">${esc(side.label)}</span>` +
      `<span class="env-loc" title="${esc(side.server)} / ${esc(side.database)}">${esc(side.server)} / ${esc(side.database)}</span>` +
      `<span class="env-ver" title="${esc(side.version)} ${esc(side.edition)}">${esc(side.version)}${side.edition ? ' &middot; ' + esc(side.edition) : ''}</span>`;
  }

  function renderHeader() {
    const c = state.cmp;
    $('#env-left').innerHTML = envHtml(c.left);
    $('#env-right').innerHTML = envHtml(c.right);
    $('#compared-at').textContent = `Compared ${formatTime(c.createdAt)}`;
    if (!REPORT) $('#download-report').href = `/api/compare/${encodeURIComponent(c.id)}/report`;
    document.title = `${c.left.label} vs ${c.right.label} · ${c.left.database} · SQL Difftool`;
  }

  function renderSummary() {
    const c = state.cmp;
    $('#status-chips').innerHTML = STATUSES.map(s =>
      `<button type="button" class="chip st-${s}" data-status="${s}" aria-pressed="${state.filters[s]}"
         title="Show or hide objects that are ${esc(statusText(s).toLowerCase())}">
         <span class="ico"></span><b>${c.summary[s]}</b> ${esc(statusText(s))}</button>`).join('');

    $('#diff-options').innerHTML = '<span>Ignore</span>' + OPTIONS.map(o =>
      `<button type="button" class="pill-toggle" data-opt="${o.key}" aria-pressed="${!!c.options[o.key]}"
         title="${esc(REPORT ? 'Options are fixed in a saved report' : o.long + ': ' + o.hint)}"
         ${REPORT ? 'disabled' : ''}>${esc(o.short)}</button>`).join('');

    const props = c.dbProperties.filter(p => p.significant);
    const differing = props.filter(p => !p.equal);
    $('#db-props').innerHTML = differing.length
      ? differing.map(p => `<span class="prop diff" title="Database property differs"><span class="k">${esc(p.name)}</span>` +
          `<span class="va">${esc(p.left)}</span> vs <span class="vb">${esc(p.right)}</span></span>`).join('')
      : `<span class="prop" title="${props.map(p => esc(p.name) + ': ' + esc(p.left)).join(' · ')}">` +
          `&#10003; ${props.map(p => esc(p.name.toLowerCase())).join(' and ')} match</span>`;
  }

  function renderWarnings() {
    const c = state.cmp;
    const byText = new Map();  // the same message on both sides is shown once
    [['left', c.left.label], ['right', c.right.label]].forEach(([side, label]) => {
      (c.warnings[side] || []).forEach(w => {
        const seen = byText.get(w.text);
        if (seen) seen.labels.push(label);
        else byText.set(w.text, { level: w.level, labels: [label] });
      });
    });
    const items = [];
    byText.forEach((w, text) => items.push(
      `<div class="banner ${w.level === 'warn' ? 'warn' : 'info'}"><b>${esc(w.labels.join(' and '))}:</b>${esc(text)}</div>`));
    if (c.excludes && c.excludes.length) {
      items.push(`<div class="banner info"><b>Excluded:</b>${esc(c.excludes.join(', '))}</div>`);
    }
    $('#warnings').innerHTML = items.join('');
  }

  function filteredObjects() {
    const q = state.search.toLowerCase();
    return state.cmp.objects.filter(o => state.filters[o.status] &&
      (!q || `${o.schema}.${o.name}`.toLowerCase().includes(q) || (o.parent || '').toLowerCase().includes(q)));
  }

  function renderTree() {
    const groups = new Map();
    CATEGORIES.forEach(c => groups.set(c, []));
    filteredObjects().forEach(o => {
      if (!groups.has(o.category)) groups.set(o.category, []);
      groups.get(o.category).push(o);
    });
    let html = '';
    groups.forEach((items, cat) => {
      if (!items.length) return;
      const collapsed = state.collapsed.has(cat);
      html += `<div class="group${collapsed ? ' collapsed' : ''}">
        <button type="button" class="group-head" data-cat="${esc(cat)}" aria-expanded="${!collapsed}">
          <span class="caret">&#9660;</span>${esc(CATEGORY_TITLES[cat] || cat)}<span class="count">${items.length}</span>
        </button><div class="group-items">`;
      items.forEach(o => {
        const counts = o.status === 'different'
          ? `<span class="cnt"><i class="a" title="Changed lines in ${esc(leftLabel())}">${o.leftChanged}</i>` +
            `<i class="b" title="Changed lines in ${esc(rightLabel())}">${o.rightChanged}</i></span>`
          : '';
        const title = `${o.schema ? o.schema + '.' : ''}${o.name}${o.parent ? ' (on ' + o.parent + ')' : ''} · ${statusText(o.status)}`;
        html += `<button type="button" class="item st-${o.status}${o.key === state.selected ? ' active' : ''}"
            data-key="${esc(o.key)}" title="${esc(title)}"><span class="ico"></span>
            <span class="nm">${o.schema ? `<span class="sch">${esc(o.schema)}.</span>` : ''}${esc(o.name)}</span>${counts}</button>`;
      });
      html += '</div></div>';
    });
    $('#tree').innerHTML = html || '<div class="empty-tree">No objects match the current filters.</div>';
  }

  function markActive() {
    $$('#tree .item.active').forEach(el => el.classList.remove('active'));
    const el = $$('#tree .item').find(x => x.dataset.key === state.selected);
    if (el) {
      el.classList.add('active');
      el.scrollIntoView({ block: 'nearest' });
    }
  }

  async function selectObject(key) {
    const summary = state.byKey.get(key);
    if (!summary) return;
    if (state.collapsed.delete(summary.category)) renderTree();
    state.selected = key;
    state.expanded.clear();
    markActive();
    writeHash();
    const seq = ++state.detailSeq;
    let detail = null;
    if (REPORT) {
      detail = REPORT.details[key] || null;
    } else {
      $('#detail').classList.add('loading');
      try {
        detail = await api('GET', `/api/compare/${encodeURIComponent(state.cmp.id)}/object?key=${encodeURIComponent(key)}`);
      } catch (e) {
        if (seq === state.detailSeq) toast(e.message, 'error');
      }
    }
    if (seq !== state.detailSeq) return;
    $('#detail').classList.remove('loading');
    state.detail = Object.assign({}, summary, detail || { rows: null, leftText: null, rightText: null });
    renderDetail();
  }

  function renderEmptyDetail() {
    $('#detail').innerHTML = `<div class="empty-detail"><b>Nothing to show</b>` +
      `No objects match the current filters. Use the status chips above to show more.</div>`;
  }

  function renderDetail() {
    const d = state.detail;
    const L = leftLabel();
    const R = rightLabel();
    const single = d.status === 'only_left' || d.status === 'only_right';
    let stats = '';
    if (d.status === 'different') {
      stats = `<span class="change-stats"><span class="a">${d.leftChanged}</span> changed line${d.leftChanged === 1 ? '' : 's'} in ${esc(L)} &middot; ` +
        `<span class="b">${d.rightChanged}</span> in ${esc(R)}</span>`;
    } else if (single) {
      const n = d.status === 'only_left' ? d.leftChanged : d.rightChanged;
      stats = `<span class="change-stats">${n} line${n === 1 ? '' : 's'}</span>`;
    }
    const notes = [];
    if (d.leftNote) notes.push(`<div class="banner info"><b>${esc(L)}:</b>${esc(d.leftNote)}</div>`);
    if (d.rightNote && d.rightNote !== d.leftNote) notes.push(`<div class="banner info"><b>${esc(R)}:</b>${esc(d.rightNote)}</div>`);

    $('#detail').innerHTML = `
      <div class="detail-head">
        <div class="detail-title">
          <span class="cat-badge">${esc(d.category)}</span>
          <h2>${d.schema ? `<span class="sch">${esc(d.schema)}.</span>` : ''}${esc(d.name)}</h2>
          ${d.parent ? `<span class="parent">on ${esc(d.parent)}</span>` : ''}
        </div>
        <span class="status-pill st-${d.status}"><span class="ico"></span>${esc(statusText(d.status))}</span>
        ${stats}
        <div class="detail-tools">
          <div class="segmented" role="group" aria-label="Diff layout">
            <button type="button" data-view="split" aria-pressed="${state.view === 'split'}" ${single ? 'disabled' : ''}>Side by side</button>
            <button type="button" data-view="unified" aria-pressed="${state.view === 'unified'}" ${single ? 'disabled' : ''}>Unified</button>
          </div>
          <button type="button" class="btn small" data-act="expand" ${single || !d.rows ? 'disabled' : ''}
            title="Show or hide unchanged lines (e)">${state.expandAll ? 'Collapse unchanged' : 'Show all lines'}</button>
          <button type="button" class="btn small" data-act="copy-left" ${d.leftText == null ? 'disabled' : ''}>Copy ${esc(L)}</button>
          <button type="button" class="btn small" data-act="copy-right" ${d.rightText == null ? 'disabled' : ''}>Copy ${esc(R)}</button>
        </div>
      </div>
      <div class="notes">${notes.join('')}</div>
      <div class="diff-wrap" id="diff-host"></div>`;
    renderDiffBody();
  }

  function sideLines(rows, numKey, textKey) {
    const out = [];
    rows.forEach(r => { if (r[numKey]) out[r[numKey] - 1] = r[textKey]; });
    return out;
  }

  function lineHtml(text, runs, segs) {
    if (!text) return '';
    const n = text.length;
    const cls = new Array(n).fill('');
    (runs || []).forEach(([s, e, c]) => { for (let i = s; i < e && i < n; i++) cls[i] = c; });
    const chg = new Uint8Array(n);
    if (segs) {
      let p = 0;
      segs.forEach(([t, changed]) => { if (changed) chg.fill(1, p, Math.min(n, p + t.length)); p += t.length; });
    }
    let out = '';
    let i = 0;
    while (i < n) {
      let j = i + 1;
      while (j < n && cls[j] === cls[i] && chg[j] === chg[i]) j++;
      let piece = esc(text.slice(i, j));
      if (cls[i]) piece = `<span class="${cls[i]}">${piece}</span>`;
      if (chg[i]) piece = `<mark>${piece}</mark>`;
      out += piece;
      i = j;
    }
    return out;
  }

  // Collapse long runs of unchanged lines into clickable gaps.
  function buildItems(rows) {
    const hasChanges = rows.some(r => r.t !== 'e');
    if (!hasChanges || state.expandAll) return rows.map(row => ({ row }));
    const items = [];
    let i = 0;
    while (i < rows.length) {
      if (rows[i].t !== 'e') { items.push({ row: rows[i] }); i++; continue; }
      let j = i;
      while (j < rows.length && rows[j].t === 'e') j++;
      const head = i === 0 ? 0 : CONTEXT;
      const tail = j === rows.length ? 0 : CONTEXT;
      const hidden = j - i - head - tail;
      if (hidden > 2 && !state.expanded.has(i)) {
        for (let k = i; k < i + head; k++) items.push({ row: rows[k] });
        items.push({ gap: true, start: i, count: hidden });
        for (let k = j - tail; k < j; k++) items.push({ row: rows[k] });
      } else {
        for (let k = i; k < j; k++) items.push({ row: rows[k] });
      }
      i = j;
    }
    return items;
  }

  const gapRow = (item, cols) =>
    `<tr class="gap" data-start="${item.start}"><td colspan="${cols}">&#8597; ${item.count} unchanged line${item.count === 1 ? '' : 's'}</td></tr>`;

  function sideHeader(side, cls, colspan) {
    return `<th colspan="${colspan}" class="${cls}"><span class="dot"></span>${esc(side.label)} &middot; ${esc(side.server)} / ${esc(side.database)}</th>`;
  }

  function splitTable(items, hlL, hlR) {
    const c = state.cmp;
    let body = '';
    items.forEach(item => {
      if (item.gap) { body += gapRow(item, 4); return; }
      const r = item.row;
      const lc = r.t === 'c' || r.t === 'l' ? ' ca' : '';
      const rc = r.t === 'c' || r.t === 'r' ? ' cb' : '';
      const left = r.l
        ? `<td class="ln${lc}">${r.l}</td><td class="code${lc}">${lineHtml(r.lt, hlL[r.l - 1], r.ls)}</td>`
        : '<td class="ln none"></td><td class="code none"></td>';
      const right = r.r
        ? `<td class="ln rb${rc}">${r.r}</td><td class="code${rc}">${lineHtml(r.rt, hlR[r.r - 1], r.rs)}</td>`
        : '<td class="ln rb none"></td><td class="code none"></td>';
      body += `<tr>${left}${right}</tr>`;
    });
    return `<table class="diff split">
      <colgroup><col class="ln"><col><col class="ln"><col></colgroup>
      <thead><tr>${sideHeader(c.left, 'ha', 2)}${sideHeader(c.right, 'hb', 2)}</tr></thead>
      <tbody>${body}</tbody></table>`;
  }

  function unifiedTable(items, hlL, hlR) {
    const c = state.cmp;
    let body = '';
    let block = [];
    const line = (l, r, mark, cls, text, runs, segs) =>
      `<tr><td class="ln${cls}">${l || ''}</td><td class="ln${cls}">${r || ''}</td>` +
      `<td class="mk${cls}">${mark}</td><td class="code${cls}">${lineHtml(text, runs, segs)}</td></tr>`;
    const flush = () => {
      block.forEach(r => { if (r.l) body += line(r.l, '', '&lsaquo;', ' ca', r.lt, hlL[r.l - 1], r.ls); });
      block.forEach(r => { if (r.r) body += line('', r.r, '&rsaquo;', ' cb', r.rt, hlR[r.r - 1], r.rs); });
      block = [];
    };
    items.forEach(item => {
      if (item.gap) { flush(); body += gapRow(item, 4); return; }
      const r = item.row;
      if (r.t !== 'e') { block.push(r); return; }
      flush();
      body += line(r.l, r.r, '', '', r.rt, hlR[r.r - 1], null);
    });
    flush();
    return `<table class="diff unified">
      <colgroup><col class="ln"><col class="ln"><col class="mk"><col></colgroup>
      <thead><tr><th colspan="4"><span class="dot" style="background:var(--a)"></span>&lsaquo; ${esc(c.left.label)}
        &nbsp;&nbsp;<span class="dot" style="background:var(--b)"></span>&rsaquo; ${esc(c.right.label)}</th></tr></thead>
      <tbody>${body}</tbody></table>`;
  }

  function singleTable(rows, sideKey, hl) {
    const side = sideKey === 'l' ? state.cmp.left : state.cmp.right;
    const other = sideKey === 'l' ? state.cmp.right : state.cmp.left;
    const cls = sideKey === 'l' ? 'a' : 'b';
    const textKey = sideKey === 'l' ? 'lt' : 'rt';
    const body = rows.map(r => `<tr><td class="ln c${cls}">${r[sideKey]}</td>` +
      `<td class="code s${cls}">${lineHtml(r[textKey], hl[r[sideKey] - 1], null)}</td></tr>`).join('');
    return `<table class="diff single">
      <colgroup><col class="ln"><col></colgroup>
      <thead><tr><th colspan="2" class="h${cls}"><span class="dot"></span>Only in ${esc(side.label)}
        (${esc(side.server)} / ${esc(side.database)}). This object does not exist in ${esc(other.label)}.</th></tr></thead>
      <tbody>${body}</tbody></table>`;
  }

  function renderDiffBody() {
    const d = state.detail;
    const host = $('#diff-host');
    if (!host) return;
    if (!d.rows) {
      host.innerHTML = d.status === 'identical'
        ? `<div class="empty-detail"><b>Identical in ${esc(leftLabel())} and ${esc(rightLabel())}</b>` +
          `Saved reports only include the definitions of objects that differ.</div>`
        : `<div class="empty-detail"><b>Could not load this object</b>Try selecting it again.</div>`;
      return;
    }
    const hlL = SqlHighlight.highlight(sideLines(d.rows, 'l', 'lt'));
    const hlR = SqlHighlight.highlight(sideLines(d.rows, 'r', 'rt'));
    if (d.status === 'only_left') host.innerHTML = singleTable(d.rows, 'l', hlL);
    else if (d.status === 'only_right') host.innerHTML = singleTable(d.rows, 'r', hlR);
    else {
      const items = buildItems(d.rows);
      host.innerHTML = state.view === 'split' ? splitTable(items, hlL, hlR) : unifiedTable(items, hlL, hlR);
    }
  }

  async function setOption(key, value) {
    if (REPORT) return;
    const options = Object.assign({}, state.cmp.options, { [key]: value });
    try {
      const cmp = await api('POST', `/api/compare/${encodeURIComponent(state.cmp.id)}/options`, options);
      writeOptions(cmp.options);
      saveForm();
      openComparison(cmp, state.selected);
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  function navigate(step) {
    const list = filteredObjects();
    if (!list.length) return;
    const idx = list.findIndex(o => o.key === state.selected);
    const next = idx < 0 ? 0 : Math.min(list.length - 1, Math.max(0, idx + step));
    if (list[next].key !== state.selected) selectObject(list[next].key);
  }

  function setView(view) {
    if (state.view === view) return;
    state.view = view;
    $$('[data-view]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === view)));
    writeHash();
    renderDiffBody();
  }

  function toggleExpandAll() {
    state.expandAll = !state.expandAll;
    const btn = $('[data-act=expand]');
    if (btn) btn.textContent = state.expandAll ? 'Collapse unchanged' : 'Show all lines';
    renderDiffBody();
  }

  // ------------------------------------------------------------------ wiring

  function bindDiffView() {
    $('#status-chips').addEventListener('click', e => {
      const chip = e.target.closest('[data-status]');
      if (!chip) return;
      state.filters[chip.dataset.status] = !state.filters[chip.dataset.status];
      renderSummary();
      renderTree();
    });
    $('#diff-options').addEventListener('click', e => {
      const btn = e.target.closest('[data-opt]');
      if (btn && !btn.disabled) setOption(btn.dataset.opt, btn.getAttribute('aria-pressed') !== 'true');
    });
    $('#tree').addEventListener('click', e => {
      const head = e.target.closest('.group-head');
      if (head) {
        const cat = head.dataset.cat;
        if (!state.collapsed.delete(cat)) state.collapsed.add(cat);
        renderTree();
        return;
      }
      const item = e.target.closest('.item');
      if (item) selectObject(item.dataset.key);
    });
    let searchTimer = null;
    $('#search').addEventListener('input', e => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { state.search = e.target.value.trim(); renderTree(); }, 80);
    });
    $('#detail').addEventListener('click', e => {
      const gap = e.target.closest('tr.gap');
      if (gap) { state.expanded.add(Number(gap.dataset.start)); renderDiffBody(); return; }
      const viewBtn = e.target.closest('[data-view]');
      if (viewBtn && !viewBtn.disabled) { setView(viewBtn.dataset.view); return; }
      const act = e.target.closest('[data-act]');
      if (!act || act.disabled) return;
      const d = state.detail;
      if (act.dataset.act === 'expand') toggleExpandAll();
      if (act.dataset.act === 'copy-left') copyText(d.leftText, `the ${leftLabel()} script`);
      if (act.dataset.act === 'copy-right') copyText(d.rightText, `the ${rightLabel()} script`);
    });
    $('#back-btn').addEventListener('click', () => {
      showView('connect');
      try { history.replaceState(null, '', '#connect'); } catch (e) { /* ignore */ }
    });
    document.addEventListener('keydown', e => {
      if ($('#diff-view').hidden || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.target.matches('input, textarea, select')) {
        if (e.key === 'Escape') e.target.blur();
        if (e.key === 'Enter' && e.target.id === 'search') { const first = filteredObjects()[0]; if (first) selectObject(first.key); }
        return;
      }
      const actions = {
        n: () => navigate(1), j: () => navigate(1), p: () => navigate(-1), k: () => navigate(-1),
        u: () => setView(state.view === 'split' ? 'unified' : 'split'),
        e: () => toggleExpandAll(),
        '/': () => $('#search').focus(),
      };
      if (actions[e.key]) { e.preventDefault(); actions[e.key](); }
    });
  }

  function bindConnectView() {
    buildSideCard('left');
    buildSideCard('right');
    buildOptionCheckboxes();
    $('#excludes').addEventListener('input', saveForm);
    $('#swap-sides').addEventListener('click', swapSides);
    $('#compare-btn').addEventListener('click', runCompare);
    $('#profile-select').addEventListener('change', e => applyProfile(e.target.value));
    $('#profile-save').addEventListener('click', saveProfile);
    $('#profile-delete').addEventListener('click', deleteProfile);
    $('#connect-view').addEventListener('keydown', e => {
      if (e.key === 'Enter' && e.target.matches('input') && !e.target.matches('[type=checkbox]')) runCompare();
    });
  }

  async function init() {
    bindDiffView();
    if (new URLSearchParams(location.hash.slice(1)).get('view') === 'unified') state.view = 'unified';
    if (REPORT) {
      document.body.classList.add('report-mode');
      const obj = new URLSearchParams(location.hash.slice(1)).get('obj');
      openComparison(REPORT, obj);
      return;
    }
    bindConnectView();
    const saved = restoreForm();
    loadProfiles(saved.profile);

    const hash = new URLSearchParams(location.hash.slice(1));
    let st = {};
    try { st = await api('GET', '/api/state'); } catch (e) { /* server not reachable */ }
    const id = hash.get('compare') || (hash.has('connect') ? null : st.demoCompareId);
    if (id) {
      try {
        openComparison(await api('GET', `/api/compare/${encodeURIComponent(id)}`), hash.get('obj'));
        return;
      } catch (e) { /* comparison expired: fall through to the form */ }
    }
    showView('connect');
    const firstEmpty = $$('#connect-view [data-f=server]').find(el => !el.value);
    if (firstEmpty) firstEmpty.focus();
  }

  init();
})();
