#!/usr/bin/env bash
# One-command server setup for the voice agent on Ubuntu 24.04.
#
#   HOSTNAME=py-voice-agent.duckdns.org bash deploy/install.sh
#
# Installs Python deps into /opt/voice-agent/.venv, runs the bot as a systemd service
# on 127.0.0.1:7860, and puts Caddy in front for automatic HTTPS on $HOSTNAME.
# Safe to re-run: it updates the code and restarts the service.
#
# Afterwards, copy the secrets (not in git) into /opt/voice-agent/server/:
#   .env  google_credentials.json  google_token.json
# then: sudo systemctl restart voice-agent

set -euo pipefail

REPO="${REPO:-https://github.com/ahmedfarooq-dev/voice-agent.git}"
APP_DIR="${APP_DIR:-/opt/voice-agent}"
HOSTNAME="${HOSTNAME:?Set HOSTNAME, e.g. HOSTNAME=py-voice-agent.duckdns.org bash deploy/install.sh}"
RUN_USER="${SUDO_USER:-$USER}"

echo "==> System packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git curl debian-keyring debian-archive-keyring apt-transport-https

echo "==> Caddy (HTTPS reverse proxy)"
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg --yes
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq caddy
fi

echo "==> Code"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  sudo mkdir -p "$APP_DIR"
  sudo chown "$RUN_USER":"$RUN_USER" "$APP_DIR"
  git clone "$REPO" "$APP_DIR"
fi

echo "==> Python environment"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q \
  "pipecat-ai[deepgram,groq,runner,silero,webrtc,websocket]>=1.11,<2" \
  tzdata python-docx google-api-python-client google-auth-oauthlib

echo "==> systemd service"
sudo tee /etc/systemd/system/voice-agent.service >/dev/null <<EOF
[Unit]
Description=Voice agent (Pipecat)
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$APP_DIR/server
Environment=PYTHONUTF8=1
# --ice-servers: a STUN server lets the bot learn its public IP (EC2 sits behind 1:1 NAT)
# so browsers can reach it directly for WebRTC audio.
ExecStart=$APP_DIR/.venv/bin/python bot.py --host 127.0.0.1 --port 7860 --ice-servers stun:stun.l.google.com:19302
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable voice-agent >/dev/null

echo "==> Caddy config for https://$HOSTNAME"
sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
$HOSTNAME {
    reverse_proxy 127.0.0.1:7860
}
EOF
sudo systemctl enable caddy >/dev/null
sudo systemctl restart caddy

if [ -f "$APP_DIR/server/.env" ]; then
  sudo systemctl restart voice-agent
  echo "==> Bot restarted."
else
  echo "==> Code installed. The bot is NOT started yet: copy .env, google_credentials.json and"
  echo "    google_token.json into $APP_DIR/server/ then run: sudo systemctl restart voice-agent"
fi

echo
echo "Done. Useful commands:"
echo "  sudo systemctl status voice-agent        # is it running?"
echo "  sudo journalctl -u voice-agent -f        # live logs"
echo "  sudo journalctl -u caddy -n 50           # HTTPS certificate logs"
echo "  HOSTNAME=$HOSTNAME bash $APP_DIR/deploy/install.sh   # update to latest code"
