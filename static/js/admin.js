/* Smart Weather Admin — small UI helpers (no dependencies beyond Bootstrap + Chart.js) */
(function () {
  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  var A = window.AdminUI = {};

  /* theme (same localStorage key as the main site) */
  var saved = localStorage.getItem('theme') || 'light';
  document.documentElement.setAttribute('data-bs-theme', saved);
  A.toggleTheme = function () {
    var next = document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-bs-theme', next); localStorage.setItem('theme', next);
    (A.charts || []).forEach(function (c) { A.applyChartTheme(c); c.update(); });
  };

  /* toasts */
  A.toast = function (msg, kind) {
    var box = document.getElementById('toasts'); if (!box) return;
    var el = document.createElement('div');
    el.className = 'toast align-items-center text-bg-' + (kind === 'error' ? 'danger' : kind === 'warning' ? 'warning' : 'success') + ' border-0';
    el.setAttribute('role', 'alert');
    el.innerHTML = '<div class="d-flex"><div class="toast-body"></div><button class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>';
    el.querySelector('.toast-body').textContent = msg;
    box.appendChild(el); var t = new bootstrap.Toast(el, { delay: 4500 }); t.show();
    el.addEventListener('hidden.bs.toast', function () { el.remove(); });
  };

  /* fetch helpers (same-origin cookies + CSRF header) */
  A.get = function (url) {
    return fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } }).then(A._json);
  };
  A.post = function (url) {
    return fetch(url, { method: 'POST', credentials: 'same-origin',
      headers: { 'X-CSRF-Token': csrf, 'X-Requested-With': 'fetch' } }).then(A._json);
  };
  A._json = function (r) {
    if (r.status === 401) { location.href = '/admin/login?next=' + encodeURIComponent(location.pathname); throw new Error('session expired'); }
    return r.json().then(function (j) { if (!r.ok || !j.ok) throw new Error(errText(j.error)); return j.data; });
  };
  function errText(e) {
    return ({ database_unavailable: 'Database unavailable', server_error: 'Server error', invalid_csrf_token: 'Session expired — reload the page', forbidden: 'Not allowed' })[e] || e || 'Request failed';
  }

  /* confirmation dialog -> resolves true/false */
  A.confirm = function (title, body, okLabel) {
    return new Promise(function (resolve) {
      var m = document.getElementById('confirmModal'), done = false;
      m.querySelector('.modal-title').textContent = title;
      m.querySelector('.modal-body').textContent = body;
      var ok = m.querySelector('[data-ok]'); ok.textContent = okLabel || 'Confirm';
      var modal = bootstrap.Modal.getOrCreateInstance(m);
      function yes() { done = true; modal.hide(); resolve(true); }
      ok.onclick = yes;
      m.addEventListener('hidden.bs.modal', function h() { m.removeEventListener('hidden.bs.modal', h); if (!done) resolve(false); });
      modal.show();
    });
  };

  /* data-action buttons: <button data-action="post" data-url=".." data-confirm="..." data-reload> */
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-action="post"]'); if (!b) return;
    e.preventDefault();
    var run = function () {
      b.disabled = true;
      A.post(b.dataset.url).then(function (d) {
        if (d && d.warning) A.toast(d.warning, 'warning'); else A.toast(b.dataset.success || 'Done');
        if (b.dataset.redirect) location.href = b.dataset.redirect; else setTimeout(function () { location.reload(); }, 600);
      }).catch(function (err) { b.disabled = false; A.toast(err.message, 'error'); });
    };
    if (b.dataset.confirm) A.confirm(b.dataset.title || 'Are you sure?', b.dataset.confirm, b.dataset.ok).then(function (y) { if (y) run(); });
    else run();
  });

  /* sidebar (mobile) */
  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-sidebar-toggle]')) document.querySelector('.admin-sidebar').classList.toggle('open');
    else if (!e.target.closest('.admin-sidebar')) document.querySelector('.admin-sidebar').classList.remove('open');
  });

  /* charts */
  A.charts = [];
  var PALETTE = ['#2563EB', '#06B6D4', '#22C55E', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#64748B'];
  A.applyChartTheme = function (c) {
    var dark = document.documentElement.getAttribute('data-bs-theme') === 'dark';
    var txt = dark ? '#94A3B8' : '#64748B', grid = dark ? 'rgba(148,163,184,.14)' : 'rgba(15,23,42,.07)';
    var o = c.options; o.plugins = o.plugins || {}; o.plugins.legend = o.plugins.legend || {}; o.plugins.legend.labels = { color: txt, boxWidth: 12 };
    Object.keys(o.scales || {}).forEach(function (k) { o.scales[k].ticks = Object.assign(o.scales[k].ticks || {}, { color: txt }); o.scales[k].grid = { color: grid }; });
  };
  A.chart = function (id, type, labels, datasets, opts) {
    var el = document.getElementById(id); if (!el || !window.Chart) return null;
    var pie = type === 'doughnut' || type === 'pie';
    var hasData = datasets.some(function (d) { return (d.data || []).some(function (v) { return v !== null && v !== 0; }); });
    if (!hasData) { el.parentNode.innerHTML = '<div class="empty-state"><i class="fa-regular fa-chart-bar"></i>No data for this period yet</div>'; return null; }
    datasets = datasets.map(function (d, i) {
      var color = PALETTE[i % PALETTE.length];
      return Object.assign({ borderColor: pie ? 'transparent' : color, backgroundColor: pie ? PALETTE : (type === 'line' || d.type === 'line' ? color + '33' : color),
        tension: .35, borderWidth: pie ? 0 : 2, pointRadius: 2, fill: type === 'line' && !d.type, borderRadius: 4, spanGaps: true }, d);
    });
    var cfg = { type: type, data: { labels: labels, datasets: datasets },
      options: Object.assign({ responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: datasets.length > 1 || pie, position: pie ? 'bottom' : 'top' } },
        scales: pie ? {} : { y: { beginAtZero: true, ticks: { precision: 0 } }, x: { grid: { display: false } } } }, opts || {}) };
    var c = new Chart(el, cfg); A.applyChartTheme(c); c.update(); A.charts.push(c); return c;
  };
})();
