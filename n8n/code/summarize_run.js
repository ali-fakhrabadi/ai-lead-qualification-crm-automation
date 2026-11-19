// Code node "Summarize run" (run once for all items): what to report for this execution.
//
//   success   the pipeline ran to its end (a draft exists, or a delivery was made)
//   stopped   the run ended on purpose: the lead needs review, or a permanent error needs
//             an operator; the event is complete and is not delivered again
//   error     a transient failure outlasted this run's retries; the API schedules another
//             delivery of the event with backoff
const STAGES = __STAGES__; // plan nodes, latest stage first
const VERIFY_NODE = 'Verify event';

const verified = $(VERIFY_NODE).first().json;
let last = null;
for (const name of STAGES) {
  if ($(name).isExecuted) {
    last = $(name).last().json;
    break;
  }
}

let status;
let step;
let error = null;
if (last === null) {
  status = 'stopped';
  step = 'next_step';
} else {
  step = last.step;
  if (last.route === 'ok') {
    status = last.next === 'done' ? 'success' : 'stopped';
  } else if (last.route === 'permanent') {
    status = 'stopped';
    error = last.error;
  } else {
    status = 'error';
    error = last.error;
  }
}

return [
  {
    json: {
      event_id: verified.event_id,
      report: {
        execution_id: String($execution.id),
        status,
        step,
        duration_ms: Math.max(0, Date.now() - Number(verified.started_at || Date.now())),
        error,
      },
    },
  },
];
