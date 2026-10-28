#!/bin/zsh
set -e
cd "${0:A:h}"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
if [[ ! -x .venv/bin/python ]]; then
  uv sync --extra mac
fi
if [[ -f .local/ui-url ]] && /usr/bin/curl --silent --max-time 2 http://127.0.0.1:8765/ >/dev/null 2>&1; then
  open "$(cat .local/ui-url)"
else
  .venv/bin/python -m imessage_bot.cli ui
fi
