#!/bin/bash
# Restart cloudflared quick tunnels for the local graded servers and print the public URLs.
# Usage: bash tunnels.sh [port ...]   (default: 8072 8081 8091)
cd "$(dirname "$0")"
declare -A PATHS=([8072]="/.well-known/agent-card.json" [8081]="/mailroom" [8091]="/")
declare -A SUFFIX=([8072]="/a2a/" [8081]="/mailroom" [8091]="")
declare -A METHOD=([8072]="GET" [8081]="POST" [8091]="GET")
declare -A WANT=([8072]="200" [8081]="400" [8091]="200")

for port in "${@:-8072 8081 8091}"; do
  for port in $port; do
    url=
    for try in 1 2 3 4 5; do
      powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | ? { \$_.CommandLine -like '*:$port*' } | % { Stop-Process -Id \$_.ProcessId -Force }"
      log="tunnel$port.log"; rm -f "$log"
      (nohup cloudflared tunnel --no-autoupdate --protocol http2 --url "http://127.0.0.1:$port" > "$log" 2>&1 &)
      for j in $(seq 1 20); do
        sleep 2
        url=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$log" | head -1)
        [ -n "$url" ] && grep -q "Registered tunnel connection" "$log" && break
      done
      sleep 8
      code=$(curl -s -o /dev/null -m 15 -w "%{http_code}" -X "${METHOD[$port]}" -d '{}' "$url${PATHS[$port]}")
      [ "$code" = "${WANT[$port]}" ] && break
      url=
    done
    echo "$port -> ${url:-FAILED}${url:+${SUFFIX[$port]}}"
  done
done
