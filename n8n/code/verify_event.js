// Code node "Verify event" (run once for all items).
//
// The Webhook node has already checked the HS256 signature and expiry of the dispatcher's
// token (JWT auth credential). This node checks what it cannot: issuer and audience, that
// the token was issued for exactly this payload (its SHA-256 is a signed claim) and for this
// event id, and that the envelope is well formed. A captured token is useless with any
// other body and expires within minutes.
const crypto = require('crypto');

const WEBHOOK_NODE = '__WEBHOOK_NODE__';
const EVENT_TYPES = __EVENT_TYPES__;
const ISSUER = 'lead-automation-dispatcher';
const AUDIENCE = 'n8n';
const MAX_PAYLOAD_CHARS = 65536;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const request = $(WEBHOOK_NODE).first().json;
const claims = request.jwtPayload || {};
const raw = request.body ? request.body.payload : undefined;

function reject(reason) {
  return [{ json: { valid: false, reason } }];
}

if (typeof raw !== 'string' || raw.length === 0) {
  return reject('body.payload must be a non-empty JSON string');
}
if (raw.length > MAX_PAYLOAD_CHARS) {
  return reject('payload is too large');
}
if (claims.iss !== ISSUER || claims.aud !== AUDIENCE) {
  return reject('unexpected token issuer or audience');
}
const digest = crypto.createHash('sha256').update(raw, 'utf8').digest('hex');
if (digest !== claims.payload_sha256) {
  return reject('the payload does not match the token');
}
let event;
try {
  event = JSON.parse(raw);
} catch (error) {
  return reject('the payload is not valid JSON');
}
if (!UUID.test(String(event.event_id)) || event.event_id !== claims.eid) {
  return reject('the payload is for another event');
}
if (!EVENT_TYPES.includes(event.event_type)) {
  return reject(`unsupported event type ${event.event_type}`);
}
if (!UUID.test(String(event.lead_id))) {
  return reject('lead_id is missing or invalid');
}
const data = event.data && typeof event.data === 'object' ? event.data : {};
if (data.draft_id !== undefined && !UUID.test(String(data.draft_id))) {
  return reject('draft_id is invalid');
}
return [
  {
    json: {
      valid: true,
      event_id: event.event_id,
      event_type: event.event_type,
      lead_id: event.lead_id,
      correlation_id: String(event.correlation_id || '').slice(0, 64),
      attempt: Number(event.attempt) || 1,
      data,
      started_at: Date.now(),
    },
  },
];
