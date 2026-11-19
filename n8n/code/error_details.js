// Code node "Error details" in the error workflow (run once for all items).
//
// The Error Trigger hands over the failed execution's metadata. Only identifiers and a short
// message are forwarded: no lead data, no request bodies.
const input = $json || {};
const execution = input.execution || {};
const workflow = input.workflow || {};
const message = String((execution.error && execution.error.message) || 'workflow error')
  .replace(/\s+/g, ' ')
  .slice(0, 300);
return [
  {
    json: {
      execution_id: execution.id !== undefined ? String(execution.id) : null,
      workflow: String(workflow.name || '').slice(0, 120) || null,
      node: String(execution.lastNodeExecuted || '').slice(0, 120) || null,
      message,
    },
  },
];
