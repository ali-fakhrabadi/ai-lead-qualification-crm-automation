// One-shot n8n setup: import the credentials and the three workflows, then publish all three.
// Runs in the n8n image before n8n starts (the "n8n-setup" service).
//
// Credentials are built from Docker secrets and environment variables, written to a file on
// tmpfs only for the n8n CLI to read, and deleted afterwards; n8n stores them encrypted with
// N8N_ENCRYPTION_KEY. Nothing secret is printed. The exported workflow files contain no
// credential values, only references by id and name.
//
// Idempotent: workflows and credentials have fixed ids, so a rerun updates them. When nothing
// changed since the last run (a hash kept in n8n's data volume), it does nothing.
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';

const bcrypt = createRequire('/usr/local/lib/node_modules/n8n/package.json')('bcryptjs');
const OWNER_HASH_FILE = '/home/node/.n8n/lead-owner-password.bcrypt';
const WORKFLOW_DIR = process.env.LEAD_WORKFLOW_DIR || '/opt/lead/workflows';
const STATE_FILE = '/home/node/.n8n/lead-setup.sha256';
// The error handler must be published too: n8n 2.x runs only a workflow's published version,
// and an error workflow without one is silently skipped (found by the workflow harness).
const PUBLISH = ['leadPipeline0001', 'leadFollowDeliv1', 'leadErrorHandl01'];

function log(message) {
  console.log(`[lead-setup] ${message}`);
}

function secret(name) {
  try {
    const value = readFileSync(`/run/secrets/${name}`, 'utf8').trim();
    if (!value) throw new Error('empty');
    return value;
  } catch (error) {
    throw new Error(`secret ${name} is missing or empty`);
  }
}

function setting(name, fallback = '') {
  return (process.env[name] || fallback).trim();
}

function credentialData(type) {
  switch (type) {
    case 'jwtAuth':
      return { keyType: 'passphrase', secret: secret('dispatch_secret'), algorithm: 'HS256' };
    case 'httpHeaderAuth':
      return { name: 'Authorization', value: `Bearer ${secret('internal_token')}` };
    case 'smtp':
      return {
        user: '',
        password: '',
        host: setting('LEAD_SMTP_HOST', 'mailpit'),
        port: Number(setting('LEAD_SMTP_PORT', '1025')),
        secure: false,
        disableStartTls: true,
      };
    default:
      throw new Error(`a workflow uses a credential type this setup does not know: ${type}`);
  }
}

function credentials(workflows) {
  const byId = new Map();
  for (const workflow of workflows) {
    for (const node of workflow.nodes) {
      for (const [type, ref] of Object.entries(node.credentials || {})) {
        byId.set(ref.id, { id: ref.id, name: ref.name, type, data: credentialData(type) });
      }
    }
  }
  return [...byId.values()];
}

function configure(workflow) {
  const values = {
    api_url: setting('LEAD_API_URL', 'http://api:8000').replace(/\/+$/, ''),
    operator_email: setting('LEAD_OPERATOR_EMAIL', 'operators@northwind-automation.example'),
    mail_from: setting('LEAD_N8N_MAIL_FROM', 'lead-automation@northwind-automation.example'),
    dashboard_url: setting('LEAD_DASHBOARD_URL', 'http://localhost:8080').replace(/\/+$/, ''),
  };
  for (const node of workflow.nodes.filter((n) => n.name === 'Settings')) {
    for (const assignment of node.parameters.assignments.assignments) {
      if (assignment.name in values) assignment.value = values[assignment.name];
    }
  }
  return workflow;
}

function n8n(...args) {
  execFileSync('n8n', args, { stdio: ['ignore', 'inherit', 'inherit'] });
}

function ownerHash() {
  const password = secret('n8n_owner_password');
  try {
    if (bcrypt.compareSync(password, readFileSync(OWNER_HASH_FILE, 'utf8').trim())) return;
  } catch {}
  writeFileSync(OWNER_HASH_FILE, `${bcrypt.hashSync(password, 12)}\n`, { mode: 0o600 });
  log('owner password hash written');
}

function main() {
  ownerHash();
  const files = readdirSync(WORKFLOW_DIR).filter((f) => f.endsWith('.json')).sort();
  const workflows = files.map((f) => configure(JSON.parse(readFileSync(join(WORKFLOW_DIR, f), 'utf8'))));
  const creds = credentials(workflows);
  const digest = createHash('sha256').update(JSON.stringify({ workflows, creds })).digest('hex');
  let previous = '';
  try {
    previous = readFileSync(STATE_FILE, 'utf8').trim();
  } catch {}
  if (previous === digest && !process.argv.includes('--force')) {
    log('workflows and credentials are up to date');
    return;
  }
  const dir = mkdtempSync('/tmp/lead-setup-');
  try {
    const credentialsFile = join(dir, 'credentials.json');
    const workflowsFile = join(dir, 'workflows.json');
    writeFileSync(credentialsFile, JSON.stringify(creds), { mode: 0o600 });
    writeFileSync(workflowsFile, JSON.stringify(workflows), { mode: 0o600 });
    n8n('import:credentials', `--input=${credentialsFile}`);
    n8n('import:workflow', `--input=${workflowsFile}`);
    for (const id of PUBLISH) n8n('publish:workflow', `--id=${id}`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
  writeFileSync(STATE_FILE, `${digest}\n`, { mode: 0o600 });
  log(`imported ${creds.length} credentials and ${workflows.length} workflows; published ${PUBLISH.length}`);
}

try {
  main();
} catch (error) {
  console.error(`[lead-setup] ${error.message}`);
  process.exit(1);
}
