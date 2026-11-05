#!/usr/bin/env bash
set -euo pipefail

# One-time developer workstation setup. Do not run this script in GitHub Actions:
# init --browser requires an interactive browser login. CI uses FIRECRAWL_API_KEY.
npx --yes skills add firecrawl/skills --skill firecrawl-build --agent codex -y
npx --yes firecrawl-cli@latest init -y --browser
firecrawl setup build

echo "Firecrawl build skills installed. Configure FIRECRAWL_API_KEY in the runtime secrets."
