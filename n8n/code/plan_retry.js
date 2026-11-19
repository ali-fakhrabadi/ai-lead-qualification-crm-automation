// Code node "Plan <step> retry" (run once for all items), placed after an HTTP Request node
// that returns the full response and never throws on HTTP errors.
//
// Classifies the API's answer and decides what happens next:
//   ok          2xx: continue with the step the API names in body.next
//   retry       transient (connection error, 429, 5xx): wait, then call again
//   exhausted   transient, but the attempts for this run are used up: stop this run with an
//               error so the event is delivered again later (the dispatcher backs off)
//   permanent   4xx: stop; an operator must act (nothing is retried automatically)
//
// The wait grows exponentially with full jitter and honours Retry-After, capped at 60 s.
const STEP = '__STEP__';
const MAX_ATTEMPTS = __MAX_ATTEMPTS__;
const BASE_WAIT_S = __BASE_WAIT_S__;

const response = $json || {};
const status = Number(response.statusCode || 0);
const body = response.body && typeof response.body === 'object' ? response.body : {};
const attempt = $runIndex + 1;

let route;
if (status >= 200 && status < 300) {
  route = 'ok';
} else if (status === 0 || status === 429 || status >= 500) {
  route = attempt < MAX_ATTEMPTS ? 'retry' : 'exhausted';
} else {
  route = 'permanent';
}

let waitS = 0;
if (route === 'retry') {
  const headers = response.headers || {};
  const retryAfter = Number(headers['retry-after'] || 0);
  const ceiling = Math.min(60, BASE_WAIT_S * 2 ** (attempt - 1));
  const jittered = Math.max(1, Math.round(ceiling / 2 + Math.random() * (ceiling / 2)));
  waitS = Math.min(60, Math.max(jittered, retryAfter));
}

const error = body.error || {};
return [
  {
    json: {
      step: STEP,
      route,
      attempt,
      status_code: status,
      wait_s: waitS,
      next: route === 'ok' ? String(body.next || 'stop') : 'stop',
      lead_status: body.status || null,
      detail: body.detail || {},
      error: route === 'ok' ? null : String(error.code || (response.error && response.error.message) || `HTTP ${status}`).slice(0, 200),
    },
  },
];
