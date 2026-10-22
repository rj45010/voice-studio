#!/bin/sh
# Voice Studio installer for macOS and Linux: curl -fsSL <url>/install.sh | sh
set -eu

REPO="https://voice-studio.seemsfit.workers.dev"
SITE="https://voice-studio.seemsfit.workers.dev"
APP="$HOME/.voice-studio/app"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (Python installer)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

say "Installing the voice engine (about 1 GB)"
uv tool install --python 3.12 omnivoice
PY="$(uv tool dir)/omnivoice/bin/python"

say "Downloading Voice Studio"
mkdir -p "$APP"
curl -fsSL "$REPO/server.py" -o "$APP/server.py"
curl -fsSL "$REPO/index.html" -o "$APP/index.html"

say "Downloading the voice model (3.3 GB, the slow part)"
HF_HUB_OFFLINE=0 "$PY" -c "from huggingface_hub import snapshot_download; snapshot_download('k2-fsa/OmniVoice')"

say "Starting Voice Studio in the background, now and at every login"
case "$(uname)" in
  Darwin)
    PLIST="$HOME/Library/LaunchAgents/dev.voice-studio.plist"
    mkdir -p "$(dirname "$PLIST")"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>dev.voice-studio</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$APP/server.py</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>$HOME/.voice-studio/server.log</string>
</dict></plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    OPEN=open
    ;;
  *)
    UNIT="$HOME/.config/systemd/user/voice-studio.service"
    mkdir -p "$(dirname "$UNIT")"
    printf '[Unit]\nDescription=Voice Studio\n\n[Service]\nExecStart=%s %s\nRestart=on-failure\n\n[Install]\nWantedBy=default.target\n' "$PY" "$APP/server.py" > "$UNIT"
    systemctl --user daemon-reload
    systemctl --user enable --now voice-studio
    OPEN=xdg-open
    ;;
esac

say "Done. Opening $SITE"
sleep 3
"$OPEN" "$SITE" >/dev/null 2>&1 || echo "Open $SITE in your browser."
