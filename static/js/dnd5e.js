/* D&D 5e shared client helpers. No build step; loaded by every templates/dnd5e page.
 *
 * - D5.get / D5.post      JSON fetch (CSRF header added by templates/_csrf.html's fetch wrapper)
 * - D5.sse(name, fn)      subscribe through the shared hub (never new EventSource)
 * - click .d5-roll        roll the dice expression (and, on a sheet, report it to the GM)
 * - click .d5-ref         compendium popover for {@spell ...}-style references
 * - D5.statblock(sb)      HTML for a monster statblock payload (routes._statblock)
 *
 * All strings from data go through D5.esc before reaching innerHTML; rendered
 * entry HTML from the server is already escaped by systems/dnd5e/render.py.
 */
(function () {
  'use strict';
  var D5 = window.D5 = window.D5 || {};

  D5.esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  };
  D5.mod = function (n) { n = Number(n) || 0; return (n >= 0 ? '+' : '') + n; };
  D5.title = function (s) { return String(s || '').replace(/\b\w/g, function (c) { return c.toUpperCase(); }); };
  D5.abilities = ['str', 'dex', 'con', 'int', 'wis', 'cha'];
  D5.abilityNames = {str: 'Strength', dex: 'Dexterity', con: 'Constitution', int: 'Intelligence', wis: 'Wisdom', cha: 'Charisma'};

  function handle(r) {
    return r.text().then(function (t) {
      var d = null;
      try { d = t ? JSON.parse(t) : {}; } catch (e) { d = {error: t.slice(0, 200) || ('HTTP ' + r.status)}; }
      if (!r.ok) {
        var msg = (d && (d.error && (d.error.message || d.error))) || ('HTTP ' + r.status);
        var err = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
        err.status = r.status;
        throw err;
      }
      return d;
    });
  }
  D5.get = function (url) { return fetch(url, {credentials: 'same-origin'}).then(handle); };
  D5.post = function (url, body) {
    return fetch(url, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})}).then(handle);
  };

  D5.toast = function (msg, kind) {
    var box = document.getElementById('d5-toasts');
    if (!box) {
      box = document.createElement('div');
      box.id = 'd5-toasts';
      document.body.appendChild(box);
    }
    var el = document.createElement('div');
    el.className = 'd5-toast' + (kind ? ' is-' + kind : '');
    el.innerHTML = msg;
    box.appendChild(el);
    setTimeout(function () { el.classList.add('is-out'); }, 3800);
    setTimeout(function () { el.remove(); }, 4400);
  };
  // multipart POST (file uploads); same error shape as D5.post
  D5.upload = function (url, formData) {
    return fetch(url, {method: 'POST', body: formData, credentials: 'same-origin'}).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) { var e = new Error(j.error || ('Upload failed (' + r.status + ')')); e.status = r.status; throw e; }
        return j;
      });
    });
  };
  D5.fail = function (e) { D5.toast(D5.esc(e && e.message ? e.message : e), 'error'); };

  D5.sse = function (name, fn) {
    if (!window.appSSE) return;
    window.appSSE(name, function (e) {
      try { fn(JSON.parse(e.data)); } catch (x) { console.error('[dnd5e sse]', name, x); }
    });
  };

  // ---- dice ---------------------------------------------------------------
  D5.roll = function (expr) {
    var s = String(expr || '').replace(/\s+/g, '').replace(/×/g, '*');
    var re = /([+-]?)(?:(\d*)d(\d+)|(\d+))/gi, m, total = 0, parts = [];
    while ((m = re.exec(s))) {
      var sign = m[1] === '-' ? -1 : 1;
      if (m[3]) {
        var n = parseInt(m[2] || '1', 10), f = parseInt(m[3], 10), sub = [];
        for (var i = 0; i < Math.min(n, 100); i++) { var v = 1 + Math.floor(Math.random() * f); sub.push(v); total += sign * v; }
        parts.push((sign < 0 ? '-' : '') + '[' + sub.join(',') + ']');
      } else if (m[4]) {
        total += sign * parseInt(m[4], 10);
        parts.push((sign < 0 ? '-' : '+') + m[4]);
      }
    }
    return {total: total, detail: parts.join(' ')};
  };
  // A sheet sets D5.rollTarget = pc id so its rolls go through the server
  // (trusted + mirrored to the GM's feed). Elsewhere rolls are local.
  D5.rollTarget = null;
  D5.doRoll = function (expr, label) {
    if (D5.rollTarget) {
      return D5.post('/api/dnd5e/pc/' + D5.rollTarget + '/roll', {expr: expr, label: label || ''})
        .then(function (r) { D5.showRoll(label, expr, r.total, r.natural); return r; })
        .catch(D5.fail);
    }
    var r = D5.roll(expr);
    var nat = /^1d20/i.test(expr.replace(/\s+/g, '')) ? parseInt((r.detail.match(/\[(\d+)\]/) || [])[1], 10) : null;
    D5.showRoll(label, expr, r.total, nat);
    return Promise.resolve(r);
  };
  D5.showRoll = function (label, expr, total, nat) {
    var cls = nat === 20 ? 'crit' : (nat === 1 ? 'fumble' : '');
    D5.toast('<span class="d5-toast-roll ' + cls + '">' + D5.esc(total) + '</span> <span>' +
      D5.esc(label || 'Roll') + '</span> <span class="d5-muted">' + D5.esc(expr) + '</span>', cls || 'roll');
  };

  document.addEventListener('click', function (ev) {
    var r = ev.target.closest && ev.target.closest('.d5-roll');
    if (r) {
      ev.preventDefault();
      D5.doRoll(r.getAttribute('data-roll'), r.getAttribute('data-label') || r.textContent);
      return;
    }
    var ref = ev.target.closest && ev.target.closest('.d5-ref');
    if (ref) {
      ev.preventDefault();
      D5.popover(ref, ref.getAttribute('data-kind'), ref.getAttribute('data-id'));
      return;
    }
    var pop = document.getElementById('d5-pop');
    if (pop && !pop.hidden && !pop.contains(ev.target)) pop.hidden = true;
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') { var p = document.getElementById('d5-pop'); if (p) p.hidden = true; }
  });

  // ---- compendium popover --------------------------------------------------
  D5.popover = function (anchor, kind, id) {
    var pop = document.getElementById('d5-pop');
    if (!pop) {
      pop = document.createElement('div');
      pop.id = 'd5-pop';
      pop.className = 'd5-pop';
      pop.hidden = true;
      document.body.appendChild(pop);
    }
    pop.innerHTML = '<div class="d5-muted">Loading&hellip;</div>';
    pop.hidden = false;
    var rect = anchor.getBoundingClientRect();
    var top = window.scrollY + rect.bottom + 6, left = Math.min(window.scrollX + rect.left, window.scrollX + window.innerWidth - 440);
    pop.style.top = top + 'px';
    pop.style.left = Math.max(8, left) + 'px';
    D5.get('/api/dnd5e/content/' + encodeURIComponent(kind) + '/' + encodeURIComponent(id))
      .then(function (d) { pop.innerHTML = D5.recordHTML(d); })
      .catch(function (e) { pop.innerHTML = '<div class="d5-muted">' + D5.esc(e.status === 403 ? 'GM only' : 'Not found') + '</div>'; });
  };

  D5.recordHTML = function (d) {
    if (!d) return '';
    if (d.statblock) return D5.statblock(d.statblock);
    var h = '<div class="d5-rec">' + D5.artHTML(d.image, d.name) + '<div class="d5-rec-head"><span class="d5-rec-name">' + D5.esc(d.name) +
      '</span><span class="d5-src">' + D5.esc(d.source || '') + (d.page ? ' p' + D5.esc(d.page) : '') + '</span></div>';
    (d.meta || []).forEach(function (m) { h += '<div class="d5-rec-meta">' + D5.esc(m) + '</div>'; });
    h += '<div class="d5-prose">' + (d.html || '') + '</div>';
    if (d.classes && d.classes.length) h += '<div class="d5-rec-meta">Classes: ' + D5.esc(d.classes.join(', ')) + '</div>';
    return h + '</div>';
  };

  // Art beside a statblock or record; click opens the full-size version.
  D5.artHTML = function (url, alt) {
    if (!url) return '';
    return '<a class="d5-art" href="' + D5.esc(url) + '" target="_blank" rel="noopener">' +
      '<img src="' + D5.esc(url) + '" alt="' + D5.esc(alt || '') + '" loading="lazy"></a>';
  };
  // Round token for list rows; falls back to the initial when there is none.
  D5.tokenHTML = function (url, name, cls) {
    if (url) return '<img class="d5-token ' + (cls || '') + '" src="' + D5.esc(url) + '" alt="" loading="lazy">';
    return '<span class="d5-token is-blank ' + (cls || '') + '">' + D5.esc(String(name || '?').charAt(0).toUpperCase()) + '</span>';
  };

  D5.statblock = function (sb) {
    var h = '<div class="d5-statblock">' + D5.artHTML(sb.image || sb.token, sb.name) + '<div class="d5-sb-name">' + D5.esc(sb.name) + '</div>' +
      '<div class="d5-sb-line">' + D5.esc(sb.line) + ' <span class="d5-src">' + D5.esc(sb.source || '') + '</span></div><hr>' +
      '<div><b>Armor Class</b> ' + D5.esc(sb.ac) + ' &nbsp; <b>Initiative</b> ' +
      '<span class="d5-roll" data-roll="1d20' + D5.mod(sb.initiative) + '" data-label="' + D5.esc(sb.name) + ' initiative">' + D5.mod(sb.initiative) + '</span></div>' +
      '<div><b>Hit Points</b> ' + D5.esc(sb.hp) + '</div><div><b>Speed</b> ' + D5.esc(sb.speed) + '</div><hr>' +
      '<div class="d5-sb-abil">';
    D5.abilities.forEach(function (a) {
      var x = sb.abilities[a];
      var save = x.save ? ' <span class="d5-muted">save ' + D5.esc(x.save) + '</span>' : '';
      h += '<div><div class="d5-sb-ab">' + a.toUpperCase() + '</div><div>' + x.score + ' (' +
        '<span class="d5-roll" data-roll="1d20' + D5.mod(x.mod) + '" data-label="' + D5.esc(sb.name + ' ' + a.toUpperCase()) + '">' + D5.mod(x.mod) + '</span>)' + save + '</div></div>';
    });
    h += '</div><hr>';
    (sb.lines || []).forEach(function (l) { h += '<div><b>' + D5.esc(l[0]) + '</b> ' + D5.esc(l[1]) + '</div>'; });
    (sb.sections || []).forEach(function (s) {
      h += '<div class="d5-sb-sec">' + D5.esc(s.title) + '</div><div class="d5-prose">' + s.html + '</div>';
    });
    return h + '</div>';
  };

  D5.hpClass = function (hp, max) {
    if (hp <= 0) return 'is-down';
    var p = hp / Math.max(1, max);
    return p <= 0.25 ? 'is-crit' : (p <= 0.5 ? 'is-bloodied' : 'is-ok');
  };
  D5.hpBar = function (hp, max, temp) {
    var pct = Math.max(0, Math.min(100, Math.round(100 * hp / Math.max(1, max))));
    return '<div class="d5-hpbar ' + D5.hpClass(hp, max) + '"><span style="width:' + pct + '%"></span></div>' +
      '<div class="d5-hptext">' + hp + ' / ' + max + (temp ? ' <span class="d5-temp">+' + temp + ' temp</span>' : '') + '</div>';
  };

  // Debounce helper for search boxes.
  D5.debounce = function (fn, ms) {
    var t; return function () { var a = arguments, self = this; clearTimeout(t); t = setTimeout(function () { fn.apply(self, a); }, ms || 200); };
  };

  // A small pick-one sheet (phone friendly, unlike window.prompt).
  // options: [{value, label}] -> Promise resolving to the value, or null.
  D5.choose = function (title, options) {
    return new Promise(function (resolve) {
      var wrap = document.createElement('div');
      wrap.className = 'd5-choose';
      wrap.setAttribute('role', 'dialog');
      wrap.setAttribute('aria-modal', 'true');
      wrap.setAttribute('aria-label', title);
      wrap.innerHTML = '<div class="d5-choose-box"><div class="d5-choose-title">' + D5.esc(title) + '</div>' +
        options.map(function (o, i) {
          return '<button type="button" class="d5-btn d5-choose-opt" data-i="' + i + '">' + D5.esc(o.label) + '</button>';
        }).join('') + '<button type="button" class="d5-btn is-ghost d5-choose-opt" data-i="-1">Cancel</button></div>';
      function done(v) { document.removeEventListener('keydown', onKey); wrap.remove(); resolve(v); }
      function onKey(e) { if (e.key === 'Escape') done(null); }
      wrap.addEventListener('click', function (e) {
        var b = e.target.closest('[data-i]');
        if (b) { var i = +b.getAttribute('data-i'); done(i < 0 ? null : options[i].value); }
        else if (e.target === wrap) done(null);
      });
      document.addEventListener('keydown', onKey);
      document.body.appendChild(wrap);
      var first = wrap.querySelector('.d5-choose-opt');
      if (first) first.focus();
    });
  };
})();
