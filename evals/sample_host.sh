#!/usr/bin/env bash
# Sample the model host while the benchmark runs: whole-host CPU, and the CPU and resident
# memory of the Ollama processes, every 15 s. Read-only (/proc); stops after the given seconds.
#
#   evals/sample_host.sh model-host.csv 14400
#
# CPU figures are per interval (deltas of /proc counters), not lifetime averages.
# Copy the CSV to evals/out/model-host.csv and set EVAL_MODEL_HOST_SAMPLES=1 for evals/run.sh.
set -euo pipefail
out=${1:-model-host.csv}
duration=${2:-14400}
interval=15
end=$(( $(date +%s) + duration ))
ticks=$(getconf CLK_TCK)
host_cpu() { awk '/^cpu /{print $2+$3+$4+$6+$7+$8, $5}' /proc/stat; }
ollama_ticks() {
  local total=0 rss=0 pid
  for pid in $(pgrep -x ollama || true); do
    if [ -r "/proc/$pid/stat" ]; then
      read -r ut st < <(awk '{print $14, $15}' "/proc/$pid/stat")
      total=$(( total + ut + st ))
      rss=$(( rss + $(awk '/VmRSS/{print $2}' "/proc/$pid/status" 2>/dev/null || echo 0) ))
    fi
  done
  echo "$total $rss"
}
echo "ts,host_cpu_percent,ollama_cpu_percent,ollama_rss_mib,mem_available_mib,logical_cpus" > "$out"
read -r busy0 idle0 < <(host_cpu)
read -r ot0 _ < <(ollama_ticks)
while [ "$(date +%s)" -lt "$end" ]; do
  sleep "$interval"
  read -r busy1 idle1 < <(host_cpu)
  read -r ot1 rss_kb < <(ollama_ticks)
  host=$(awk -v b=$((busy1 - busy0)) -v i=$((idle1 - idle0)) \
    'BEGIN{ if (b+i>0) printf "%.1f", 100*b/(b+i); else print 0 }')
  delta=$(( ot1 - ot0 )); [ "$delta" -lt 0 ] && delta=0   # a runner restarted
  ocpu=$(awk -v d="$delta" -v t="$ticks" -v s="$interval" 'BEGIN{printf "%.1f", 100*d/(t*s)}')
  busy0=$busy1; idle0=$idle1; ot0=$ot1
  avail=$(awk '/MemAvailable/{printf "%.0f", $2/1024}' /proc/meminfo)
  echo "$(date -u +%FT%TZ),$host,$ocpu,$((rss_kb / 1024)),$avail,$(nproc)" >> "$out"
done
