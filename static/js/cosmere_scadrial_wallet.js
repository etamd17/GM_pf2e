/* Scadrial-only wallet editor. Server totals are authoritative. */
(function () {
  const root = document.getElementById('cos-scadrial-wallet');
  if (!root) return;
  const form = root.querySelector('[data-money-form]');
  const status = root.querySelector('[data-money-status]');
  let expected = JSON.parse(root.querySelector('[data-money-initial]').textContent);
  let dirty = false;
  let sending = false;
  if (form) form.addEventListener('input', () => { dirty = true; });
  function paint(data, saved) {
    if (!data.wallet || !data.wallet.scadrial) return;
    const money = data.wallet.scadrial;
    root.querySelector('[data-money-total]').textContent = data.wallet_total_money;
    for (const key of ['boxing', 'clip']) root.querySelector('[data-money-count="' + key + '"]').textContent = money[key];
    const legacy = root.querySelector('[data-money-legacy]');
    const legacyCounts = root.querySelector('[data-money-legacy-counts]');
    legacyCounts.replaceChildren();
    const raw = data.wallet.spheres || {};
    const spheres = data.wallet.version === 2 ? raw : {diamond: raw};
    for (const [gem, counts] of Object.entries(spheres)) {
      if (!counts || !['chip', 'mark', 'broam'].some(key => Number(counts[key]) > 0)) continue;
      const node = document.createElement('p');
      node.textContent = gem + ': ' + ['broam', 'mark', 'chip'].map(key => String(counts[key] || 0) + ' ' + key + 's').join(', ');
      legacyCounts.appendChild(node);
    }
    legacy.hidden = !legacyCounts.children.length;
    const goods = root.querySelector('[data-money-goods]');
    goods.replaceChildren();
    (data.wallet.items || []).forEach(item => {
      const node = document.createElement('span');
      node.className = 'cs-chip';
      node.textContent = String(item.name || '') + (item.qty > 1 ? ' ×' + item.qty : '');
      goods.appendChild(node);
    });
    if (dirty && !saved) {
      status.textContent = 'Money changed elsewhere. Reload before saving your edits.';
      return; // Retain the old comparison base so a stale edit cannot overwrite loot.
    }
    expected = money;
    if (form) for (const key of ['boxing', 'clip']) form.elements[key].value = money[key];
  }
  if (form) form.addEventListener('submit', async event => {
    event.preventDefault();
    if (sending || !form.reportValidity()) return;
    const money = {};
    for (const key of ['boxing', 'clip']) money[key] = Number(form.elements[key].value);
    if (!Object.values(money).every(n => Number.isSafeInteger(n) && n >= 0 && n <= 1000000000000)) return;
    sending = true;
    const button = form.querySelector('button');
    if (button) button.disabled = true;
    try {
      const response = await fetch('/cosmere/pc/' + encodeURIComponent(root.dataset.pid) + '/wallet', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({scadrial: money, expected})
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw Error(data.error || 'Money could not be saved.');
      dirty = false;
      paint(data, true);
      status.textContent = 'Saved';
    } catch (error) { status.textContent = error.message || 'Money could not be saved.'; }
    finally { sending = false; if (button) button.disabled = false; }
  });
  for (const name of ['cosmere_loot', 'cosmere_player_state']) {
    if (window.appSSE) window.appSSE(name, event => {
      try { const data = JSON.parse(event.data); if (data.pid === root.dataset.pid) paint(data, false); }
      catch (_) { /* Ignore unrelated or incomplete live frames. */ }
    });
  }
})();
