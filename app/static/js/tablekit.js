/* tablekit — améliore les <table data-smart> : recherche, tri, pagination.
   Sans dépendance. Les lignes sont rendues côté serveur ; on filtre/trie/pagine
   côté client. Idéal jusqu'à quelques centaines de lignes. */
(function () {
  // textContent (et non innerText) : fiable même sur des lignes détachées du DOM.
  function textOf(row) { return (row.textContent || '').toLowerCase(); }

  function cellValue(row, idx) {
    var cell = row.cells[idx];
    if (!cell) return '';
    var raw = (cell.dataset && cell.dataset.sort) || cell.textContent || '';
    return raw.trim();
  }

  function compare(a, b, idx, dir) {
    var va = cellValue(a, idx), vb = cellValue(b, idx);
    var na = parseFloat(va.replace(/[^\d.,-]/g, '').replace(/\s/g, '').replace(',', '.'));
    var nb = parseFloat(vb.replace(/[^\d.,-]/g, '').replace(/\s/g, '').replace(',', '.'));
    var bothNum = !isNaN(na) && !isNaN(nb) && va !== '' && vb !== '';
    if (bothNum) return (na - nb) * dir;
    return va.localeCompare(vb, 'fr', { numeric: true }) * dir;
  }

  function init(table) {
    var tbody = table.tBodies[0];
    if (!tbody) return;
    var all = Array.prototype.slice.call(tbody.rows).filter(function (r) {
      return !r.hasAttribute('data-empty') && r.cells.length > 1;
    });
    if (!all.length) return;

    var pageSize = parseInt(table.getAttribute('data-page-size') || '15', 10);
    var state = { rows: all.slice(), page: 1, col: -1, dir: 1, q: '' };

    // Barre d'outils (recherche + compteur) au-dessus du tableau.
    var wrap = table.parentNode;
    var toolbar = document.createElement('div');
    toolbar.className = 'table-toolbar flex items-center gap-3 flex-wrap';
    toolbar.innerHTML =
      '<div class="relative flex-1 min-w-[200px] max-w-sm">' +
      '<input type="search" placeholder="Rechercher…" class="input" style="padding-left:2.2rem" data-tk-search>' +
      '<i data-lucide="search" class="absolute left-2.5 top-1/2 -translate-y-1/2 muted" style="width:1rem;height:1rem"></i>' +
      '</div><span class="text-xs muted" data-tk-count></span>';
    wrap.parentNode.insertBefore(toolbar, wrap);

    // Pagination sous le tableau.
    var pager = document.createElement('div');
    pager.className = 'table-pager flex items-center justify-between gap-2';
    pager.innerHTML =
      '<span class="text-xs muted" data-tk-range></span>' +
      '<div class="flex items-center gap-1">' +
      '<button class="btn btn-outline btn-sm" data-tk-prev>Précédent</button>' +
      '<span class="text-xs muted px-2" data-tk-page></span>' +
      '<button class="btn btn-outline btn-sm" data-tk-next>Suivant</button></div>';
    wrap.parentNode.insertBefore(pager, wrap.nextSibling);

    var searchEl = toolbar.querySelector('[data-tk-search]');
    var countEl = toolbar.querySelector('[data-tk-count]');
    var rangeEl = pager.querySelector('[data-tk-range]');
    var pageEl = pager.querySelector('[data-tk-page]');

    // En-têtes triables.
    var ths = table.tHead ? table.tHead.rows[0].cells : [];
    Array.prototype.forEach.call(ths, function (th, idx) {
      if (th.hasAttribute('data-no-sort')) return;
      th.style.cursor = 'pointer';
      th.title = 'Trier';
      th.addEventListener('click', function () {
        state.dir = state.col === idx ? -state.dir : 1;
        state.col = idx;
        Array.prototype.forEach.call(ths, function (h) { h.dataset.tkArrow = ''; h.querySelectorAll('[data-tk-ar]').forEach(function(e){e.remove();}); });
        var ar = document.createElement('span'); ar.setAttribute('data-tk-ar', '');
        ar.textContent = state.dir > 0 ? ' ▲' : ' ▼'; ar.style.opacity = '.6'; ar.style.fontSize = '.7em';
        th.appendChild(ar);
        render();
      });
    });

    function apply() {
      var q = state.q;
      state.rows = q ? all.filter(function (r) { return textOf(r).indexOf(q) !== -1; }) : all.slice();
      if (state.col >= 0) state.rows.sort(function (a, b) { return compare(a, b, state.col, state.dir); });
    }

    function render() {
      apply();
      var total = state.rows.length;
      var pages = Math.max(1, Math.ceil(total / pageSize));
      if (state.page > pages) state.page = pages;
      var start = (state.page - 1) * pageSize;
      var end = Math.min(start + pageSize, total);
      // Détache toutes les lignes, réattache la page courante.
      all.forEach(function (r) { if (r.parentNode) r.parentNode.removeChild(r); });
      for (var i = start; i < end; i++) tbody.appendChild(state.rows[i]);
      countEl.textContent = total + (total > 1 ? ' résultats' : ' résultat');
      rangeEl.textContent = total ? (start + 1) + '–' + end + ' sur ' + total : 'Aucun résultat';
      pageEl.textContent = state.page + ' / ' + pages;
      pager.querySelector('[data-tk-prev]').disabled = state.page <= 1;
      pager.querySelector('[data-tk-next]').disabled = state.page >= pages;
      pager.style.display = total > pageSize ? 'flex' : 'none';
      if (window.lucide) window.lucide.createIcons();
    }

    searchEl.addEventListener('input', function () { state.q = this.value.trim().toLowerCase(); state.page = 1; render(); });
    pager.querySelector('[data-tk-prev]').addEventListener('click', function () { if (state.page > 1) { state.page--; render(); } });
    pager.querySelector('[data-tk-next]').addEventListener('click', function () { state.page++; render(); });
    render();
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('table[data-smart]').forEach(init);
  });
})();
