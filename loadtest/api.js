// k6 latency test of the API endpoints an operator dashboard and a partner system call.
//
//   loadtest/run.sh api.js -e VUS=5 -e DURATION=60s
//
// Reads: health, public form configuration, the lead list, a lead's detail and the stats.
// Writes: a signed partner webhook REDELIVERY (the same event every time), which exercises
// signature verification, the idempotency lookup and the audit write without creating leads
// or model work. The webhook is paced below the per-source rate limit (300/min by default).
// Run it after the benchmark so the database holds realistic data.
import http from "k6/http";
import crypto from "k6/crypto";
import { check } from "k6";

const BASE = __ENV.BASE_URL || "http://web:8080";
const PASSWORD = open(__ENV.PASSWORD_FILE).trim();
const WEBHOOK_KEY = JSON.parse(open(__ENV.WEBHOOK_SECRETS_FILE))["partner-demo"];
const VUS = Number(__ENV.VUS || 5);
const DURATION = __ENV.DURATION || "60s";

export const options = {
  scenarios: {
    reads: { executor: "constant-vus", vus: VUS, duration: DURATION, exec: "reads" },
    webhook: {
      executor: "constant-arrival-rate", rate: 3, timeUnit: "1s", duration: DURATION,
      preAllocatedVUs: 3, exec: "webhook",
    },
  },
  thresholds: {
    "http_req_duration{name:health}": ["max>=0"],
    "http_req_duration{name:form_config}": ["max>=0"],
    "http_req_duration{name:lead_list}": ["max>=0"],
    "http_req_duration{name:lead_detail}": ["max>=0"],
    "http_req_duration{name:stats}": ["max>=0"],
    "http_req_duration{name:webhook_redelivery}": ["max>=0"],
    "checks": ["rate>0.99"],
  },
  summaryTrendStats: ["min", "med", "avg", "p(90)", "p(95)", "p(99)", "max"],
};

const EVENT = JSON.stringify({
  event_id: "evt_loadtest_redelivery_0001",
  lead: {
    first_name: "Load", last_name: "Test", email: "load.test@loadtest-partner.example",
    company: "Loadtest Partner", interest: "crm_integration", consent_contact: true,
    message: "Synthetic load-test event: we want our partner leads synced to the CRM.",
  },
});

export function setup() {
  const login = http.post(`${BASE}/api/v1/auth/login`,
    JSON.stringify({ email: __ENV.EMAIL, password: PASSWORD }),
    { headers: { "Content-Type": "application/json" }, tags: { name: "login" } });
  check(login, { "login 200": (r) => r.status === 200 });
  const token = login.json("access_token");
  const auth = { headers: { Authorization: `Bearer ${token}` } };
  const leads = http.get(`${BASE}/api/v1/leads?limit=50`, auth).json("items");
  // Deliver the event once so every later request is a redelivery.
  signedWebhook();
  return { token, ids: leads.map((l) => l.id) };
}

function signedWebhook() {
  const ts = Math.floor(Date.now() / 1000).toString();
  const signature = "v1=" + crypto.hmac("sha256", WEBHOOK_KEY, `${ts}.${EVENT}`, "hex");
  return http.post(`${BASE}/api/v1/webhooks/leads/partner-demo`, EVENT, {
    headers: { "Content-Type": "application/json", "X-Lead-Timestamp": ts,
               "X-Lead-Signature": signature },
    tags: { name: "webhook_redelivery" },
  });
}

export function reads(data) {
  const auth = { headers: { Authorization: `Bearer ${data.token}` } };
  const id = data.ids[(__VU * 7 + __ITER) % data.ids.length];
  check(http.get(`${BASE}/api/health`, { tags: { name: "health" } }),
        { "health 200": (r) => r.status === 200 });
  check(http.get(`${BASE}/api/v1/public/form-config`, { tags: { name: "form_config" } }),
        { "form config 200": (r) => r.status === 200 });
  check(http.get(`${BASE}/api/v1/leads?limit=25`, { ...auth, tags: { name: "lead_list" } }),
        { "list 200": (r) => r.status === 200 });
  check(http.get(`${BASE}/api/v1/leads/${id}`, { ...auth, tags: { name: "lead_detail" } }),
        { "detail 200": (r) => r.status === 200 });
  check(http.get(`${BASE}/api/v1/stats`, { ...auth, tags: { name: "stats" } }),
        { "stats 200": (r) => r.status === 200 });
}

export function webhook() {
  check(signedWebhook(), {
    "redelivery 202 replayed": (r) => r.status === 202 && r.json("replayed") === true,
  });
}
