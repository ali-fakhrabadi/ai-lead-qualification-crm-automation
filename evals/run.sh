#!/usr/bin/env bash
# Run the benchmark against a FRESH stack started with the evaluation overlay:
#
#   export COMPOSE_FILE=compose.yaml:compose.eval.yaml
#   LEAD_LLM_DRAFT_FOLLOWUPS=false docker compose up -d --build --wait   # see below
#   scripts/create-demo-users.sh
#   evals/run.sh                       # all scenarios (hours with a CPU-only model)
#   EVAL_ONLY=S001,S050 evals/run.sh   # selected scenario ids or kinds
#   EVAL_ALLOW_EXISTING=1 EVAL_RESUME_FROM=/out/raw-part1.json EVAL_RERUN=S025 evals/run.sh
#                                      # resume an interrupted run on the same stack
#
# LEAD_LLM_DRAFT_FOLLOWUPS=false makes follow-up drafts use the template, so the benchmark
# measures qualification, deduplication, CRM sync and approval without spending ~2 minutes of
# CPU inference per draft; the model-written drafts are exercised by the end-to-end tests.
#
# Output (evals/out/): raw.json (everything observed), resources.csv (container CPU/RAM every
# 15 s), environment.json, model.json, results.json (computed metrics).
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="$PWD/evals/out"
mkdir -p "$OUT"
compose() { docker compose "$@"; }

# --- environment and model (no host names or addresses are recorded) -------------------------
python3 - "$OUT/environment.json" <<'EOF'
import json, os, platform, subprocess, sys
cpu = next((l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")), "")
mem_kb = next(int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("MemTotal"))
def run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return None
images = run("docker", "compose", "images", "--format", "json")
json.dump({
    "app_host": {"cpu_model": cpu, "logical_cpus": os.cpu_count(),
                 "memory_gib": round(mem_kb / 1024 / 1024, 1),
                 "kernel": platform.release(), "docker": run("docker", "version", "--format", "{{.Server.Version}}")},
    "images": [{"service": i.get("ContainerName", "").replace("lead-automation-", ""),
                "image": i.get("Repository"), "tag": i.get("Tag")}
               for i in (json.loads(images) if images and images.startswith("[") else [])],
    "app_commit": os.environ.get("APP_COMMIT") or run("git", "rev-parse", "--short", "HEAD"),
    "draft_generator": "llm" if os.environ.get("LEAD_LLM_DRAFT_FOLLOWUPS", "true") == "true" else "template",
}, open(sys.argv[1], "w"), indent=2)
EOF
compose exec -T api python - > "$OUT/model.json" <<'EOF'
import json, httpx
from app.core.config import get_settings
s = get_settings()
info = {"provider": s.llm_provider, "model": s.llm_model, "num_ctx": s.llm_num_ctx,
        "temperature": s.llm_temperature, "seed": s.llm_seed, "num_thread": s.llm_num_thread,
        "timeout_s": s.llm_timeout_s, "draft_followups_by_model": s.llm_draft_followups}
if s.llm_provider == "ollama":
    base = s.ollama_base_url.rstrip("/")
    show = httpx.post(f"{base}/api/show", json={"model": s.llm_model}, timeout=30).json()
    tags = httpx.get(f"{base}/api/tags", timeout=30).json().get("models", [])
    digest = next((m.get("digest") for m in tags if m.get("name") == s.llm_model), None)
    info.update({"details": show.get("details"), "digest": digest,
                 "ollama_version": httpx.get(f"{base}/api/version", timeout=10).json().get("version")})
print(json.dumps(info, indent=2))
EOF

# --- container resource samples while the benchmark runs -------------------------------------
( echo "ts,container,cpu_percent,mem_used,mem_limit"
  while true; do
    docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' \
      | grep '^lead-automation-' | sed "s/^/$(date -u +%FT%TZ),/; s| / |,|" || true
    sleep 15
  done ) > "$OUT/resources.csv" &
SAMPLER=$!
trap 'kill $SAMPLER 2>/dev/null || true' EXIT

# --- the harness ------------------------------------------------------------------------------
docker build -q -t lead-automation-e2e \
  --build-arg PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple}" e2e >/dev/null
mounts=()
for name in demo_password webhook_secrets internal_token fault_admin_token crm_admin_token crm_token; do
  mounts+=(-v "$PWD/secrets/$name:/run/secrets/$name:ro")
done
harness() {
  docker run --rm --network "${EVAL_NETWORK:-lead-automation_backend}" \
    --user "$(id -u):$(id -g)" -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
    -e EVAL_ONLY="${EVAL_ONLY:-}" -e EVAL_ALLOW_EXISTING="${EVAL_ALLOW_EXISTING:-}" \
    -e EVAL_LEAD_TIMEOUT_S="${EVAL_LEAD_TIMEOUT_S:-1800}" \
    -e EVAL_RESUME_FROM="${EVAL_RESUME_FROM:-}" -e EVAL_RERUN="${EVAL_RERUN:-}" \
    -v "$PWD/evals:/evals:ro" -v "$PWD/docs/evaluation:/data:ro" -v "$OUT:/out" \
    "${mounts[@]}" -w /evals lead-automation-e2e "$@"
}
harness python run_eval.py
kill $SAMPLER 2>/dev/null || true

# --- metrics ----------------------------------------------------------------------------------
harness python summarize_resources.py /out/resources.csv /out/resources.json \
  ${EVAL_MODEL_HOST_SAMPLES:+--model-host /out/model-host.csv}
harness python metrics.py /out/raw.json --out /out/results.json --resources /out/resources.json \
  --environment /out/environment.json --model /out/model.json --k6 /out/k6-summary.json
echo "results: evals/out/results.json"
