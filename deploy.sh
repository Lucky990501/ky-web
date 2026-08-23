#!/bin/zsh
# macOS entry point for the shared deployment configuration.
set -euo pipefail

project_root="$(cd -- "$(dirname -- "$0")" && pwd -P)"
exec zsh "$project_root/deployment/release.sh" --project-root "$project_root" "$@"

