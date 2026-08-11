const {
  ItemView,
  MarkdownView,
  Modal,
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  normalizePath,
  requestUrl,
} = require('obsidian');

const VIEW_TYPE = 'session-operations-sync-view';
const API_PREFIX = '/api/integrations/obsidian/v1';

const DEFAULT_SETTINGS = {
  baseUrl: 'http://127.0.0.1:5001',
  campaignId: '',
  token: '',
  sessionDataFolder: '_Session Data',
  visiblePollMs: 1000,
  backgroundPollMs: 5000,
  lastEventSequence: 0,
  pendingCommands: [],
  roomSyncEnabled: true,
};

const NUMERIC_CONDITIONS = [
  'frightened', 'sickened', 'stunned', 'slowed', 'enfeebled', 'clumsy',
  'drained', 'stupefied', 'wounded', 'dying', 'doomed',
];
const BOOLEAN_CONDITIONS = ['prone', 'off_guard', 'concealed', 'hidden', 'undetected'];

class SyncApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.name = 'SyncApiError';
    this.status = status;
    this.body = body || {};
  }
}

function commandId(prefix = 'obs') {
  const random = Math.random().toString(36).slice(2, 10);
  return `${prefix}-${Date.now()}-${random}`;
}

function safeSegment(value) {
  return String(value || 'unknown')
    .replace(/[\\/:*?"<>|#^[\]]/g, '-')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 100) || 'unknown';
}

// Frontmatter values come from the server (session id, label, timestamps), so
// they cannot be pasted into hand-built YAML raw. JSON string syntax is a valid
// YAML 1.2 double-quoted scalar, so this escapes quotes, backslashes, newlines
// and control characters in one step -- a label containing a quote or a newline
// used to corrupt the note's frontmatter permanently for every other plugin
// that reads it.
function yamlString(value) {
  return JSON.stringify(String(value ?? ''));
}

function conditionLabel(value) {
  return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function activeConditions(conditions, exclude = []) {
  return Object.entries(conditions || {})
    .filter(([name, value]) => value && value !== 0 && !exclude.includes(name))
    .map(([name, value]) => typeof value === 'boolean' ? conditionLabel(name) : `${conditionLabel(name)} ${value}`)
    .join(', ');
}

function signed(value) {
  const number = Number(value || 0);
  return number >= 0 ? `+${number}` : String(number);
}

function degreeLabel(value) {
  return conditionLabel(String(value || '').replace('crit_', 'critical_'));
}

function firstDiceExpression(value) {
  const match = String(value || '').match(/\d+d\d+(?:\s*[+-]\s*\d+)?/i);
  return match ? match[0].replace(/\s+/g, '') : '';
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  const chunk = 0x8000;
  for (let index = 0; index < bytes.length; index += chunk) {
    binary += String.fromCharCode(...bytes.subarray(index, Math.min(index + chunk, bytes.length)));
  }
  return window.btoa(binary);
}

function plainExcerpt(markdown, limit = 700) {
  return String(markdown || '')
    .replace(/^---[\s\S]*?---\s*/m, '')
    .replace(/!\[\[[^\]]+\]\]/g, '')
    .replace(/\[\[([^\]|]+)(?:\|[^\]]+)?\]\]/g, '$1')
    .replace(/^>\s*\[![^\]]+\].*$/gm, '')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/[|*_`>#]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, limit);
}

class TextPromptModal extends Modal {
  constructor(app, title, placeholder, initialValue = '') {
    super(app);
    this.titleText = title;
    this.placeholder = placeholder;
    this.initialValue = initialValue;
    this.promise = new Promise((resolve) => { this.resolve = resolve; });
    this.didResolve = false;
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.createEl('h3', { text: this.titleText });
    const input = contentEl.createEl('input', { type: 'text', value: this.initialValue });
    input.placeholder = this.placeholder;
    input.style.width = '100%';
    const submit = () => {
      const value = input.value.trim();
      if (!value) return;
      this.didResolve = true;
      this.resolve(value);
      this.close();
    };
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') submit();
    });
    const button = contentEl.createEl('button', { text: 'Continue' });
    button.style.marginTop = '12px';
    button.addEventListener('click', submit);
    setTimeout(() => input.focus(), 0);
  }

  onClose() {
    this.contentEl.empty();
    if (!this.didResolve) this.resolve(null);
  }
}

class NumberPromptModal extends Modal {
  constructor(app, title, actionLabel) {
    super(app);
    this.titleText = title;
    this.actionLabel = actionLabel;
    this.promise = new Promise((resolve) => { this.resolve = resolve; });
    this.didResolve = false;
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.createEl('h3', { text: this.titleText });
    const input = contentEl.createEl('input', { type: 'number', value: '1', attr: { min: '1', step: '1' } });
    input.style.width = '100%';
    const submit = () => {
      const value = Number.parseInt(input.value, 10);
      if (!Number.isFinite(value) || value < 1) return;
      this.didResolve = true;
      this.resolve(value);
      this.close();
    };
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') submit();
    });
    const button = contentEl.createEl('button', { text: this.actionLabel });
    button.style.marginTop = '12px';
    button.addEventListener('click', submit);
    setTimeout(() => { input.focus(); input.select(); }, 0);
  }

  onClose() {
    this.contentEl.empty();
    if (!this.didResolve) this.resolve(null);
  }
}

class ConfirmModal extends Modal {
  constructor(app, title, detail, confirmLabel) {
    super(app);
    this.titleText = title;
    this.detail = detail;
    this.confirmLabel = confirmLabel;
    this.promise = new Promise((resolve) => { this.resolve = resolve; });
    this.didResolve = false;
  }

  onOpen() {
    this.contentEl.createEl('h3', { text: this.titleText });
    this.contentEl.createEl('p', { text: this.detail });
    const row = this.contentEl.createDiv({ cls: 'modal-button-container' });
    const cancel = row.createEl('button', { text: 'Cancel' });
    cancel.addEventListener('click', () => this.close());
    const confirm = row.createEl('button', { text: this.confirmLabel, cls: 'mod-warning' });
    confirm.addEventListener('click', () => {
      this.didResolve = true;
      this.resolve(true);
      this.close();
    });
  }

  onClose() {
    this.contentEl.empty();
    if (!this.didResolve) this.resolve(false);
  }
}

class SessionOperationsView extends ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.selectedId = null;
    this.selectedPartyName = null;
    this.selectedTab = 'overview';
    // Sticky condition duration in rounds; 0 means no auto-expiry timer.
    this.conditionRounds = 0;
  }

  getViewType() { return VIEW_TYPE; }
  getDisplayText() { return 'Session Operations'; }
  getIcon() { return 'swords'; }

  async onOpen() {
    this.containerEl.addClass('session-ops-host');
    this.render();
    await this.plugin.syncNow({ quiet: true });
  }

  async onClose() {}

  button(parent, label, handler, cls = '') {
    const button = parent.createEl('button', { text: label, cls });
    button.addEventListener('click', async (event) => {
      event.stopPropagation();
      button.disabled = true;
      try { await handler(); } finally { button.disabled = false; }
    });
    return button;
  }

  render() {
    const root = this.containerEl.children[1];
    root.empty();
    root.addClass('session-ops-view');

    const header = root.createDiv({ cls: 'so-header' });
    const title = header.createDiv();
    title.createEl('h2', { text: 'Session Operations' });
    const campaignName = this.plugin.state?.campaign?.name || 'No campaign connected';
    title.createDiv({ cls: 'so-subtitle', text: campaignName });
    const connection = header.createDiv({ cls: 'so-connection', text: this.plugin.connectionLabel() });
    if (this.plugin.connected) connection.addClass('is-connected');
    if (this.plugin.lastError) connection.addClass('is-error');

    const toolbar = root.createDiv({ cls: 'so-toolbar' });
    this.button(toolbar, 'Sync now', () => this.plugin.syncNow());
    if (this.plugin.session?.status === 'live') {
      this.button(toolbar, 'End session', () => this.plugin.endSession());
    } else {
      this.button(toolbar, 'Start session', () => this.plugin.startSession());
    }
    this.button(toolbar, 'Website setup', () => this.plugin.openSetupPage());
    this.button(toolbar, 'Reveal selection', () => this.plugin.revealCurrentSelection());
    if (this.plugin.settings.pendingCommands.length) {
      this.button(toolbar, `Review pending (${this.plugin.settings.pendingCommands.length})`, () => this.plugin.retryPending());
    }

    if (!this.plugin.isConfigured()) {
      root.createDiv({
        cls: 'so-empty',
        text: 'Add the website URL, campaign ID, and connection token in Settings → Session Operations Sync.',
      });
      return;
    }
    if (this.plugin.lastError && !this.plugin.state) {
      root.createDiv({ cls: 'so-error-box', text: this.plugin.lastError });
      return;
    }

    this.renderSession(root);
    this.renderRoomOperations(root);
    this.renderParty(root);
    this.renderEncounter(root);
    this.renderSelected(root);
    this.renderPlayerRequests(root);
    this.renderCapture(root);
  }

  renderSession(root) {
    const section = root.createDiv({ cls: 'so-section' });
    const row = section.createDiv({ cls: 'so-session-line' });
    const session = this.plugin.session;
    row.createDiv({ text: session ? `${session.label} · ${conditionLabel(session.status)}` : 'No live session' });
    row.createDiv({ cls: 'so-meta', text: `Revision ${this.plugin.revision}` });
    if (this.plugin.settings.pendingCommands.length) {
      section.createDiv({ cls: 'so-pending', text: 'Offline commands are waiting for review; they are never silently rebased.' });
    }
  }

  renderRoomOperations(root) {
    const section = root.createDiv({ cls: 'so-section so-room-operations' });
    const title = section.createDiv({ cls: 'so-section-title' });
    title.createEl('h3', { text: 'Room operations' });
    const room = this.plugin.currentRoom;
    const active = this.plugin.state?.operations?.active_room;
    if (active) {
      section.createDiv({ cls: 'so-active-room', text: `Live room: ${active.title} (${active.area_id})` });
    }
    if (!room) {
      section.createDiv({ cls: 'so-empty', text: 'Open a location note to preview its room controls. Opening a note never enters it automatically.' });
      return;
    }
    const card = section.createDiv({ cls: 'so-room-card' });
    const heading = card.createDiv({ cls: 'so-card-heading' });
    const headingText = heading.createDiv();
    headingText.createEl('h4', { text: room.title });
    headingText.createDiv({ cls: 'so-meta', text: `${room.areaId} · ${room.path}` });
    if (active?.area_id === room.areaId) heading.createSpan({ cls: 'so-live-pill', text: 'LIVE' });
    const controls = card.createDiv({ cls: 'so-inline-controls' });
    if (active?.area_id !== room.areaId) {
      this.button(controls, 'Enter room', () => this.plugin.enterCurrentRoom(), 'mod-cta');
    } else {
      // Entering is one tap from any note carrying an area_id, so mis-taps are
      // routine. Without a way out the pane insisted a room was live until
      // another was entered.
      this.button(controls, 'Leave room', () => this.plugin.clearActiveRoom());
    }
    const manifest = room.sessionOps || {};
    const encounter = manifest.encounter || {};
    const roster = Array.isArray(encounter.monsters) ? encounter.monsters : [];
    const previewNames = roster.length
      ? roster.map((row) => `${row.count || 1}× ${row.name || row.path || 'Unknown'}`)
      : (room.creatures || []).map((name) => String(name));
    if (encounter.template || roster.length) {
      card.createDiv({ cls: 'so-room-summary', text: `Encounter: ${encounter.template || previewNames.join(', ')}` });
      this.button(controls, 'Launch encounter', () => this.plugin.launchCurrentRoomEncounter());
    }
    // Every creature the note mentions, tappable. Previously this was a warning
    // telling the GM to go and hand-author a manifest of library paths before
    // anything could be launched -- which is work done at prep time to solve a
    // problem that occurs at play time. Names resolve against the bestiary
    // server-side, so the note can just say "Desmohund".
    const addable = roster.length
      ? roster.map((row) => ({ name: row.name || row.path || '', path: row.path || '', count: row.count || 1 }))
      : (room.creatures || []).map((name) => ({ name: String(name), path: '', count: 1 }));
    if (addable.length) {
      const addRow = card.createDiv({ cls: 'so-creature-row' });
      addRow.createDiv({ cls: 'so-duration-label', text: 'Add to initiative' });
      addable.forEach((creature) => {
        const chip = addRow.createDiv({
          cls: 'so-creature-chip',
          text: creature.count > 1 ? `${creature.name} x${creature.count}` : creature.name,
        });
        chip.addEventListener('click', () => this.plugin.addCreaturesToEncounter([creature]));
      });
      if (addable.length > 1) {
        const all = addRow.createDiv({ cls: 'so-creature-chip is-all', text: 'add all' });
        all.addEventListener('click', () => this.plugin.addCreaturesToEncounter(addable));
      }
    }
    const reminders = Array.isArray(manifest.round_reminders) ? manifest.round_reminders : [];
    if (reminders.length) {
      card.createDiv({ cls: 'so-room-summary', text: `${reminders.length} round reminder${reminders.length === 1 ? '' : 's'} prepared.` });
      this.button(controls, 'Sync reminders', () => this.plugin.syncCurrentRoomReminders());
    }
    const reveals = Array.isArray(manifest.reveals) ? manifest.reveals : [];
    if (reveals.length) {
      const revealRow = card.createDiv({ cls: 'so-preset-reveals' });
      reveals.forEach((reveal) => {
        this.button(revealRow, `Reveal: ${reveal.title || 'Room detail'}`, () => this.plugin.revealPreset(reveal));
      });
    }
    if (room.npcCards?.length) {
      const npcGrid = card.createDiv({ cls: 'so-npc-grid' });
      room.npcCards.forEach((npc) => this.renderNpcCard(npcGrid, npc));
    }
  }

  renderNpcCard(parent, npc) {
    const card = parent.createDiv({ cls: 'so-npc-card' });
    const head = card.createDiv({ cls: 'so-npc-head' });
    if (npc.portraitUrl) {
      const image = head.createEl('img', { cls: 'so-portrait', attr: { src: npc.portraitUrl, alt: npc.name } });
      image.addEventListener('error', () => image.addClass('is-hidden'));
    } else {
      head.createDiv({ cls: 'so-portrait so-portrait-fallback', text: String(npc.name || '?').slice(0, 1).toUpperCase() });
    }
    const identity = head.createDiv({ cls: 'so-card-identity' });
    identity.createEl('h5', { text: npc.name });
    identity.createDiv({ cls: 'so-meta', text: [npc.role, npc.status].filter(Boolean).join(' · ') || 'NPC' });
    if (npc.excerpt) card.createDiv({ cls: 'so-npc-excerpt', text: npc.excerpt });
    const controls = card.createDiv({ cls: 'so-inline-controls' });
    if (npc.file) this.button(controls, 'Open note', () => this.plugin.openVaultFile(npc.file));
    if (npc.portraitFile) this.button(controls, 'Reveal portrait', () => this.plugin.revealNpcPortrait(npc));
  }

  renderParty(root) {
    const section = root.createDiv({ cls: 'so-section' });
    const title = section.createDiv({ cls: 'so-section-title' });
    title.createEl('h3', { text: 'Party state' });
    const grid = section.createDiv({ cls: 'so-party-grid' });
    const party = this.plugin.state?.party || [];
    if (!party.length) {
      grid.createDiv({ cls: 'so-empty', text: 'No party members are loaded on the active website table.' });
      return;
    }
    // Dying first. A PC at 0 HP is the most consequential state at the table and
    // it used to be one comma-separated word among others; sorting it to the top
    // means the GM never has to look for it.
    const dyingValue = (pc) => Number((pc.conditions || {}).dying || 0);
    const ordered = [...party].sort((a, b) => dyingValue(b) - dyingValue(a));

    ordered.forEach((pc) => {
      const dying = dyingValue(pc);
      const row = grid.createDiv({ cls: 'so-party-row' });
      if (pc.name === this.selectedPartyName) row.addClass('is-selected');
      if (dying) row.addClass('is-dying');
      row.addEventListener('click', () => {
        this.selectedPartyName = pc.name;
        this.selectedId = null;
        this.selectedTab = 'overview';
        this.render();
      });

      const current = Number(pc.current_hp || 0);
      const maximum = Math.max(1, Number(pc.max_hp || 0));
      const percent = Math.max(0, Math.min(100, current / maximum * 100));

      const top = row.createDiv({ cls: 'so-row-top' });
      top.createDiv({ cls: 'so-party-name', text: pc.name });
      top.createDiv({
        cls: 'so-hp' + (percent <= 25 ? ' is-critical' : percent <= 50 ? ' is-wounded' : ''),
        text: `${current}/${maximum}` + (pc.temp_hp ? ` +${pc.temp_hp}` : ''),
      });

      const bar = row.createDiv({ cls: 'so-hp-bar' });
      const fill = bar.createDiv({ cls: 'so-hp-fill' });
      fill.style.width = `${percent}%`;
      if (percent <= 25) fill.addClass('is-critical');
      else if (percent <= 50) fill.addClass('is-wounded');

      // Everything below here was already crossing the wire and being discarded.
      // hero_points, focus, reaction_used and persistent_damage have always been
      // in the snapshot; the card rendered name, HP and a conditions string.
      const chips = row.createDiv({ cls: 'so-chips' });
      if (dying) {
        chips.createDiv({ cls: 'so-chip is-danger', text: `DYING ${dying}` });
        const wounded = Number((pc.conditions || {}).wounded || 0);
        // Recovery DC is 10 + dying value, raised by wounded (Player Core).
        chips.createDiv({ cls: 'so-chip is-danger', text: `recovery DC ${10 + dying + wounded}` });
      }
      if (pc.persistent_damage) {
        const pd = Array.isArray(pc.persistent_damage) ? pc.persistent_damage.length : pc.persistent_damage;
        if (pd) chips.createDiv({ cls: 'so-chip is-danger', text: `persistent ${pd}` });
      }
      if (Number(pc.hero_points)) chips.createDiv({ cls: 'so-chip is-hero', text: `hero ${pc.hero_points}` });
      if (pc.focus_max) chips.createDiv({ cls: 'so-chip is-focus', text: `focus ${pc.focus || 0}/${pc.focus_max}` });
      else if (Number(pc.focus)) chips.createDiv({ cls: 'so-chip is-focus', text: `focus ${pc.focus}` });
      if (pc.reaction_used) chips.createDiv({ cls: 'so-chip is-muted', text: 'reaction spent' });
      activeConditions(pc.conditions, ['dying'])
        .split(', ')
        .filter(Boolean)
        .forEach((c) => chips.createDiv({ cls: 'so-chip', text: c }));

      // No Inspect button: the row itself already opens the inspector, and the
      // button did nothing else.
      const actions = row.createDiv({ cls: 'so-party-actions' });
      this.button(actions, 'Damage', () => this.plugin.adjustPartyHp(pc.name, 'damage'));
      this.button(actions, 'Heal', () => this.plugin.adjustPartyHp(pc.name, 'heal'));
    });
  }

  renderEncounter(root) {
    const section = root.createDiv({ cls: 'so-section' });
    const title = section.createDiv({ cls: 'so-section-title' });
    title.createEl('h3', { text: 'Initiative' });
    const encounter = this.plugin.state?.encounter || {};
    const combatants = encounter.combatants || [];
    if (!combatants.length) {
      section.createDiv({ cls: 'so-empty', text: 'No active encounter. Start or load combat on the website, then sync.' });
      return;
    }

    const banner = section.createDiv({ cls: 'so-round-banner' });
    banner.createDiv({ cls: 'so-round-number', text: String(encounter.round || 1) });
    banner.createDiv({ text: `Round ${encounter.round || 1}` });
    banner.createDiv({ cls: 'so-meta', text: `Active: ${encounter.active_name || 'Unknown'}` });
    const turnControls = section.createDiv({ cls: 'so-inline-controls' });
    this.button(turnControls, 'Previous turn', () => this.plugin.advanceTurn('prev'));
    this.button(turnControls, 'Next turn', () => this.plugin.advanceTurn('next'));

    const list = section.createDiv({ cls: 'so-combatant-list' });
    combatants.forEach((combatant) => {
      const row = list.createDiv({ cls: 'so-combatant' });
      if (combatant.is_active) row.addClass('is-active');
      if (combatant.instance_id === this.selectedId) row.addClass('is-selected');
      row.createDiv({ cls: 'so-init', text: String(combatant.initiative ?? '—') });
      const main = row.createDiv({ cls: 'so-combatant-main' });
      main.createDiv({ cls: 'so-combatant-name', text: combatant.name || 'Unknown' });
      const hp = combatant.current_hp == null ? '' : `${combatant.current_hp}/${combatant.max_hp} HP`;
      const cond = activeConditions(combatant.conditions);
      main.createDiv({ cls: 'so-meta', text: [hp, cond].filter(Boolean).join(' · ') || 'No visible state' });
      row.addEventListener('click', () => {
        this.selectedId = combatant.instance_id;
        this.selectedPartyName = null;
        this.selectedTab = 'overview';
        this.render();
      });
    });

    if (!this.selectedId || !combatants.some((c) => c.instance_id === this.selectedId)) {
      this.selectedId = encounter.active_id || combatants[0].instance_id;
    }
  }

  renderSelected(root) {
    const combatants = this.plugin.state?.encounter?.combatants || [];
    const party = this.plugin.state?.party || [];
    let target = combatants.find((c) => c.instance_id === this.selectedId);
    if (target?.is_pc) {
      const partyState = party.find((pc) => pc.name === target.name) || {};
      target = Object.assign({}, partyState, target, {
        target_id: target.instance_id,
        strikes: partyState.strikes || target.strikes,
        spell_casters: partyState.spell_casters || target.spell_casters,
        feats: partyState.feats || target.feats,
        reactions: partyState.reactions || target.reactions,
        skills: partyState.skills || target.skills,
        persistent_damage_list: partyState.persistent_damage || target.persistent_damage_list,
      });
    }
    if (!target && this.selectedPartyName) {
      const partyState = party.find((pc) => pc.name === this.selectedPartyName);
      if (partyState) target = Object.assign({}, partyState, { is_pc: true, pc_name: partyState.name });
    }
    if (!target) return;

    // Overlay the on-demand statblock when we have one. Additive by design: the
    // snapshot still carries these fields today, so a server without the detail
    // endpoint, an offline pane, or a first paint before the fetch returns all
    // degrade to exactly the previous behaviour rather than to an empty card.
    // Snapshot values win for anything volatile (HP, conditions) because those
    // are a second old at most, while the cached statblock may be much older.
    const cached = this.plugin.detailFor(target.target_id || target.instance_id || target.name);
    if (cached) {
      // Fill gaps only. A plain Object.assign({}, cached, target) would let an
      // EXPLICITLY undefined key on target clobber a good cached value --
      // Object.assign copies undefined -- and the merge above sets exactly such
      // keys (strikes, feats, reactions...) whenever the snapshot no longer
      // carries them. Volatile values on target still win, because they are a
      // second old at most while a cached statblock may be much older.
      target = Object.assign({}, target);
      Object.keys(cached).forEach((key) => {
        const value = target[key];
        const empty = value === undefined || value === null
          || (Array.isArray(value) && value.length === 0);
        if (empty) target[key] = cached[key];
      });
    }
    this.plugin.ensureDetail(target);

    const section = root.createDiv({ cls: 'so-section' });
    const title = section.createDiv({ cls: 'so-section-title' });
    title.createEl('h3', { text: 'Combatant inspector' });
    const card = section.createDiv({ cls: 'so-detail-card so-inspector' });
    this.renderInspectorHeader(card, target);
    this.renderRollResult(card);
    const tabs = card.createDiv({ cls: 'so-inspector-tabs' });
    ['overview', 'offense', 'spells', 'abilities', 'conditions'].forEach((tab) => {
      const button = this.button(tabs, conditionLabel(tab), async () => {
        this.selectedTab = tab;
        this.render();
      });
      if (this.selectedTab === tab) button.addClass('is-active');
    });
    const panel = card.createDiv({ cls: 'so-inspector-panel' });
    if (this.selectedTab === 'overview') this.renderInspectorOverview(panel, target);
    else if (this.selectedTab === 'offense') this.renderInspectorOffense(panel, target);
    else if (this.selectedTab === 'spells') this.renderInspectorSpells(panel, target);
    else if (this.selectedTab === 'abilities') this.renderInspectorAbilities(panel, target);
    else this.renderInspectorConditions(panel, target);
  }

  rollPayload(target, extra = {}) {
    const payload = Object.assign({}, extra);
    if (target.target_id || target.instance_id) payload.target_id = target.target_id || target.instance_id;
    else if (target.is_pc) payload.pc_name = target.name;
    return payload;
  }

  renderInspectorHeader(card, target) {
    const head = card.createDiv({ cls: 'so-inspector-head' });
    const portraitUrl = target.portrait_url
      ? this.plugin.absoluteWebsiteUrl(target.portrait_url)
      : this.plugin.localPortraitUrl(target.name);
    if (portraitUrl) {
      const image = head.createEl('img', { cls: 'so-inspector-portrait', attr: { src: portraitUrl, alt: target.name } });
      if (target.portrait_focus) image.style.objectPosition = `${target.portrait_focus.x || 50}% ${target.portrait_focus.y || 50}%`;
    } else {
      head.createDiv({ cls: 'so-inspector-portrait so-portrait-fallback', text: String(target.name || '?').slice(0, 1).toUpperCase() });
    }
    const identity = head.createDiv({ cls: 'so-card-identity' });
    identity.createEl('h4', { text: target.name });
    const role = target.is_pc
      ? [target.ancestry, target.class_name && `${target.class_name} ${target.level || ''}`, target.subclass].filter(Boolean).join(' · ')
      : [`Level ${target.level ?? '?'}`, ...(target.traits || []).slice(0, 4)].join(' · ');
    identity.createDiv({ cls: 'so-meta', text: role || (target.is_pc ? 'Player character' : 'Enemy') });
    const hp = target.current_hp == null ? '' : `${target.current_hp}/${target.max_hp} HP${target.temp_hp ? ` + ${target.temp_hp} temp` : ''}`;
    identity.createDiv({ cls: 'so-hp', text: hp });
    const hpControls = head.createDiv({ cls: 'so-inline-controls so-inspector-hp' });
    if (target.target_id || target.instance_id) {
      const hpTarget = Object.assign({}, target, { instance_id: target.target_id || target.instance_id });
      this.button(hpControls, 'Damage', () => this.plugin.adjustCombatantHp(hpTarget, 'damage'));
      this.button(hpControls, 'Heal', () => this.plugin.adjustCombatantHp(hpTarget, 'heal'));
    } else if (target.is_pc) {
      this.button(hpControls, 'Damage', () => this.plugin.adjustPartyHp(target.name, 'damage'));
      this.button(hpControls, 'Heal', () => this.plugin.adjustPartyHp(target.name, 'heal'));
    }
  }

  renderRollResult(card) {
    const result = this.plugin.lastRoll;
    if (!result) return;
    const rollBox = card.createDiv({ cls: 'so-roll-result' });
    rollBox.createDiv({ cls: 'so-roll-total', text: String(result.total ?? '—') });
    const detail = [result.label, result.formula, result.dc != null ? `DC ${result.dc}` : '', degreeLabel(result.degree)].filter(Boolean).join(' · ');
    rollBox.createDiv({ text: detail });
    if (result.flat_check) {
      rollBox.createDiv({ cls: 'so-meta', text: `Persistent flat check: ${result.flat_check.total} vs DC 15 · ${degreeLabel(result.flat_check.degree)}${result.ended ? ' · ended' : ''}` });
    }
  }

  renderInspectorOverview(panel, target) {
    const stats = panel.createDiv({ cls: 'so-stat-grid' });
    [['AC', target.ac], ['Fort', target.fort], ['Ref', target.ref], ['Will', target.will], ['Perception', target.perception], ['Speed', target.speed]].forEach(([label, value]) => {
      const stat = stats.createDiv({ cls: 'so-stat' });
      stat.createDiv({ cls: 'so-stat-value', text: value == null ? '—' : String(value) });
      stat.createDiv({ cls: 'so-meta', text: label });
    });
    if (target.is_pc) {
      const resources = panel.createDiv({ cls: 'so-resource-line' });
      resources.createSpan({ text: `Hero ${target.hero_points ?? 0}` });
      resources.createSpan({ text: `Focus ${target.focus ?? target.focus_current ?? 0}/${target.focus_max ?? target.focus_pool ?? 0}` });
      resources.createSpan({ text: target.reaction_used ? 'Reaction spent' : 'Reaction ready' });
    }
    const rolls = panel.createDiv({ cls: 'so-inline-controls so-roll-controls' });
    this.button(rolls, 'Initiative', () => this.plugin.rollInitiative(this.rollPayload(target, { roll_kind: 'initiative' })));
    this.button(rolls, `Perception ${signed(target.perception)}`, () => this.plugin.roll(this.rollPayload(target, { roll_kind: 'perception' })));
    this.button(rolls, `Fort ${signed(target.fort)}`, () => this.plugin.roll(this.rollPayload(target, { roll_kind: 'save', save: 'fortitude' })));
    this.button(rolls, `Ref ${signed(target.ref)}`, () => this.plugin.roll(this.rollPayload(target, { roll_kind: 'save', save: 'reflex' })));
    this.button(rolls, `Will ${signed(target.will)}`, () => this.plugin.roll(this.rollPayload(target, { roll_kind: 'save', save: 'will' })));
    const skills = target.skills || [];
    if (skills.length) {
      panel.createEl('h5', { text: 'Skills' });
      const skillGrid = panel.createDiv({ cls: 'so-skill-grid' });
      skills.forEach((skill) => {
        this.button(skillGrid, `${skill.name} ${signed(Number.parseInt(skill.total, 10) || 0)}`, () => this.plugin.roll(this.rollPayload(target, { roll_kind: 'skill', skill: skill.name })));
      });
    }
    if (!target.is_pc) {
      [['Weaknesses', target.weaknesses], ['Resistances', target.resistances], ['Immunities', target.immunities]].forEach(([label, values]) => {
        if (!values?.length) return;
        const row = panel.createDiv({ cls: 'so-defense-line' });
        row.createEl('strong', { text: `${label}: ` });
        row.createSpan({ text: values.join(', ') });
      });
    }
  }

  renderInspectorOffense(panel, target) {
    const strikes = target.strikes || [];
    if (!strikes.length) {
      panel.createDiv({ cls: 'so-empty', text: 'No strikes are available in the website combatant data.' });
      return;
    }
    strikes.forEach((strike) => {
      const row = panel.createDiv({ cls: 'so-action-card' });
      const head = row.createDiv({ cls: 'so-action-head' });
      head.createEl('strong', { text: strike.name || 'Strike' });
      head.createSpan({ cls: 'so-meta', text: strike.damage || '' });
      const controls = row.createDiv({ cls: 'so-inline-controls' });
      [0, 1, 2].forEach((stage) => this.button(controls, stage ? `Attack MAP ${stage + 1}` : 'Attack', () => this.plugin.roll(this.rollPayload(target, {
        roll_kind: 'attack', strike_name: strike.name, map_stage: stage,
      }))));
      this.button(controls, 'Damage', () => this.plugin.rollDamage(this.rollPayload(target, {
        roll_kind: 'damage', strike_name: strike.name,
      }), firstDiceExpression(strike.damage)));
    });
  }

  renderInspectorSpells(panel, target) {
    const casters = target.is_pc ? (target.spell_casters || []) : (target.spellcasting || []);
    if (!casters.length) {
      panel.createDiv({ cls: 'so-empty', text: 'No spellcasting is available for this combatant.' });
      return;
    }
    const spellHeader = panel.createDiv({ cls: 'so-resource-line' });
    spellHeader.createSpan({ text: `Spell attack ${signed(target.spell_attack)}` });
    spellHeader.createSpan({ text: `Spell DC ${target.spell_dc || '—'}` });
    casters.forEach((caster) => {
      const group = panel.createDiv({ cls: 'so-spell-group' });
      group.createEl('h5', { text: [caster.name, caster.tradition, caster.type].filter(Boolean).join(' · ') });
      const spells = target.is_pc
        ? (caster.levels || []).flatMap((level) => (level.spells || []).map((spell) => Object.assign({ rank: level.level }, spell)))
        : (caster.spells || []);
      spells.forEach((spell) => {
        const row = group.createDiv({ cls: 'so-spell-row' });
        const main = row.createDiv();
        main.createEl('strong', { text: spell.name });
        main.createDiv({ cls: 'so-meta', text: [`Rank ${spell.rank ?? '?'}`, spell.actions ? `${spell.actions} action${spell.actions === '1' ? '' : 's'}` : '', spell.save ? `${conditionLabel(spell.save)} save` : ''].filter(Boolean).join(' · ') });
        const controls = row.createDiv({ cls: 'so-inline-controls' });
        this.button(controls, 'Attack', () => this.plugin.roll(this.rollPayload(target, { roll_kind: 'spell_attack', spell_name: spell.name })));
        const formula = spell.damage?.[0]?.formula || '';
        this.button(controls, 'Damage', () => this.plugin.rollDamage(this.rollPayload(target, {
          roll_kind: 'spell_damage', spell_name: spell.name, damage_type: spell.damage?.[0]?.type || 'untyped',
        }), formula));
      });
    });
  }

  renderInspectorAbilities(panel, target) {
    if (!target.is_pc && target.tactics) {
      const tactics = panel.createDiv({ cls: 'so-tactics' });
      tactics.createEl('strong', { text: 'Tactics' });
      tactics.createDiv({ text: target.tactics });
    }
    const collections = target.is_pc
      ? [['Reactions', target.reactions], ['Important feats', target.feats]]
      : [['Reactions', target.reactions], ['Abilities', target.actions]];
    let any = false;
    collections.forEach(([label, values]) => {
      if (!values?.length) return;
      any = true;
      panel.createEl('h5', { text: label });
      values.forEach((item) => {
        const row = panel.createDiv({ cls: 'so-ability-card' });
        row.createEl('strong', { text: item.name || 'Ability' });
        const description = item.description || item.desc;
        if (description) row.createDiv({ text: description });
      });
    });
    if (!any && !target.tactics) panel.createDiv({ cls: 'so-empty', text: 'No abilities or reactions are available.' });
  }

  renderInspectorConditions(panel, target) {
    const targetId = target.target_id || target.instance_id;

    // Duration picker. Sticky rather than per-application on purpose: at the
    // table you set "3 rounds" once for a spell and then tap several targets.
    // Defaults to none, so the common case costs no extra taps and behaves
    // exactly as before.
    const durations = panel.createDiv({ cls: 'so-duration-row' });
    durations.createDiv({ cls: 'so-duration-label', text: 'Duration' });
    [0, 1, 2, 3, 10].forEach((n) => {
      const chip = durations.createDiv({
        cls: 'so-duration-chip' + (this.conditionRounds === n ? ' is-selected' : ''),
        text: n === 0 ? 'none' : `${n}r`,
      });
      chip.addEventListener('click', () => {
        this.conditionRounds = n;
        this.render();
      });
    });

    const conditions = panel.createDiv({ cls: 'so-condition-list' });
    const shown = [...new Set([...Object.keys(target.conditions || {}), 'frightened', 'sickened', 'stunned', 'slowed', 'prone', 'off_guard'])]
      .filter((name) => NUMERIC_CONDITIONS.includes(name) || BOOLEAN_CONDITIONS.includes(name));
    shown.forEach((name) => {
      const row = conditions.createDiv({ cls: 'so-condition-row' });
      row.createDiv({ text: conditionLabel(name) });
      const value = target.conditions?.[name] || 0;
      row.createDiv({ cls: 'so-condition-value', text: typeof value === 'boolean' ? (value ? 'On' : 'Off') : String(value) });
      const controls = row.createDiv({ cls: 'so-inline-controls' });
      const rounds = this.conditionRounds || 0;
      if (targetId && BOOLEAN_CONDITIONS.includes(name)) {
        this.button(controls, value ? 'Remove' : 'Add', () => this.plugin.conditionAction(targetId, name, value ? 'remove' : 'add', rounds));
      } else if (targetId) {
        this.button(controls, '−', () => this.plugin.conditionAction(targetId, name, 'decrease'));
        this.button(controls, '+', () => this.plugin.conditionAction(targetId, name, 'increase', rounds));
      }
      const expiry = (target.condition_expiry || {})[name];
      if (expiry) row.createDiv({ cls: 'so-condition-expiry', text: `${expiry}r left` });
    });
    const persistent = target.persistent_damage_list || target.persistent_damage || [];
    const entries = Array.isArray(persistent)
      ? persistent
      : (persistent ? [{ damage: firstDiceExpression(persistent), type: String(persistent).replace(firstDiceExpression(persistent), '').trim() }] : []);
    if (entries.length) {
      panel.createEl('h5', { text: 'Persistent damage' });
      entries.forEach((entry, index) => {
        const row = panel.createDiv({ cls: 'so-action-card' });
        row.createSpan({ text: `${entry.damage || '?'} ${entry.type || ''}`.trim() });
        this.button(row, 'Roll damage + flat check', () => this.plugin.roll(this.rollPayload(target, {
          roll_kind: 'persistent_damage', persistent_index: index, damage_type: entry.type || 'untyped',
        })));
      });
    }
    if (Number(target.conditions?.dying || 0) > 0) {
      const row = panel.createDiv({ cls: 'so-inline-controls' });
      this.button(row, 'Recovery check', () => this.plugin.roll(this.rollPayload(target, { roll_kind: 'recovery' })));
    }
  }

  renderPlayerRequests(root) {
    const requests = this.plugin.state?.operations?.player_requests || [];
    const open = requests.filter((item) => item.status === 'open');
    const section = root.createDiv({ cls: 'so-section' });
    const title = section.createDiv({ cls: 'so-section-title' });
    title.createEl('h3', { text: `Player request inbox${open.length ? ` (${open.length})` : ''}` });
    if (!open.length) {
      section.createDiv({ cls: 'so-empty', text: 'No open player whispers or requests.' });
      return;
    }
    open.slice().reverse().forEach((request) => {
      const card = section.createDiv({ cls: 'so-request-card' });
      const head = card.createDiv({ cls: 'so-card-heading' });
      head.createEl('strong', { text: request.pc_name || 'Player' });
      head.createSpan({ cls: 'so-meta', text: request.created_at || '' });
      card.createDiv({ text: request.text || '' });
      const controls = card.createDiv({ cls: 'so-inline-controls' });
      this.button(controls, 'Resolve', () => this.plugin.resolvePlayerRequest(request, 'resolve'));
      this.button(controls, 'Dismiss', () => this.plugin.resolvePlayerRequest(request, 'dismiss'));
      this.button(controls, 'Capture', () => this.plugin.captureNote(`${request.pc_name}: ${request.text}`));
    });
  }

  renderCapture(root) {
    const section = root.createDiv({ cls: 'so-section so-capture' });
    const title = section.createDiv({ cls: 'so-section-title' });
    title.createEl('h3', { text: 'Quick session capture' });
    const textArea = section.createEl('textarea', {
      attr: { placeholder: 'Decision, consequence, reveal, treasure, NPC response, or follow-up...' },
    });
    const actions = section.createDiv({ cls: 'so-capture-actions' });
    this.button(actions, 'Record note', async () => {
      const text = textArea.value.trim();
      if (!text) return;
      await this.plugin.captureNote(text);
      textArea.value = '';
    });
  }
}

class SessionOpsSettingTab extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl('h2', { text: 'Session Operations Sync' });
    containerEl.createEl('p', {
      text: 'Create a campaign key at the website setup page, then enter its three connection values here.',
    });

    new Setting(containerEl)
      .setName('Website URL')
      .setDesc('The dashboard origin only, such as https://your-site.example or http://127.0.0.1:5001.')
      .addText((text) => text
        .setPlaceholder('http://127.0.0.1:5001')
        .setValue(this.plugin.settings.baseUrl)
        .onChange(async (value) => {
          this.plugin.settings.baseUrl = value.trim().replace(/\/+$/, '');
          await this.plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName('Campaign ID')
      .setDesc('The 32-character campaign ID shown on the website setup page.')
      .addText((text) => text
        .setPlaceholder('campaign id')
        .setValue(this.plugin.settings.campaignId)
        .onChange(async (value) => {
          this.plugin.settings.campaignId = value.trim();
          this.plugin.settings.lastEventSequence = 0;
          // Revision is per-campaign and only monotonic within one, so the new
          // campaign's revision may be lower than what we hold. adoptServerState
          // refuses anything older, which would wedge the pane permanently, so
          // clear the cached state with the event cursor.
          this.plugin.revision = 0;
          this.plugin.state = null;
          this.plugin.session = null;
          await this.plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName('Connection token')
      .setDesc('Campaign-scoped and revocable. Obsidian stores it locally in this plugin’s data file.')
      .addText((text) => {
        text.setPlaceholder('obs1_...').setValue(this.plugin.settings.token).onChange(async (value) => {
          this.plugin.settings.token = value.trim();
          await this.plugin.saveSettings();
        });
        text.inputEl.type = 'password';
      });

    new Setting(containerEl)
      .setName('Session data folder')
      .setDesc('Structured snapshots, JSONL events, and materialized Session Record notes are written here.')
      .addText((text) => text
        .setPlaceholder('_Session Data')
        .setValue(this.plugin.settings.sessionDataFolder)
        .onChange(async (value) => {
          this.plugin.settings.sessionDataFolder = normalizePath(value.trim() || '_Session Data');
          await this.plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName('Connection')
      .setDesc(this.plugin.connectionLabel())
      .addButton((button) => button.setButtonText('Test and sync').onClick(async () => {
        await this.plugin.syncNow();
        this.display();
      }))
      .addButton((button) => button.setButtonText('Open setup page').onClick(() => this.plugin.openSetupPage()));
  }
}

module.exports = class SessionOperationsSyncPlugin extends Plugin {
  async onload() {
    await this.loadSettings();
    this.state = null;
    this.session = null;
    this.revision = 0;
    this.connected = false;
    this.lastError = '';
    this.polling = false;
    this.lastPollAt = 0;
    this.lastRoll = null;
    // On-demand statblock cache: instance_id/name -> {detail_key, data}.
    this.detailCache = new Map();
    this.detailInFlight = new Set();
    this.currentRoom = null;

    this.registerView(VIEW_TYPE, (leaf) => new SessionOperationsView(leaf, this));
    this.addRibbonIcon('swords', 'Open Session Operations', () => this.activateView());
    this.addCommand({ id: 'open-session-operations', name: 'Open Session Operations', callback: () => this.activateView() });
    this.addCommand({ id: 'sync-session-operations', name: 'Sync Session Operations now', callback: () => this.syncNow() });
    this.addCommand({ id: 'start-session-operations', name: 'Start structured session', callback: () => this.startSession() });
    this.addCommand({ id: 'end-session-operations', name: 'End structured session', callback: () => this.endSession() });
    this.addCommand({ id: 'enter-current-room', name: 'Enter current room', callback: () => this.enterCurrentRoom() });
    this.addCommand({ id: 'reveal-current-selection', name: 'Reveal selected text to players', callback: () => this.revealCurrentSelection() });
    this.addSettingTab(new SessionOpsSettingTab(this.app, this));

    this.registerEvent(this.app.workspace.on('file-open', () => this.refreshCurrentRoom()));
    this.registerEvent(this.app.metadataCache.on('changed', (file) => {
      if (file?.path === this.app.workspace.getActiveFile()?.path) this.refreshCurrentRoom();
    }));
    this.app.workspace.onLayoutReady(() => {
      this.refreshCurrentRoom();
      this.announcePendingCommands();
    });

    const interval = window.setInterval(() => this.pollIfDue(), 1000);
    this.registerInterval(interval);
  }

  /**
   * Say out loud that queued commands are waiting.
   *
   * The queue persists correctly across a reload and drains in order, but
   * nothing drained it on load and nothing mentioned it: no notice, no ribbon
   * badge, no status anywhere outside the sidebar pane. A GM who closed Obsidian
   * mid-session came back to a plugin that looked idle while still holding
   * unapplied table actions.
   *
   * Deliberately announce rather than auto-apply. The queue exists because the
   * website was unreachable, so every entry is by definition stale, and the
   * offline toast already promises the command "was not silently applied".
   * Draining it without the GM looking would break that promise.
   */
  announcePendingCommands() {
    const count = this.settings.pendingCommands.length;
    if (!count) return;
    new Notice(
      `Session Operations has ${count} pending command${count === 1 ? '' : 's'} from an earlier outage. `
      + 'Open the pane to review and apply them.',
      10000,
    );
  }

  async onunload() {
    this.app.workspace.detachLeavesOfType(VIEW_TYPE);
  }

  async loadSettings() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
    if (!Array.isArray(this.settings.pendingCommands)) this.settings.pendingCommands = [];
  }

  async saveSettings() {
    await this.saveData(this.settings);
    this.renderViews();
  }

  isConfigured() {
    return Boolean(this.settings.baseUrl && this.settings.campaignId && this.settings.token);
  }

  connectionLabel() {
    if (!this.isConfigured()) return 'Not configured';
    if (this.lastError) return this.lastError;
    return this.connected ? 'Connected' : 'Not connected';
  }

  renderViews() {
    this.app.workspace.getLeavesOfType(VIEW_TYPE).forEach((leaf) => leaf.view.render());
  }

  async activateView() {
    let leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      leaf = this.app.workspace.getRightLeaf(false);
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    this.app.workspace.revealLeaf(leaf);
    await this.syncNow({ quiet: true });
  }

  openSetupPage() {
    const base = (this.settings.baseUrl || 'http://127.0.0.1:5001').replace(/\/+$/, '');
    window.open(`${base}/gm/integrations/obsidian`);
  }

  absoluteWebsiteUrl(path) {
    if (!path) return '';
    if (/^https?:\/\//i.test(path)) return path;
    return `${this.settings.baseUrl.replace(/\/+$/, '')}/${String(path).replace(/^\/+/, '')}`;
  }

  async refreshCurrentRoom() {
    const file = this.app.workspace.getActiveFile();
    if (!file || file.extension !== 'md') {
      this.currentRoom = null;
      this.renderViews();
      return;
    }
    const cache = this.app.metadataCache.getFileCache(file);
    const frontmatter = cache?.frontmatter || {};
    if (String(frontmatter.type || '').toLowerCase() !== 'location' || !frontmatter.area_id) {
      this.currentRoom = null;
      this.renderViews();
      return;
    }
    const room = {
      file,
      path: file.path,
      areaId: String(frontmatter.area_id),
      title: String(frontmatter.title || file.basename),
      creatures: Array.isArray(frontmatter.creatures) ? frontmatter.creatures : [],
      npcs: Array.isArray(frontmatter.npcs) ? frontmatter.npcs : [],
      sessionOps: frontmatter.session_ops && typeof frontmatter.session_ops === 'object' ? frontmatter.session_ops : {},
      npcCards: [],
    };
    for (const npcName of room.npcs.slice(0, 20)) {
      const card = await this.loadNpcCard(String(npcName), file.path);
      if (card) room.npcCards.push(card);
    }
    this.currentRoom = room;
    this.renderViews();
  }

  async loadNpcCard(name, sourcePath) {
    let file = this.app.metadataCache.getFirstLinkpathDest(name, sourcePath);
    if (!file) {
      const normalized = name.toLowerCase();
      file = this.app.vault.getMarkdownFiles().find((candidate) => candidate.basename.toLowerCase() === normalized);
    }
    if (!file) return { name, role: '', status: '', excerpt: '', file: null };
    const cache = this.app.metadataCache.getFileCache(file);
    const frontmatter = cache?.frontmatter || {};
    const content = await this.app.vault.cachedRead(file);
    const portraitMatch = content.match(/!\[\[([^\]|]+\.(?:png|jpe?g|webp|gif))(?:\|[^\]]+)?\]\]/i);
    let portraitFile = null;
    let portraitUrl = '';
    if (portraitMatch) {
      portraitFile = this.app.metadataCache.getFirstLinkpathDest(portraitMatch[1], file.path)
        || this.app.vault.getAbstractFileByPath(normalizePath(portraitMatch[1]));
      if (portraitFile) portraitUrl = this.app.vault.getResourcePath(portraitFile);
    }
    const roleSection = content.match(/##\s+(?:Role\s*&\s*Motives|Roleplaying[^\n]*|At the Table)[^\n]*\n([\s\S]*?)(?=\n##\s|$)/i);
    return {
      name: String(frontmatter.name || file.basename),
      role: String(frontmatter.role || ''),
      status: String(frontmatter.status || ''),
      excerpt: plainExcerpt(roleSection?.[1] || content),
      file,
      portraitFile,
      portraitUrl,
    };
  }

  localPortraitUrl(name) {
    const normalized = String(name || '').toLowerCase().replace(/\s+\d+$/, '');
    const card = this.currentRoom?.npcCards?.find((npc) => {
      const candidate = String(npc.name || '').toLowerCase();
      return candidate === normalized || candidate.includes(normalized) || normalized.includes(candidate);
    });
    return card?.portraitUrl || '';
  }

  async openVaultFile(file) {
    if (!file) return;
    await this.app.workspace.getLeaf(false).openFile(file);
  }

  async api(path, options = {}) {
    if (!this.isConfigured()) throw new SyncApiError('Connection settings are incomplete', 0, {});
    const method = options.method || 'GET';
    const headers = {
      Authorization: `Bearer ${this.settings.token}`,
      'X-Campaign-ID': this.settings.campaignId,
      Accept: 'application/json',
    };
    let body;
    if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(options.body);
    }
    let response;
    try {
      response = await requestUrl({
        url: `${this.settings.baseUrl.replace(/\/+$/, '')}${API_PREFIX}${path}`,
        method,
        headers,
        body,
        throw: false,
      });
    } catch (error) {
      throw new SyncApiError(`Offline: ${error.message || 'website unavailable'}`, 0, {});
    }
    let parsed = {};
    try { parsed = response.json; } catch (_) {
      try { parsed = JSON.parse(response.text || '{}'); } catch (_) { parsed = {}; }
    }
    if (response.status < 200 || response.status >= 300) {
      throw new SyncApiError(parsed.error || `Website returned ${response.status}`, response.status, parsed);
    }
    return parsed;
  }

  viewIsOpen() {
    return this.app.workspace.getLeavesOfType(VIEW_TYPE).length > 0;
  }

  async pollIfDue() {
    if (!this.isConfigured() || this.polling) return;
    const desired = this.viewIsOpen() ? this.settings.visiblePollMs : this.settings.backgroundPollMs;
    if (Date.now() - this.lastPollAt < desired) return;
    await this.syncNow({ quiet: true });
  }

  async syncNow({ quiet = false } = {}) {
    if (!this.isConfigured() || this.polling) {
      if (!quiet && !this.isConfigured()) new Notice('Complete the Session Operations Sync settings first.');
      return;
    }
    this.polling = true;
    const wasConnected = this.connected;
    const priorRevision = this.revision;
    // Stamp the attempt, not the success. This used to sit inside the try after
    // the request returned, so every failure path skipped it, the elapsed-time
    // gate in pollIfDue passed on every 1s tick, and an outage became a 1 Hz
    // retry storm -- with backgroundPollMs dead exactly when it mattered most.
    this.lastPollAt = Date.now();
    try {
      const response = await this.api('/state');
      this.adoptServerState(response);
      this.connected = true;
      this.lastError = '';
      await this.pullEvents();
      if (this.revision !== priorRevision) await this.persistSnapshot('poll');
      if (!quiet) new Notice('Session Operations is synchronized.');
    } catch (error) {
      this.connected = false;
      this.lastError = error.message;
      if (!quiet) new Notice(error.message);
    } finally {
      this.polling = false;
      if (this.revision !== priorRevision || wasConnected !== this.connected || !quiet) this.renderViews();
    }
  }

  async sendCommand(type, payload, { queueOnOffline = true, endpoint = '/commands' } = {}) {
    const command = {
      command_id: commandId(type),
      expected_revision: this.revision,
      type,
      payload,
    };
    try {
      const response = await this.api(endpoint, { method: 'POST', body: command });
      await this.acceptCommandResponse(response);
      return response;
    } catch (error) {
      if (error.status === 409 && error.body?.conflict) {
        this.state = error.body.state || this.state;
        this.session = error.body.session || this.session;
        this.revision = error.body.current_revision ?? this.revision;
        this.renderViews();
        new Notice('Website state changed first. The pane refreshed; repeat the action if it is still needed.');
        return null;
      }
      if (error.status === 0 && queueOnOffline) {
        this.settings.pendingCommands.push(command);
        await this.saveSettings();
        new Notice('Website unavailable. The command is pending review and was not silently applied.');
        return null;
      }
      new Notice(error.message);
      throw error;
    }
  }

  /**
   * Adopt a server payload only when it is not older than what we already hold.
   *
   * Poll and command responses race. `this.polling` guards syncNow against
   * itself but never covered sendCommand, so a /state reply computed BEFORE a
   * command was applied could land after the command response and rewind
   * this.state/this.session/this.revision. Nothing showed it: priorRevision is
   * captured before the request, so the repaint gate saw no change and
   * suppressed the redraw, leaving a correct-looking pane over a stale model.
   * The next command then stamped the rewound expected_revision and 409'd for a
   * conflict that never happened -- and repeating the action, as the toast
   * advised, applied it twice.
   *
   * Server revision is monotonic per campaign, so "not older" is the whole
   * test. Campaign switches reset it deliberately (see the Campaign ID setting).
   */
  /** Cached statblock for a combatant, or null. Never fetches. */
  detailFor(id) {
    const entry = id ? this.detailCache.get(String(id)) : null;
    return entry ? entry.data : null;
  }

  /**
   * Fetch a combatant's statblock if we do not have a current one.
   *
   * Fired from render, so it must be cheap and idempotent: it returns
   * immediately when the cache is warm, and an in-flight set stops a repaint
   * loop from firing the same request several times. Nothing prefetches -- a GM
   * clicking through nine combatants would otherwise fire nine statblock builds
   * on the worker that also serves the players' SSE.
   */
  ensureDetail(target) {
    const id = target?.target_id || target?.instance_id || target?.name;
    if (!id || !this.isConfigured()) return;
    const key = String(id);
    const cached = this.detailCache.get(key);
    // detail_key changes when the statblock does -- an elite/weak toggle, a
    // level change, a new strike. Without it a cached card would stay
    // confidently wrong.
    if (cached && (!target.detail_key || cached.detail_key === target.detail_key)) return;
    if (this.detailInFlight.has(key)) return;
    this.detailInFlight.add(key);
    this.api(`/combatant/${encodeURIComponent(key)}`)
      .then((response) => {
        if (response?.detail) {
          this.detailCache.set(key, { detail_key: target.detail_key || null, data: response.detail });
          this.renderViews();
        }
      })
      .catch(() => {
        // Silent: the snapshot still carries these fields, so a failed detail
        // fetch costs richness, not function. Surfacing it would put an error
        // toast in front of the GM for something they cannot act on.
      })
      .finally(() => this.detailInFlight.delete(key));
  }

  adoptServerState(response) {
    const incoming = Number(response?.revision ?? NaN);
    if (!Number.isFinite(incoming) || incoming < this.revision) return false;
    if (response.state) this.state = response.state;
    this.session = response.session ?? this.session;
    this.revision = incoming;
    return true;
  }

  async acceptCommandResponse(response) {
    this.connected = true;
    this.lastError = '';
    // An idempotent replay is the server telling us this command_id already ran:
    // the body is the one captured at ORIGINAL execution, so its revision and
    // state are as old as the command. Adopting them rewound the pane and wrote
    // a stale snapshot into the vault. Take the acknowledgement, not the payload,
    // and let the poll that follows carry the real current state.
    if (response?.idempotent_replay) {
      await this.pullEvents();
      this.renderViews();
      return;
    }
    this.adoptServerState(response);
    await this.pullEvents();
    await this.persistSnapshot('command');
    this.renderViews();
  }

  async retryPending() {
    if (!this.settings.pendingCommands.length) return;
    const modal = new ConfirmModal(
      this.app,
      'Apply pending commands?',
      'Each queued command will be reviewed against the current website state and applied in order. Stop if any command is no longer appropriate.',
      'Apply pending',
    );
    modal.open();
    if (!(await modal.promise)) return;
    await this.syncNow({ quiet: true });
    const pending = [...this.settings.pendingCommands];
    this.settings.pendingCommands = [];
    for (let index = 0; index < pending.length; index += 1) {
      const command = pending[index];
      command.expected_revision = this.revision;
      try {
        const response = await this.api('/commands', { method: 'POST', body: command });
        await this.acceptCommandResponse(response);
      } catch (error) {
        this.settings.pendingCommands.push(command, ...pending.slice(index + 1));
        await this.saveSettings();
        new Notice(`Pending command stopped: ${error.message}`);
        return;
      }
    }
    await this.saveSettings();
    new Notice('Pending commands applied.');
  }

  async adjustCombatantHp(target, action) {
    const modal = new NumberPromptModal(this.app, `${conditionLabel(action)} ${target.name}`, conditionLabel(action));
    modal.open();
    const amount = await modal.promise;
    if (!amount) return;
    await this.sendCommand('adjust_hp', { target_id: target.instance_id, action, amount, damage_type: 'untyped' });
  }

  async adjustPartyHp(pcName, action) {
    const modal = new NumberPromptModal(this.app, `${conditionLabel(action)} ${pcName}`, conditionLabel(action));
    modal.open();
    const amount = await modal.promise;
    if (!amount) return;
    await this.sendCommand('adjust_party_hp', { pc_name: pcName, action, amount });
  }

  async conditionAction(targetId, condition, action, rounds = 0) {
    // `rounds` drives the website's auto-expiry timer. It was never sent, so a
    // "frightened 2 for 3 rounds" applied from Obsidian sat on the combatant
    // forever and the GM had to remember to clear it. The server has always
    // accepted and clamped the field (services/obsidian_sync.py, condition_action).
    //
    // Only meaningful when a condition is going ON: decrease/remove carry no
    // duration, and sending one would set a timer on a condition being cleared.
    const payload = { target_id: targetId, condition, action };
    const timed = ['add', 'increase'].includes(action) && Number(rounds) > 0;
    if (timed) payload.rounds = Math.max(1, Math.min(Math.round(Number(rounds)), 1000));
    await this.sendCommand('condition_action', payload);
  }

  async setInitiative(targetId, value) {
    const initiative = Number.parseInt(value, 10);
    if (!Number.isFinite(initiative)) {
      new Notice('Initiative must be an integer.');
      return;
    }
    await this.sendCommand('set_initiative', { target_id: targetId, initiative });
  }

  async advanceTurn(direction) {
    await this.sendCommand('advance_turn', { direction });
  }

  async roll(payload) {
    const response = await this.sendCommand('roll', payload, { queueOnOffline: false });
    if (!response) return null;
    this.lastRoll = response.result;
    this.renderViews();
    const degree = degreeLabel(response.result?.degree);
    new Notice(`${response.result?.label || 'Roll'}: ${response.result?.total ?? '—'}${degree ? ` · ${degree}` : ''}`);
    return response.result;
  }

  async rollInitiative(payload) {
    const modal = new TextPromptModal(this.app, 'Roll initiative', 'Perception or skill name', 'Perception');
    modal.open();
    const statistic = await modal.promise;
    if (!statistic) return;
    await this.roll(Object.assign({}, payload, { statistic }));
  }

  async rollDamage(payload, suggestedFormula = '') {
    let formula = suggestedFormula;
    if (!formula) {
      const modal = new TextPromptModal(this.app, 'Damage roll', 'Dice formula, such as 2d6+4');
      modal.open();
      formula = await modal.promise;
      if (!formula) return;
    }
    await this.roll(Object.assign({}, payload, { formula }));
  }

  async clearActiveRoom() {
    const response = await this.sendCommand('clear_active_room', {}, { queueOnOffline: false });
    if (response) new Notice('Left the room. Nothing is live.');
  }

  /**
   * Drop creatures named in the open note into the encounter already running.
   *
   * Additive: it never clears the board or re-adds the party, because the case
   * this exists for is reinforcements arriving mid-fight. Ambiguous names come
   * back with candidates rather than a guess -- adding the wrong monster in the
   * middle of a round is worse than being asked which one.
   */
  async addCreaturesToEncounter(creatures) {
    if (!creatures?.length) return;
    const response = await this.sendCommand('add_creatures', { creatures }, { queueOnOffline: false });
    if (!response) return;
    const { added = [], unresolved = [] } = response.result || {};
    if (added.length) new Notice(`Added ${added.join(', ')}`);
    unresolved.forEach((miss) => {
      const options = (miss.candidates || []).map((c) => c.name).slice(0, 4);
      new Notice(
        options.length
          ? `"${miss.name}" matched several: ${options.join(', ')}. Name it exactly in the note.`
          : `"${miss.name}" is not in the bestiary.`,
        8000,
      );
    });
  }

  async enterCurrentRoom() {
    const room = this.currentRoom;
    if (!room) {
      new Notice('Open a location note with an area_id first.');
      return;
    }
    const active = this.state?.operations?.active_room;
    if (active && active.area_id !== room.areaId) {
      const modal = new ConfirmModal(
        this.app,
        `Enter ${room.title}?`,
        `The website currently records ${active.title} as the live room. This changes session context but does not launch combat.`,
        'Enter room',
      );
      modal.open();
      if (!(await modal.promise)) return;
    }
    const response = await this.sendCommand('set_active_room', {
      area_id: room.areaId,
      title: room.title,
      path: room.path,
      manifest_version: Number(room.sessionOps?.version || 1),
    }, { queueOnOffline: false });
    if (response) new Notice(`${room.title} is now the live room.`);
  }

  async launchCurrentRoomEncounter() {
    const room = this.currentRoom;
    const encounter = room?.sessionOps?.encounter || {};
    if (!room || (!encounter.template && !Array.isArray(encounter.monsters))) {
      new Notice('This room needs an explicit session_ops encounter template or roster.');
      return;
    }
    const activeCombatants = this.state?.encounter?.combatants || [];
    const roster = Array.isArray(encounter.monsters) ? encounter.monsters : [];
    const preview = encounter.template
      ? `saved stage “${encounter.template}”`
      : roster.map((row) => `${row.count || 1}× ${row.name || row.path || 'Unknown'}`).join(', ');
    const detail = `${preview}. ${encounter.add_party === false ? 'The party will not be added.' : 'The current party will be added.'}${activeCombatants.length ? ` This replaces the active ${activeCombatants.length}-combatant encounter.` : ''}`;
    const modal = new ConfirmModal(this.app, `Launch ${room.title}?`, detail, activeCombatants.length ? 'Replace encounter' : 'Launch encounter');
    modal.open();
    if (!(await modal.promise)) return;
    const response = await this.sendCommand('launch_room_encounter', {
      area_id: room.areaId,
      encounter_template: encounter.template || '',
      monsters: roster,
      add_party: encounter.add_party !== false,
      notes: encounter.notes || `Launched from ${room.path}`,
      replace_active: activeCombatants.length > 0,
    }, { queueOnOffline: false });
    if (response) {
      if (Array.isArray(room.sessionOps.round_reminders) && room.sessionOps.round_reminders.length) {
        await this.syncCurrentRoomReminders();
      }
      new Notice(`Encounter launched with ${response.result?.combatant_count || 0} combatants.`);
    }
  }

  async syncCurrentRoomReminders() {
    const room = this.currentRoom;
    const reminders = room?.sessionOps?.round_reminders;
    if (!room || !Array.isArray(reminders)) {
      new Notice('This room has no round reminders.');
      return;
    }
    const response = await this.sendCommand('sync_room_reminders', {
      area_id: room.areaId,
      reminders,
    }, { queueOnOffline: false });
    if (response) new Notice(`${response.result?.reminder_count || 0} room reminders synchronized.`);
  }

  async captureNote(text) {
    const response = await this.sendCommand('capture_note', { text, category: 'table_note' });
    if (response) new Notice('Session note recorded.');
  }

  async resolvePlayerRequest(request, action) {
    let resolution = '';
    if (action === 'resolve') {
      const modal = new TextPromptModal(this.app, `Resolve ${request.pc_name || 'player'} request`, 'Resolution note', 'Handled at the table');
      modal.open();
      resolution = await modal.promise;
      if (resolution === null) return;
    }
    const response = await this.sendCommand('resolve_player_request', {
      request_id: request.id,
      action,
      resolution: resolution || '',
    }, { queueOnOffline: false });
    if (response) new Notice(`Player request ${action === 'resolve' ? 'resolved' : 'dismissed'}.`);
  }

  async revealCurrentSelection() {
    const view = this.app.workspace.getActiveViewOfType(MarkdownView);
    const content = view?.editor?.getSelection()?.trim();
    if (!content) {
      new Notice('Select text in a Markdown note first.');
      return;
    }
    const modal = new TextPromptModal(this.app, 'Reveal selected text', 'Handout title', this.currentRoom?.title || view.file?.basename || 'Reveal');
    modal.open();
    const title = await modal.promise;
    if (!title) return;
    await this.confirmAndSendReveal({
      title,
      content,
      source: view.file?.path || 'Obsidian selection',
    });
  }

  async revealPreset(reveal) {
    await this.confirmAndSendReveal({
      title: String(reveal.title || 'Room reveal'),
      content: String(reveal.content || reveal.text || ''),
      source: this.currentRoom?.path || 'Room manifest',
    });
  }

  async revealNpcPortrait(npc) {
    if (!npc.portraitFile) return;
    const extension = String(npc.portraitFile.extension || '').toLowerCase();
    const mime = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', gif: 'image/gif' }[extension];
    if (!mime) {
      new Notice('That portrait format cannot be sent to the website.');
      return;
    }
    const binary = await this.app.vault.readBinary(npc.portraitFile);
    await this.confirmAndSendReveal({
      title: npc.name,
      content: npc.role || '',
      image: { mime, base64: arrayBufferToBase64(binary) },
      source: npc.file?.path || this.currentRoom?.path || 'NPC card',
    });
  }

  async confirmAndSendReveal(reveal) {
    const recipientsModal = new TextPromptModal(this.app, 'Player-facing reveal', 'Recipients: all, or comma-separated PC names', 'all');
    recipientsModal.open();
    const recipientText = await recipientsModal.promise;
    if (!recipientText) return;
    const recipients = recipientText.toLowerCase() === 'all'
      ? ['all']
      : recipientText.split(',').map((value) => value.trim()).filter(Boolean);
    const preview = `${reveal.title}\n\n${String(reveal.content || '').slice(0, 500)}${reveal.image ? '\n\nIncludes an image.' : ''}\n\nRecipients: ${recipients.join(', ')}`;
    const modal = new ConfirmModal(this.app, 'Send this reveal?', preview, 'Reveal to players');
    modal.open();
    if (!(await modal.promise)) return;
    const response = await this.sendCommand('create_player_reveal', Object.assign({}, reveal, { recipients }), { queueOnOffline: false });
    if (response) new Notice('Reveal sent to the website and recorded in the session stream.');
  }

  async startSession() {
    if (!this.isConfigured()) {
      new Notice('Complete the Session Operations Sync settings first.');
      return;
    }
    await this.syncNow({ quiet: true });
    const date = new Date().toISOString().slice(0, 10);
    const modal = new TextPromptModal(this.app, 'Start structured session', 'Session label', `Session ${date}`);
    modal.open();
    const label = await modal.promise;
    if (!label) return;
    const sessionId = `session-${date}-${Math.random().toString(36).slice(2, 8)}`;
    const response = await this.sendCommand(
      'start_session',
      { session_id: sessionId, label },
      { endpoint: '/sessions/start', queueOnOffline: false },
    );
    if (response) {
      await this.materializeSessionStart(response.session);
      new Notice(`${label} is live.`);
    }
  }

  async endSession() {
    if (!this.session || this.session.status !== 'live') return;
    const modal = new ConfirmModal(
      this.app,
      'End the live session?',
      'This captures exact party and encounter state and marks the package awaiting post-session processing.',
      'End session',
    );
    modal.open();
    if (!(await modal.promise)) return;
    const sessionId = this.session.id;
    const response = await this.sendCommand(
      'end_session',
      { session_id: sessionId },
      { endpoint: '/sessions/end', queueOnOffline: false },
    );
    if (response) {
      await this.materializeSessionEnd(response.session);
      new Notice('Session closed with exact state captured.');
    }
  }

  sessionFolder(sessionId) {
    const base = normalizePath(this.settings.sessionDataFolder || '_Session Data');
    return normalizePath(`${base}/${safeSegment(sessionId || '_Unassigned')}`);
  }

  async ensureFolder(path) {
    const normalized = normalizePath(path);
    const parts = normalized.split('/').filter(Boolean);
    let current = '';
    for (const part of parts) {
      current = current ? `${current}/${part}` : part;
      if (!this.app.vault.getAbstractFileByPath(current)) {
        try { await this.app.vault.createFolder(current); } catch (_) {}
      }
    }
  }

  async writeText(path, text) {
    const normalized = normalizePath(path);
    await this.ensureFolder(normalized.split('/').slice(0, -1).join('/'));
    const existing = this.app.vault.getAbstractFileByPath(normalized);
    if (existing) await this.app.vault.modify(existing, text);
    else await this.app.vault.create(normalized, text);
  }

  async appendText(path, text) {
    const normalized = normalizePath(path);
    await this.ensureFolder(normalized.split('/').slice(0, -1).join('/'));
    const existing = this.app.vault.getAbstractFileByPath(normalized);
    if (existing) await this.app.vault.append(existing, text);
    else await this.app.vault.create(normalized, text);
  }

  async persistSnapshot(reason) {
    if (!this.state) return;
    const sessionId = this.session?.id || '_Unassigned';
    const folder = this.sessionFolder(sessionId);
    await this.writeText(`${folder}/Website State.json`, JSON.stringify({
      schema_version: 2,
      captured_at: new Date().toISOString(),
      reason,
      revision: this.revision,
      event_sequence: this.settings.lastEventSequence,
      session: this.session,
      state: this.state,
    }, null, 2));
  }

  async pullEvents() {
    if (!this.connected) return;
    const after = Number(this.settings.lastEventSequence || 0);
    const response = await this.api(`/events?after=${after}&limit=500`);
    const events = response.events || [];
    for (const event of events) {
      const sessionId = event.session_id || this.session?.id || '_Unassigned';
      const folder = this.sessionFolder(sessionId);
      await this.appendText(`${folder}/Combat Events.jsonl`, `${JSON.stringify(event)}\n`);
      this.settings.lastEventSequence = Math.max(this.settings.lastEventSequence, Number(event.sequence || 0));
    }
    if (events.length) await this.saveSettings();
  }

  async materializeSessionStart(session) {
    const folder = this.sessionFolder(session.id);
    await this.ensureFolder(`${folder}/Sources`);
    const recordPath = `${folder}/Session Record.md`;
    if (!this.app.vault.getAbstractFileByPath(recordPath)) {
      const text = [
        '---',
        `session_id: ${yamlString(session.id)}`,
        `session_label: ${yamlString(session.label)}`,
        'status: live',
        `started_at: ${yamlString(session.started_at)}`,
        'ended_at:',
        // The vault's Session Operations Specification (:120) says audio stays
        // OUT of the vault and the Session Record holds paths, recording ids or
        // transcript links instead. The plugin never emitted the key, so the
        // post-session pass had nowhere to record what it reconciled against.
        'audio_sources: []',
        'schema_version: 2',
        '---',
        '',
        `# ${session.label}`,
        '',
        '## Live Captures',
        '',
        '> Structured website events are stored in `Combat Events.jsonl`. Add broader table notes here when useful.',
        '',
        '## Closing State',
        '',
        '_Captured automatically when the session ends._',
        '',
      ].join('\n');
      await this.writeText(recordPath, text);
    }
    await this.materializeHandoff(session, 'live');
    await this.persistSnapshot('session_start');
  }

  /**
   * Turn Combat Events.jsonl into prose a human will actually reread.
   *
   * The events have only ever existed as machine-readable JSONL, but the vault's
   * Post-Session Agent Workflow Specification already ranks structured website
   * events ABOVE the audio transcript as evidence for mechanical changes -- and
   * nothing produced them in readable form, so that lane sat empty and the
   * reconciliation pass leaned entirely on audio for facts the website knew
   * exactly.
   *
   * Grouped by round because that is how the table remembers a fight. Event ids
   * are carried through so a later pass can cite a specific line rather than
   * paraphrase it -- the same spec forbids filling gaps with plausible fiction.
   */
  async buildSessionDigest(session) {
    const folder = this.sessionFolder(session.id);
    const path = normalizePath(`${folder}/Combat Events.jsonl`);
    const file = this.app.vault.getAbstractFileByPath(path);
    if (!file) return null;
    let raw = '';
    try {
      raw = await this.app.vault.read(file);
    } catch (error) {
      return null;
    }
    const events = raw.split('\n').map((line) => {
      try { return line.trim() ? JSON.parse(line) : null; } catch (_) { return null; }
    }).filter(Boolean);
    if (!events.length) return null;

    const describe = (event) => {
      const p = event.payload || {};
      const r = event.result || {};
      const who = (event.before?.target || {}).name || p.pc_name || r.pc_name || '';
      switch (event.command_type) {
        case 'adjust_hp':
        case 'adjust_party_hp': {
          if (Array.isArray(r.targets)) {
            return `${r.action === 'heal' ? 'Healed' : 'Damaged'} ${r.targets.length} targets for ${r.raw_amount}`;
          }
          const delta = (r.old_hp != null && r.new_hp != null) ? Math.abs(r.new_hp - r.old_hp) : p.amount;
          const verb = r.action === 'heal' || p.action === 'heal' ? 'healed' : 'took';
          return `${who || 'Someone'} ${verb} ${delta}${r.new_hp != null ? ` (now ${r.new_hp})` : ''}` +
            (r.defeated ? ' -- defeated' : '');
        }
        case 'condition_action':
          return `${who || 'Someone'}: ${conditionLabel(p.condition || '')} ${p.action}` +
            (p.rounds ? ` for ${p.rounds} rounds` : '');
        case 'advance_turn':
          return `Turn ${p.direction === 'prev' ? 'stepped back' : 'advanced'}`;
        case 'capture_note':
          return `Note: ${p.text || ''}`;
        case 'set_active_room':
          return `Entered ${p.title || p.area_id || 'a room'}`;
        case 'undo_last':
          return `Undid a ${r.command_type || 'command'}`;
        default:
          return event.command_type || 'command';
      }
    };

    const rounds = new Map();
    events.forEach((event) => {
      const round = event.before?.round ?? event.after?.round ?? null;
      const key = round == null ? 'Out of combat' : `Round ${round}`;
      if (!rounds.has(key)) rounds.set(key, []);
      rounds.get(key).push(event);
    });

    const lines = [];
    rounds.forEach((group, key) => {
      lines.push('', `**${key}**`, '');
      group.forEach((event) => {
        const when = String(event.occurred_at || '').slice(11, 16);
        lines.push(`- ${when ? `\`${when}\` ` : ''}${describe(event)} <!-- ${event.event_id || ''} -->`);
      });
    });
    return { lines, count: events.length, lastSequence: events[events.length - 1].sequence };
  }

  async materializeSessionEnd(session) {
    await this.persistSnapshot('session_end');
    await this.materializeHandoff(session, 'awaiting_processing');
    const folder = this.sessionFolder(session.id);
    const recordPath = normalizePath(`${folder}/Session Record.md`);
    let file = this.app.vault.getAbstractFileByPath(recordPath);
    if (!file) {
      await this.materializeSessionStart(session);
      file = this.app.vault.getAbstractFileByPath(recordPath);
    }
    if (file) {
      await this.app.fileManager.processFrontMatter(file, (frontmatter) => {
        frontmatter.status = 'awaiting_processing';
        frontmatter.ended_at = session.ended_at;
      });
      // The readable ledger goes in first, so the closing snapshot reads as the
      // final line of a narrative rather than the only thing in the note.
      const digest = await this.buildSessionDigest(session);
      if (digest) {
        const digestMarker = `<!-- session-operations-digest:${digest.lastSequence} -->`;
        const current = await this.app.vault.read(file);
        if (!current.includes(digestMarker)) {
          await this.app.vault.append(file, [
            '', digestMarker, '',
            `### What the website recorded (${digest.count} events)`,
            '',
            '> Mechanical evidence, ranked above the transcript for HP, conditions and',
            '> turn order by the Post-Session Agent Workflow Specification. Comments',
            '> carry event ids so a later pass can cite rather than paraphrase.',
            ...digest.lines,
            '',
          ].join('\n'));
        }
      }

      let body = await this.app.vault.read(file);
      const marker = `<!-- session-operations-closing:${this.revision} -->`;
      if (!body.includes(marker)) {
        const partyLines = (this.state?.party || []).map((pc) => {
          const conditions = activeConditions(pc.conditions) || 'none';
          return `- ${pc.name}: ${pc.current_hp}/${pc.max_hp} HP${pc.temp_hp ? ` + ${pc.temp_hp} temp HP` : ''}; conditions: ${conditions}; Hero Points: ${pc.hero_points ?? 0}; Focus: ${pc.focus ?? 0}`;
        });
        const encounter = this.state?.encounter || {};
        const closing = [
          '', marker, '',
          '### Exact structured close',
          '',
          `- Ended: ${session.ended_at}`,
          `- Website revision: ${this.revision}`,
          `- Last event sequence: ${this.settings.lastEventSequence}`,
          `- Encounter: ${encounter.combatants?.length ? `Round ${encounter.round}, active turn ${encounter.active_name || 'unknown'}` : 'none active'}`,
          '',
          '#### Party state',
          '',
          ...(partyLines.length ? partyLines : ['- No party state was available.']),
          '',
          '> Full canonical snapshot: `Website State.json`',
          '> Append-only structured log: `Combat Events.jsonl`',
          '',
        ].join('\n');
        await this.app.vault.append(file, closing);
      }
    }
  }

  async materializeHandoff(session, status) {
    const folder = this.sessionFolder(session.id);
    await this.ensureFolder(`${folder}/Sources`);
    const handoffPath = normalizePath(`${folder}/Post-Session Handoff.md`);
    let file = this.app.vault.getAbstractFileByPath(handoffPath);
    if (!file) {
      const text = [
        '---',
        'type: post_session_handoff',
        `session_id: ${yamlString(session.id)}`,
        `session_label: ${yamlString(session.label)}`,
        `status: ${status}`,
        'schema_version: 2',
        '---',
        '',
        `# Post-Session Handoff — ${session.label}`,
        '',
        'Follow [[Post-Session Agent Workflow Specification]].',
        '',
        '## Source Checklist',
        '',
        '- [x] `Session Record.md`',
        '- [x] `Website State.json`',
        '- [ ] `Combat Events.jsonl` (created after the first accepted session event)',
        '- [ ] Session audio or transcript placed in `Sources/` or linked below',
        '- [ ] Broader GM notes, corrections, or context linked below',
        '- [ ] Final Run Sheet linked below',
        '',
        '## Source Links and Context',
        '',
        '- Audio/transcript:',
        '- GM notes:',
        '- Run Sheet:',
        '- Starting room:',
        '- Ending room:',
        '- In-game date:',
        '',
        '## GM Corrections and Rulings',
        '',
        '> Add anything the structured tracker or audio would misrepresent. Explicit corrections take precedence during reconciliation.',
        '',
        '- ',
        '',
        '## Phase A Agent Request',
        '',
        '```text',
        `Process the session package at ${folder}.`,
        '',
        'Read and follow [[Post-Session Agent Workflow Specification]], [[Session Operations Specification]], and [[Vault Redesign Specification]]. Work in Phase A only: reconcile the structured events, final website state, audio/transcript, and GM notes, then create Reconciliation Report.md, Proposed Vault Changes.md, and Processing Manifest.json inside this session package. Do not edit any other vault file. Lead with discrepancies and decisions that require GM review. Never infer that prepared content occurred.',
        '```',
        '',
        '## Approval Notes',
        '',
        '- Approved change IDs:',
        '- Rejected change IDs:',
        '- Changes requiring discussion:',
        '',
      ].join('\n');
      await this.writeText(handoffPath, text);
      file = this.app.vault.getAbstractFileByPath(handoffPath);
    }
    if (file) {
      await this.app.fileManager.processFrontMatter(file, (frontmatter) => {
        frontmatter.status = status;
        frontmatter.ended_at = session.ended_at || null;
      });
    }
  }
};
