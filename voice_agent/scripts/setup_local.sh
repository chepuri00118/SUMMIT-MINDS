#!/usr/bin/env bash
# Set up the fully local stack: no accounts, no API keys, nothing billed.
#
#   ./scripts/setup_local.sh
#
# Installs Ollama (the brain), pulls a model, installs faster-whisper (ears)
# and Piper (voice), and downloads one Piper voice.
set -euo pipefail

cd "$(dirname "$0")/.."

OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:7b-instruct}"
VOICE_DIR="models/piper"
VOICE_NAME="en_US-lessac-medium"
VOICE_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"

echo "==> Python packages (whisper, piper, audio)"
pip install -r requirements-local.txt

echo "==> Ollama"
if ! command -v ollama >/dev/null 2>&1; then
  case "$(uname -s)" in
    Linux)  curl -fsSL https://ollama.com/install.sh | sh ;;
    Darwin) echo "Install Ollama from https://ollama.com/download then re-run this script."; exit 1 ;;
    *)      echo "Install Ollama from https://ollama.com/download then re-run this script."; exit 1 ;;
  esac
else
  echo "    already installed"
fi

echo "==> Pulling $OLLAMA_MODEL (a few GB, this is the slow part)"
ollama pull "$OLLAMA_MODEL"

echo "==> Piper voice"
mkdir -p "$VOICE_DIR"
for ext in onnx onnx.json; do
  if [ ! -f "$VOICE_DIR/$VOICE_NAME.$ext" ]; then
    curl -fL -o "$VOICE_DIR/$VOICE_NAME.$ext" "$VOICE_BASE/$VOICE_NAME.$ext"
  fi
done

cat <<EOF

Done. Add these to your .env:

  BRAIN_PROVIDER=ollama
  OLLAMA_MODEL=$OLLAMA_MODEL
  PIPER_VOICE=$VOICE_DIR/$VOICE_NAME.onnx

Then:

  python scripts/simulate_call.py --script hostile   # text, fastest to iterate
  python scripts/talk_local.py                       # actually talk to it

EOF
