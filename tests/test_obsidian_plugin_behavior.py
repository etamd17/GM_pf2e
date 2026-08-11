"""Behavioural tests for the Obsidian plugin's state machine, run under node.

These cover four defects found in the 2026-08-11 audit, all of which are
invisible to manual testing because the pane keeps *looking* right while the
model underneath is wrong:

1. A /state reply computed before a command was applied could land after the
   command response and rewind revision/state.
2. An idempotent replay -- the server saying "this command_id already ran" --
   was adopted as a fresh authoritative payload, rewinding the pane and writing
   a stale snapshot into the vault.
3. lastPollAt was stamped on success only, so any outage became a 1 Hz retry
   storm instead of backing off to backgroundPollMs.
4. Server-controlled strings went into hand-built YAML frontmatter with only
   double quotes escaped, so a label containing a newline corrupted the note.

main.js requires('obsidian'), which only exists inside the app, so the harness
stubs that module and drives the exported class directly. Skips when node is
absent; GitHub's ubuntu-latest runner ships it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAIN_JS = os.path.join(_REPO, 'tools', 'obsidian-plugin', 'main.js')

_HARNESS = r"""
const Module = require('module');

const notices = [];
class Notice { constructor(message) { notices.push(String(message)); } }
class Plugin { constructor(app, manifest) { this.app = app; this.manifest = manifest; } }
class ItemView { constructor(leaf) { this.leaf = leaf; } }
class Modal { constructor(app) { this.app = app; } }
class PluginSettingTab { constructor(app, plugin) { this.app = app; this.plugin = plugin; } }
class Setting { constructor(el) { this.el = el; } }
class MarkdownView {}

const stub = {
  ItemView, MarkdownView, Modal, Notice, Plugin, PluginSettingTab, Setting,
  normalizePath: (p) => String(p).replace(/\\/g, '/').replace(/\/{2,}/g, '/'),
  requestUrl: async () => { throw new Error('network disabled in tests'); },
};

const originalLoad = Module._load;
Module._load = function (request, ...rest) {
  if (request === 'obsidian') return stub;
  return originalLoad.call(this, request, ...rest);
};

const PluginClass = require(process.argv[2]);

function makePlugin() {
  const p = new PluginClass({}, {});
  p.settings = {
    baseUrl: 'http://example.invalid',
    campaignId: 'a'.repeat(32),
    token: 'obs1_placeholder',
    sessionDataFolder: '_Session Data',
    visiblePollMs: 1000,
    backgroundPollMs: 5000,
    lastEventSequence: 0,
    pendingCommands: [],
  };
  p.state = null;
  p.session = null;
  p.revision = 0;
  p.connected = false;
  p.lastError = '';
  p.polling = false;
  p.lastPollAt = 0;
  p.snapshots = [];
  p.renderViews = () => {};
  p.saveSettings = async () => {};
  p.pullEvents = async () => {};
  p.persistSnapshot = async (reason) => { p.snapshots.push(reason); };
  return p;
}

(async () => {
  const out = {};

  // (1) A response older than what we hold is refused outright.
  {
    const p = makePlugin();
    p.revision = 41;
    p.state = { marker: 'post-command' };
    p.session = { id: 'live' };
    const adopted = p.adoptServerState({ revision: 40, state: { marker: 'pre-command' }, session: null });
    out.stale_refused = adopted === false;
    out.stale_kept_revision = p.revision === 41;
    out.stale_kept_state = p.state.marker === 'post-command';
    out.stale_kept_session = p.session !== null;
  }

  // (2) Equal is adopted (first sync sits at 0 == 0), newer is adopted.
  {
    const p = makePlugin();
    p.revision = 41;
    p.state = { marker: 'held' };
    out.equal_accepted = p.adoptServerState({ revision: 41, state: { marker: 'same' } }) === true;
    const newer = p.adoptServerState({ revision: 42, state: { marker: 'newer' } });
    out.newer_accepted = newer === true && p.revision === 42 && p.state.marker === 'newer';
    out.garbage_refused = p.adoptServerState({ revision: 'not-a-number' }) === false;
  }

  // (3) An idempotent replay is acknowledged, never adopted, and never snapshotted.
  {
    const p = makePlugin();
    p.revision = 51;
    p.state = { marker: 'current' };
    await p.acceptCommandResponse({
      ok: true, idempotent_replay: true, revision: 41, state: { marker: 'as-originally-executed' },
    });
    out.replay_kept_revision = p.revision === 51;
    out.replay_kept_state = p.state.marker === 'current';
    out.replay_wrote_no_snapshot = p.snapshots.length === 0;
    out.replay_marks_connected = p.connected === true;
  }

  // (4) A genuine command response is adopted and does snapshot.
  {
    const p = makePlugin();
    p.revision = 51;
    p.state = { marker: 'current' };
    await p.acceptCommandResponse({ ok: true, revision: 52, state: { marker: 'applied' } });
    out.command_adopted = p.revision === 52 && p.state.marker === 'applied';
    out.command_wrote_snapshot = p.snapshots.includes('command');
  }

  // (5) A failed poll still stamps the throttle, so the retry backs off.
  {
    const p = makePlugin();
    p.api = async () => { const error = new Error('Offline: connection refused'); error.status = 0; throw error; };
    await p.syncNow({ quiet: true });
    out.failed_poll_stamped_throttle = p.lastPollAt > 0;
    out.failed_poll_marked_offline = p.connected === false;
    out.failed_poll_released_lock = p.polling === false;
  }

  // (6) Frontmatter survives a hostile session label.
  {
    const p = makePlugin();
    const written = {};
    p.app = { vault: { getAbstractFileByPath: () => null } };
    p.ensureFolder = async () => {};
    p.writeText = async (path, text) => { written[path] = text; };
    p.materializeHandoff = async () => {};
    p.persistSnapshot = async () => {};
    const label = 'Ambush at "The Wall"\nended_at: injected\nnot_frontmatter: true';
    await p.materializeSessionStart({
      id: 'session-2026-08-11-abc123', label, started_at: '2026-08-11T00:00:00Z',
    });
    const key = Object.keys(written).find((k) => k.endsWith('Session Record.md'));
    out.record_written = Boolean(key);
    out.record_text = key ? written[key] : '';
    out.record_label_input = label;
  }

  // (7) Condition durations reach the wire, but only where they mean something.
  {
    const p = makePlugin();
    const sent = [];
    p.sendCommand = async (type, payload) => { sent.push({ type, payload }); };
    await p.conditionAction('t1', 'frightened', 'increase', 3);
    await p.conditionAction('t1', 'prone', 'add', 2);
    await p.conditionAction('t1', 'frightened', 'decrease', 3);
    await p.conditionAction('t1', 'prone', 'remove', 3);
    await p.conditionAction('t1', 'sickened', 'increase', 0);
    await p.conditionAction('t1', 'sickened', 'increase', 99999);
    out.rounds_sent_on_increase = sent[0].payload.rounds === 3;
    out.rounds_sent_on_add = sent[1].payload.rounds === 2;
    out.no_rounds_on_decrease = !('rounds' in sent[2].payload);
    out.no_rounds_on_remove = !('rounds' in sent[3].payload);
    out.no_rounds_when_zero = !('rounds' in sent[4].payload);
    out.rounds_clamped = sent[5].payload.rounds === 1000;
    out.command_type_unchanged = sent.every((s) => s.type === 'condition_action');
  }

  // (8) The session digest turns the JSONL ledger into prose, grouped by round.
  {
    const p = makePlugin();
    const events = [
      { sequence: 1, event_id: 'e1', occurred_at: '2026-08-11T19:04:00Z', command_type: 'adjust_hp',
        payload: { amount: 9 }, result: { action: 'damage', old_hp: 40, new_hp: 31 },
        before: { round: 1, target: { name: 'Desmohund' } } },
      { sequence: 2, event_id: 'e2', occurred_at: '2026-08-11T19:05:00Z', command_type: 'condition_action',
        payload: { condition: 'frightened', action: 'increase', rounds: 3 },
        before: { round: 1, target: { name: 'Gavin' } } },
      { sequence: 3, event_id: 'e3', occurred_at: '2026-08-11T19:09:00Z', command_type: 'adjust_hp',
        payload: { amount: 6 }, result: { action: 'damage', raw_amount: 6, targets: [{}, {}] },
        before: { round: 2, target: { name: 'Desmohund' } } },
      { sequence: 4, event_id: 'e4', occurred_at: '2026-08-11T19:20:00Z', command_type: 'capture_note',
        payload: { text: 'the cultist fled north' }, before: { round: null } },
    ];
    const jsonl = events.map((e) => JSON.stringify(e)).join('\n') + '\n';
    p.app = { vault: { getAbstractFileByPath: (path) => (path.endsWith('Combat Events.jsonl') ? { path } : null),
                       read: async () => jsonl } };
    const digest = await p.buildSessionDigest({ id: 'session-x' });
    const text = digest.lines.join('\n');
    out.digest_count = digest.count;
    out.digest_last_sequence = digest.lastSequence;
    out.digest_groups_rounds = text.includes('**Round 1**') && text.includes('**Round 2**');
    out.digest_out_of_combat = text.includes('**Out of combat**');
    out.digest_uses_real_delta = text.includes('took 9') && text.includes('now 31');
    out.digest_names_condition = text.includes('Frightened increase for 3 rounds');
    out.digest_multi_target = text.includes('Damaged 2 targets for 6');
    out.digest_note_text = text.includes('the cultist fled north');
    out.digest_cites_event_ids = ['e1', 'e2', 'e3', 'e4'].every((id) => text.includes(id));

    const empty = makePlugin();
    empty.app = { vault: { getAbstractFileByPath: () => null } };
    out.digest_null_without_events = (await empty.buildSessionDigest({ id: 's' })) === null;
  }

  process.stdout.write(JSON.stringify(out));
})().catch((error) => {
  process.stderr.write(String(error && error.stack || error));
  process.exit(1);
});
"""


@pytest.fixture(scope='module')
def harness_output(tmp_path_factory):
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not available; plugin behaviour tests need it to load main.js')
    harness = tmp_path_factory.mktemp('obsidian-harness') / 'harness.js'
    harness.write_text(_HARNESS, encoding='utf-8')
    result = subprocess.run(
        [node, str(harness), _MAIN_JS],
        capture_output=True, text=True, encoding='utf-8', timeout=60,
    )
    assert result.returncode == 0, f'harness failed:\n{result.stderr}'
    return json.loads(result.stdout)


def test_a_stale_state_response_cannot_rewind_the_client(harness_output):
    """The bug: poll leaves at revision 40, GM advances the turn to 41, the late
    poll reply lands and silently rewinds to 40 -- with no repaint, because the
    repaint gate compares against a revision captured before the request."""
    assert harness_output['stale_refused']
    assert harness_output['stale_kept_revision']
    assert harness_output['stale_kept_state']
    assert harness_output['stale_kept_session']


def test_equal_and_newer_revisions_are_still_adopted(harness_output):
    """The guard must not be so strict that a first sync (0 == 0) is refused."""
    assert harness_output['equal_accepted']
    assert harness_output['newer_accepted']
    assert harness_output['garbage_refused']


def test_idempotent_replay_is_acknowledged_but_not_adopted(harness_output):
    """A replay body carries the revision and state from ORIGINAL execution.
    Adopting it rewound the pane and overwrote Website State.json with a
    snapshot as old as the queued command."""
    assert harness_output['replay_kept_revision']
    assert harness_output['replay_kept_state']
    assert harness_output['replay_wrote_no_snapshot']
    assert harness_output['replay_marks_connected']


def test_a_real_command_response_is_adopted_and_snapshotted(harness_output):
    """Guards the replay fix from over-reaching into the normal path."""
    assert harness_output['command_adopted']
    assert harness_output['command_wrote_snapshot']


def test_a_failed_poll_still_advances_the_throttle(harness_output):
    """lastPollAt used to be assigned after the request succeeded, so every
    failure skipped it and pollIfDue's elapsed-time gate passed on every tick."""
    assert harness_output['failed_poll_stamped_throttle']
    assert harness_output['failed_poll_marked_offline']
    assert harness_output['failed_poll_released_lock']


def test_frontmatter_survives_a_hostile_session_label(harness_output):
    """A label with a newline used to inject additional YAML keys into the note
    and corrupt it for every other plugin that reads its frontmatter."""
    assert harness_output['record_written']
    text = harness_output['record_text']
    label = harness_output['record_label_input']

    assert text.startswith('---\n')
    body_start = text.index('\n---\n', 4)
    frontmatter = text[4:body_start].split('\n')

    matches = [line for line in frontmatter if line.startswith('session_label: ')]
    assert len(matches) == 1, f'session_label did not stay on one line: {frontmatter}'
    # The emitted scalar is JSON, which is a valid YAML double-quoted scalar --
    # so a successful round-trip proves nothing leaked out of the string.
    assert json.loads(matches[0][len('session_label: '):]) == label

    assert not any(line.startswith('not_frontmatter:') for line in frontmatter), \
        'the label injected a key into the frontmatter block'
    assert len([line for line in frontmatter if line.startswith('ended_at:')]) == 1


def test_condition_durations_are_sent_only_where_they_mean_something(harness_output):
    """The server has always accepted a `rounds` field on condition_action and
    fed it to the website's auto-expiry timer; the plugin never sent one, so a
    timed condition applied from Obsidian never expired.

    A duration is only meaningful when a condition goes ON. Sending one with
    decrease/remove would set a timer on a condition being cleared.
    """
    assert harness_output['rounds_sent_on_increase']
    assert harness_output['rounds_sent_on_add']
    assert harness_output['no_rounds_on_decrease']
    assert harness_output['no_rounds_on_remove']
    assert harness_output['no_rounds_when_zero'], 'zero means "no timer", not "expire immediately"'
    assert harness_output['rounds_clamped'], 'must clamp to the server ceiling of 1000'
    assert harness_output['command_type_unchanged']


def test_session_digest_renders_the_event_ledger_as_prose(harness_output):
    """Combat Events.jsonl has only ever been machine-readable, yet the vault's
    Post-Session Agent Workflow Specification ranks structured website events
    ABOVE the audio transcript as evidence for mechanical facts. Nothing
    produced them in readable form, so that lane sat empty.

    Grouped by round because that is how a table remembers a fight, and event
    ids are carried through so a later pass can cite rather than paraphrase --
    the same spec forbids filling gaps with plausible fiction.
    """
    assert harness_output['digest_count'] == 4
    assert harness_output['digest_last_sequence'] == 4
    assert harness_output['digest_groups_rounds']
    assert harness_output['digest_out_of_combat'], 'events with no round still belong somewhere'
    assert harness_output['digest_uses_real_delta'], \
        'must report the HP the engine actually applied, not the amount requested'
    assert harness_output['digest_names_condition']
    assert harness_output['digest_multi_target']
    assert harness_output['digest_note_text']
    assert harness_output['digest_cites_event_ids']
    assert harness_output['digest_null_without_events']
