#!/bin/zsh
cd "$(dirname "$0")"
exec .venv/bin/python -m w800 ui
