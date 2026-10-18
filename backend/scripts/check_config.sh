#!/usr/bin/env bash
# Read-only check: is the RUNNING AirSOAP backend using the settings in your env file?
#
# A systemd service reads ~/.config/airsoap/airsoap.env only when it STARTS. If you edit the file
# and do not restart the service, the backend keeps the old settings. This script compares the two
# and tells you what to fix. It changes nothing.
#
#   bash scripts/check_config.sh
#
# For testing without systemd: AIRSOAP_CHECK_ENVIRON_FILE=<NUL-separated environment file> bash ...
set -u

ENV_FILE="${AIRSOAP_ENV_FILE:-$HOME/.config/airsoap/airsoap.env}"
UNIT="airsoap-backend.service"
API="${AIRSOAP_API:-http://127.0.0.1:8000}"
problems=0

ok()  { printf '  [ok]   %s\n' "$1"; }
fix() { printf '  [FIX]  %s\n' "$1"; problems=$((problems + 1)); }

echo "AirSOAP configuration check"

if [ -r "$ENV_FILE" ]; then
  ok "env file found: $ENV_FILE"
else
  fix "env file not found: $ENV_FILE (copy backend/deploy/airsoap.env.example there)"
  exit 1
fi

# Things in the file that systemd would read wrongly.
if grep -q $'\r' "$ENV_FILE"; then
  fix "the env file has Windows line endings (^M); run: sed -i 's/\\r\$//' $ENV_FILE"
fi
if grep -nE '^[[:space:]]*AIRSOAP_[A-Z_]+=[^#]*[[:space:]]#' "$ENV_FILE" > /dev/null; then
  fix "a value has a comment on the same line; systemd keeps it as part of the value. Put comments on their own line:"
  grep -nE '^[[:space:]]*AIRSOAP_[A-Z_]+=[^#]*[[:space:]]#' "$ENV_FILE" | sed 's/^/         line /'
fi

# What the running backend actually has.
if [ -n "${AIRSOAP_CHECK_ENVIRON_FILE:-}" ]; then
  environ_file="$AIRSOAP_CHECK_ENVIRON_FILE"
  started=""
else
  if ! command -v systemctl > /dev/null; then
    echo "  systemctl not found: run this on the Raspberry Pi"
    exit 1
  fi
  if ! systemctl --user is-active --quiet "$UNIT"; then
    fix "$UNIT is not running: systemctl --user status $UNIT"
    exit 1
  fi
  pid="$(systemctl --user show -p MainPID --value "$UNIT")"
  environ_file="/proc/$pid/environ"
  started="$(ps -o lstart= -p "$pid" 2>/dev/null)"
  ok "$UNIT is running (pid $pid, started $started)"
fi
running="$(tr '\0' '\n' < "$environ_file" 2> /dev/null | grep '^AIRSOAP_' || true)"

# Compare every AIRSOAP_ setting in the file with what the process holds.
stale=0
while IFS= read -r line; do
  key="${line%%=*}"
  want="${line#*=}"
  have="$(printf '%s\n' "$running" | grep "^${key}=" | head -n 1 | cut -d= -f2-)"
  if [ -z "$have" ] && ! printf '%s\n' "$running" | grep -q "^${key}="; then
    fix "$key is in the env file but the running backend does not have it"
    stale=1
  elif [ "$have" != "$want" ]; then
    fix "$key differs: file has '$want', running backend has '$have'"
    stale=1
  fi
done < <(grep -E '^[[:space:]]*AIRSOAP_[A-Z_]+=' "$ENV_FILE" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')
if [ "$stale" -eq 0 ]; then
  ok "the running backend has every AIRSOAP_ setting from the env file"
else
  echo "         -> restart it:  systemctl --user daemon-reload && systemctl --user restart $UNIT"
fi

# The settings that matter most on the Pi.
setting() { printf '%s\n' "$running" | grep "^$1=" | head -n 1 | cut -d= -f2-; }
lifecycle="$(setting AIRSOAP_WHISPER_LIFECYCLE)"
if [ "$lifecycle" = "on-demand" ]; then
  ok "whisper-server mode: on-demand"
  for var in AIRSOAP_WHISPER_SERVER_PATH AIRSOAP_WHISPER_MODEL_PATH; do
    path="$(setting "$var")"
    if [ -z "$path" ]; then
      fix "$var is not set in the running backend"
    elif [ "$var" = AIRSOAP_WHISPER_SERVER_PATH ] && [ ! -x "$path" ]; then
      fix "$var is not an executable file: $path"
    elif [ "$var" = AIRSOAP_WHISPER_MODEL_PATH ] && [ ! -f "$path" ]; then
      fix "$var does not exist: $path"
    else
      ok "$var -> $path"
    fi
  done
else
  fix "AIRSOAP_WHISPER_LIFECYCLE is '${lifecycle:-not set}', so the backend expects an always-on whisper-server on port 8178"
fi
live_model="$(setting AIRSOAP_LIVE_WHISPER_MODEL_PATH)"
if [ -z "$live_model" ]; then
  ok "live transcription: off (whole-file transcription after Stop)"
elif [ -f "$live_model" ]; then
  ok "live transcription: on, model $live_model"
else
  fix "AIRSOAP_LIVE_WHISPER_MODEL_PATH does not exist: $live_model"
fi
model="$(setting AIRSOAP_OLLAMA_MODEL)"
ok "Ollama model: ${model:-qwen2.5:3b-instruct (default)}"

# Sign-in and storage settings.
perm() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null; }
env_mode="$(perm "$ENV_FILE")"
case "$env_mode" in
  600|400) ok "env file is readable only by you ($env_mode)" ;;
  *) fix "env file permissions are $env_mode; run: chmod 600 $ENV_FILE" ;;
esac
db="$(setting AIRSOAP_DATABASE_PATH)"
if [ -z "$db" ]; then
  fix "AIRSOAP_DATABASE_PATH is not set (set it to a path on the SD card, e.g. \$HOME/.local/state/airsoap/airsoap.db)"
elif [ ! -f "$db" ]; then
  fix "the account database does not exist yet: $db (it is created when the backend starts)"
else
  db_mode="$(perm "$db")"; dir_mode="$(perm "$(dirname "$db")")"
  if [ "$db_mode" = 600 ] && [ "$dir_mode" = 700 ]; then
    ok "account database: $db (600, folder 700)"
  else
    fix "account database permissions are $db_mode (folder $dir_mode); expected 600 and 700"
  fi
fi
case "$(setting AIRSOAP_COOKIE_SECURE)" in
  true|True|1|"") ok "session cookie is HTTPS-only" ;;
  *) fix "AIRSOAP_COOKIE_SECURE is not true; the session cookie could be sent without HTTPS" ;;
esac
origins="$(setting AIRSOAP_ALLOWED_ORIGINS)"
case "$origins" in
  "") ok "allowed origin: https://raspberrypi.local (default)" ;;
  *http://*) fix "AIRSOAP_ALLOWED_ORIGINS contains a plain-HTTP origin: $origins" ;;
  *) ok "allowed origins: $origins" ;;
esac
case "$(setting AIRSOAP_HOST)" in
  ""|127.0.0.1|localhost|::1) ok "backend listens on loopback only (Caddy is the only way in)" ;;
  *) fix "AIRSOAP_HOST is $(setting AIRSOAP_HOST); set it to 127.0.0.1 and let Caddy serve HTTPS" ;;
esac

# What is reachable.
if [ -z "${AIRSOAP_CHECK_ENVIRON_FILE:-}" ] && command -v curl > /dev/null; then
  health="$(curl -s -m 5 "$API/api/health" || true)"
  case "$health" in
    *'"status":"ok"'*) ok "backend answers on $API" ;;
    *) fix "no answer from $API/api/health" ;;
  esac
  if curl -s -m 5 http://127.0.0.1:11434/api/tags > /dev/null; then
    ok "Ollama answers on 127.0.0.1:11434"
  else
    fix "Ollama does not answer on 127.0.0.1:11434: systemctl status ollama"
  fi
  if command -v ss > /dev/null; then
    exposed="$(ss -Hltn 2>/dev/null | awk '{print $4}' | grep -E ':(8000|8178|8179|11434)$' | grep -vE '^(127\.0\.0\.1|\[::1\]):' || true)"
    if [ -z "$exposed" ]; then
      ok "backend, both whisper-servers and Ollama are bound to loopback only"
    else
      fix "listening beyond loopback: $(echo "$exposed" | tr '\n' ' ')"
    fi
  fi
  if systemctl is-active --quiet caddy 2>/dev/null; then
    ok "Caddy is running"
    # -k only for this liveness probe: the point is that Caddy forwards to the backend.
    https_health="$(curl -sk -m 5 https://raspberrypi.local/api/health || true)"
    case "$https_health" in
      *'"status":"ok"'*) ok "https://raspberrypi.local reaches the backend" ;;
      *) fix "https://raspberrypi.local/api/health did not answer; check /etc/caddy/Caddyfile and: sudo systemctl status caddy" ;;
    esac
  else
    fix "Caddy is not running: sudo systemctl status caddy"
  fi
  echo "  For the detailed status (microphone, whisper, model), sign in and run: .venv/bin/python scripts/smoke_test.py --status"
fi

echo
if [ "$problems" -eq 0 ]; then echo "All good."; else echo "$problems thing(s) to fix."; fi
[ "$problems" -eq 0 ]
