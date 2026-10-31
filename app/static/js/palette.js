/* Palette de commandes (Ctrl/Cmd+K) : recherche globale clients/factures/devis. */
(function () {
  if (!document.querySelector('.app-topbar')) return; // pages connectées uniquement

  var overlay, input, list, empty, results = [], active = -1, timer = null, lastQ = '';

  function build() {
    overlay = document.createElement('div');
    overlay.className = 'cmdk-overlay';
    overlay.style.cssText =
      'position:fixed;inset:0;z-index:120;display:none;align-items:flex-start;justify-content:center;' +
      'padding-top:12vh;background:rgba(2,6,23,.5);backdrop-filter:blur(3px)';
    overlay.innerHTML =
      '<div class="cmdk-panel" style="width:100%;max-width:600px;background:var(--surface);border:1px solid var(--border);' +
      'border-radius:16px;box-shadow:var(--shadow-lg);overflow:hidden">' +
      '<div style="display:flex;align-items:center;gap:.6rem;padding:.85rem 1rem;border-bottom:1px solid var(--border)">' +
      '<i data-lucide="search" style="width:1.1rem;height:1.1rem;color:var(--text-muted)"></i>' +
      '<input class="cmdk-input" placeholder="Rechercher un client, une facture, un devis…" ' +
      'style="flex:1;border:0;outline:0;background:transparent;color:var(--text);font-size:1rem" />' +
      '<kbd style="font-size:.7rem;color:var(--text-soft);border:1px solid var(--border);border-radius:6px;padding:.1rem .4rem">Esc</kbd>' +
      '</div>' +
      '<div class="cmdk-list" style="max-height:340px;overflow-y:auto;padding:.4rem"></div>' +
      '<div class="cmdk-empty" style="padding:1.5rem;text-align:center;color:var(--text-soft);font-size:.9rem">Tapez au moins 2 caractères…</div>' +
      '</div>';
    document.body.appendChild(overlay);
    input = overlay.querySelector('.cmdk-input');
    list = overlay.querySelector('.cmdk-list');
    empty = overlay.querySelector('.cmdk-empty');

    overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });
    input.addEventListener('input', function () { schedule(input.value); });
    input.addEventListener('keydown', onKey);
    if (window.lucide) window.lucide.createIcons();
  }

  function open() {
    if (!overlay) build();
    overlay.style.display = 'flex';
    input.value = ''; results = []; active = -1; lastQ = '';
    render();
    setTimeout(function () { input.focus(); }, 20);
  }
  function close() { if (overlay) overlay.style.display = 'none'; }
  function isOpen() { return overlay && overlay.style.display === 'flex'; }

  function schedule(q) {
    clearTimeout(timer);
    timer = setTimeout(function () { run(q.trim()); }, 180);
  }

  function run(q) {
    if (q.length < 2) { results = []; active = -1; lastQ = q; render(); return; }
    lastQ = q;
    fetch('/api/search?q=' + encodeURIComponent(q), { headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : { results: [] }; })
      .then(function (d) { if (input.value.trim() === q) { results = d.results || []; active = results.length ? 0 : -1; render(); } })
      .catch(function () {});
  }

  function render() {
    if (!results.length) {
      list.innerHTML = '';
      empty.style.display = 'block';
      empty.textContent = lastQ.length >= 2 ? 'Aucun résultat.' : 'Tapez au moins 2 caractères…';
      return;
    }
    empty.style.display = 'none';
    list.innerHTML = results.map(function (r, i) {
      return '<a href="' + r.url + '" data-i="' + i + '" class="cmdk-item" ' +
        'style="display:flex;align-items:center;gap:.75rem;padding:.6rem .7rem;border-radius:10px;text-decoration:none;color:var(--text);' +
        (i === active ? 'background:var(--surface-hover)' : '') + '">' +
        '<i data-lucide="' + (r.icon || 'file') + '" style="width:1rem;height:1rem;color:var(--text-muted)"></i>' +
        '<span style="flex:1;min-width:0"><span style="font-weight:600">' + esc(r.label) + '</span>' +
        (r.sub ? '<span style="color:var(--text-soft);font-size:.8rem"> · ' + esc(r.sub) + '</span>' : '') + '</span>' +
        '<span class="badge badge-slate" style="font-size:.65rem">' + esc(r.type) + '</span></a>';
    }).join('');
    Array.prototype.forEach.call(list.querySelectorAll('.cmdk-item'), function (el) {
      el.addEventListener('mousemove', function () { active = parseInt(el.dataset.i, 10); paint(); });
    });
    if (window.lucide) window.lucide.createIcons();
  }

  function paint() {
    Array.prototype.forEach.call(list.querySelectorAll('.cmdk-item'), function (el) {
      el.style.background = parseInt(el.dataset.i, 10) === active ? 'var(--surface-hover)' : '';
    });
  }

  function onKey(e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); if (results.length) { active = (active + 1) % results.length; paint(); scrollTo(); } }
    else if (e.key === 'ArrowUp') { e.preventDefault(); if (results.length) { active = (active - 1 + results.length) % results.length; paint(); scrollTo(); } }
    else if (e.key === 'Enter') { e.preventDefault(); if (active >= 0 && results[active]) window.location.href = results[active].url; }
    else if (e.key === 'Escape') { e.preventDefault(); close(); }
  }
  function scrollTo() {
    var el = list.querySelector('.cmdk-item[data-i="' + active + '"]');
    if (el) el.scrollIntoView({ block: 'nearest' });
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); isOpen() ? close() : open(); }
  });

  // Expose pour un éventuel bouton loupe dans la barre.
  window.openCommandPalette = open;
})();
