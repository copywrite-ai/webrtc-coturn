#!/usr/bin/env bash
set -euo pipefail

exec ffmpeg -f avfoundation -list_devices true -i ""
