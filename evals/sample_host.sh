#!/usr/bin/env bash
# Sample the model host while the benchmark runs: total CPU use, the Ollama process's CPU and
# resident memory, every 15 s. Read-only (/proc and ps); stops after the given seconds.
#
#   evals/sample_host.sh model-host.csv 14400
#
# Copy the CSV to evals/out/model-host.csv and set EVAL_MODEL_HOST_SAMPLES=1 for evals/run.sh.
set -euo pipefail
out=${1:-model-host.csv}
duration=${2:-14400}
end=$(( $(date +%s) + duration ))
read_cpu() { awk '/^cpu /{print $2+$3+$4+$6+$7+$8, $5}' /proc/stat; }
echo "ts,host_cpu_percent,ollama_cpu_percent,ollama_rss_mib,mem_available_mib,logical_cpus" > "$out"
read -r busy0 idle0 < <(read_cpu)
while [ "$(date +%s)" -lt "$end" ]; do
  sleep 15
  read -r busy1 idle1 < <(read_cpu)
  host=$(awk -v b=$((busy1 - busy0)) -v i=$((idle1 - idle0)) 'BEGIN{ if (b+i>0) printf "%.1f", 100*b/(b+i); else print 0 }')
  busy0=$busy1; idle0=$idle1
  read -r ocpu orss < <(ps -C ollama -o %cpu=,rss= | awk '{c+=$1; r+=$2} END{printf "%.1f %.0f\n", c, r/1024}')
  avail=$(awk '/MemAvailable/{printf "%.0f", $2/1024}' /proc/meminfo)
  echo "$(date -u +%FT%TZ),$host,${ocpu:-0},${orss:-0},$avail,$(nproc)" >> "$out"
done
