// Unit tests of the workflows' Code nodes: `node --test n8n/tests`.
//
// Each script runs as n8n runs it: the body of an async function with n8n's helpers in scope
// ($, $json, $execution, $runIndex, require). Placeholders are filled in the same way as
// scripts/build_workflows.py does.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const CODE = join(dirname(fileURLToPath(import.meta.url)), '..', 'code');
const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor;
const nodeRequire = createRequire(import.meta.url);

async function run(file, { replace = {}, nodes = {}, executed = {}, json = {}, runIndex = 0 } = {}) {
  let script = readFileSync(join(CODE, file), 'utf8');
  for (const [key, value] of Object.entries(replace)) {
    script = script.replaceAll(`__${key}__`, typeof value === 'string' ? value : JSON.stringify(value));
  }
  const $ = (name) => {
    const isExecuted = name in nodes || Boolean(executed[name]);
    if (!isExecuted) {
      return { isExecuted: false, first: () => { throw new Error(`no output of ${name}`); } };
    }
    const item = { json: nodes[name] };
    return { isExecuted: true, first: () => item, last: () => item, item };
  };
  return new AsyncFunction('$', '$json', '$execution', '$runIndex', 'require', script)(
    $, json, { id: 42 }, runIndex, nodeRequire,
  );
}

const sha256 = (text) => createHash('sha256').update(text, 'utf8').digest('hex');
const EVENT_ID = '0b9d5c2e-6f1a-4e3b-9c7d-8a2f1e0d3c4b';
const LEAD_ID = '5e2f8a1c-3b4d-4c6e-8f0a-1b2c3d4e5f6a';

function envelope(overrides = {}) {
  return { event_id: EVENT_ID, event_type: 'lead.received', lead_id: LEAD_ID,
    correlation_id: 'req-1', attempt: 1, data: { reason: 'new_lead' }, ...overrides };
}

async function verify(payload, claims = {}) {
  const raw = typeof payload === 'string' ? payload : JSON.stringify(payload);
  const jwtPayload = { iss: 'lead-automation-dispatcher', aud: 'n8n', eid: EVENT_ID,
    payload_sha256: sha256(raw), ...claims };
  const [item] = await run('verify_event.js', {
    replace: { WEBHOOK_NODE: 'Webhook', EVENT_TYPES: ['lead.received', 'lead.resume'] },
    nodes: { Webhook: { body: { payload: raw }, jwtPayload } },
  });
  return item.json;
}

test('verify: accepts a well-formed event signed for this payload', async () => {
  const result = await verify(envelope());
  assert.equal(result.valid, true);
  assert.equal(result.lead_id, LEAD_ID);
  assert.deepEqual(result.data, { reason: 'new_lead' });
});

test('verify: rejects a payload that does not match the signed hash', async () => {
  const raw = JSON.stringify(envelope());
  const result = await verify(raw.replace('new_lead', 'tampered'), { payload_sha256: sha256(raw) });
  assert.equal(result.valid, false);
  assert.match(result.reason, /does not match/);
});

test('verify: rejects a token issued for another event', async () => {
  const result = await verify(envelope(), { eid: 'f'.repeat(8) + EVENT_ID.slice(8) });
  assert.equal(result.valid, false);
  assert.match(result.reason, /another event/);
});

test('verify: rejects wrong issuer, event types and malformed ids', async () => {
  assert.equal((await verify(envelope(), { iss: 'someone' })).valid, false);
  assert.equal((await verify(envelope({ event_type: 'lead.delete' }))).valid, false);
  assert.equal((await verify(envelope({ lead_id: '../../admin' }))).valid, false);
  assert.equal((await verify(envelope({ data: { draft_id: 'x' } }))).valid, false);
});

async function plan(json, runIndex = 0) {
  const [item] = await run('plan_retry.js', {
    replace: { STEP: 'crm_sync', MAX_ATTEMPTS: '3', BASE_WAIT_S: '10' }, json, runIndex,
  });
  return item.json;
}

test('plan: 2xx continues with the next step named by the API', async () => {
  const result = await plan({ statusCode: 200, body: { next: 'follow_up_draft', status: 'synced_to_crm', detail: { changed: true } } });
  assert.equal(result.route, 'ok');
  assert.equal(result.next, 'follow_up_draft');
  assert.equal(result.error, null);
});

test('plan: transient errors are retried with a bounded, jittered wait', async () => {
  for (const statusCode of [0, 429, 500, 503]) {
    const result = await plan({ statusCode, headers: {}, body: {} }, 0);
    assert.equal(result.route, 'retry', `status ${statusCode}`);
    assert.ok(result.wait_s >= 5 && result.wait_s <= 10, `wait ${result.wait_s}`);
  }
  const later = await plan({ statusCode: 503, headers: {}, body: {} }, 1);
  assert.ok(later.wait_s >= 10 && later.wait_s <= 20);
  const honoured = await plan({ statusCode: 503, headers: { 'retry-after': '45' }, body: {} }, 0);
  assert.equal(honoured.wait_s, 45);
  const capped = await plan({ statusCode: 503, headers: { 'retry-after': '600' }, body: {} }, 0);
  assert.equal(capped.wait_s, 60);
});

test('plan: transient errors stop the run after the last attempt', async () => {
  const result = await plan({ statusCode: 503, headers: {}, body: { error: { code: 'crm_unavailable' } } }, 2);
  assert.equal(result.route, 'exhausted');
  assert.equal(result.error, 'crm_unavailable');
  assert.equal(result.next, 'stop');
});

test('plan: 4xx is permanent and never retried', async () => {
  const result = await plan({ statusCode: 422, body: { error: { code: 'crm_rejected' } } });
  assert.equal(result.route, 'permanent');
  assert.equal(result.error, 'crm_rejected');
});

test('plan: a connection error (continued as regular output) is transient', async () => {
  const result = await plan({ error: { message: 'connect ECONNREFUSED' } });
  assert.equal(result.route, 'retry');
});

const STAGES = ['Plan draft retry', 'Plan CRM retry', 'Plan qualification retry'];

async function summarize(nodes) {
  const [item] = await run('summarize_run.js', {
    replace: { STAGES },
    nodes: { 'Verify event': { event_id: EVENT_ID, started_at: Date.now() - 1500 }, ...nodes },
  });
  return item.json;
}

test('summarize: a finished pipeline is a success with its duration', async () => {
  const result = await summarize({
    'Plan qualification retry': { step: 'qualify', route: 'ok', next: 'crm_sync' },
    'Plan CRM retry': { step: 'crm_sync', route: 'ok', next: 'follow_up_draft' },
    'Plan draft retry': { step: 'follow_up_draft', route: 'ok', next: 'done' },
  });
  assert.equal(result.event_id, EVENT_ID);
  assert.equal(result.report.status, 'success');
  assert.equal(result.report.step, 'follow_up_draft');
  assert.equal(result.report.execution_id, '42');
  assert.ok(result.report.duration_ms >= 1500);
});

test('summarize: review needed or permanent errors stop; exhausted retries are errors', async () => {
  const review = await summarize({ 'Plan qualification retry': { step: 'qualify', route: 'ok', next: 'stop' } });
  assert.equal(review.report.status, 'stopped');
  const permanent = await summarize({ 'Plan CRM retry': { step: 'crm_sync', route: 'permanent', error: 'crm_rejected' } });
  assert.equal(permanent.report.status, 'stopped');
  assert.equal(permanent.report.error, 'crm_rejected');
  const exhausted = await summarize({ 'Plan qualification retry': { step: 'qualify', route: 'exhausted', error: 'model_unavailable' } });
  assert.equal(exhausted.report.status, 'error');
  const nothing = await summarize({});
  assert.equal(nothing.report.status, 'stopped');
  assert.equal(nothing.report.step, 'next_step');
});

test('error details: forwards identifiers and a short message only', async () => {
  const [item] = await run('error_details.js', {
    // $json is the Settings node's output; the error comes from the trigger node by name.
    json: { api_url: 'http://api:8000' },
    nodes: { 'Workflow failed': { execution: { id: 77, lastNodeExecuted: 'Sync CRM contact', error: { message: `boom\n${'x'.repeat(500)}` } },
      workflow: { name: 'Lead pipeline' } } },
  });
  assert.equal(item.json.execution_id, '77');
  assert.equal(item.json.node, 'Sync CRM contact');
  assert.equal(item.json.workflow, 'Lead pipeline');
  assert.ok(item.json.message.length <= 300);
  assert.ok(!item.json.message.includes('\n'));
});
