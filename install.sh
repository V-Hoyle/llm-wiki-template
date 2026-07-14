#!/usr/bin/env bash
# Install LLM wiki template into ~/.cursor/llm-wiki + user hooks/rules.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WIKI_ROOT="${HOME}/.cursor/llm-wiki"
CURSOR_HOOKS="${HOME}/.cursor/hooks"
CURSOR_RULES="${HOME}/.cursor/rules"
LAUNCH_AGENTS="${HOME}/Library/LaunchAgents"
USER_LABEL="com.${USER}.llm-wiki"

echo "=== LLM Wiki Template Install ==="
echo "Repo:  ${REPO_ROOT}"
echo "Wiki:  ${WIKI_ROOT}"
echo ""

# 1. Sync wiki root (if cloned elsewhere, copy into ~/.cursor/llm-wiki)
if [[ "${REPO_ROOT}" != "${WIKI_ROOT}" ]]; then
  echo "Copying template into ${WIKI_ROOT}..."
  mkdir -p "${WIKI_ROOT}"
  rsync -a --exclude '.git' \
    --exclude 'queue/pending.jsonl' \
    --exclude 'queue/processed' \
    --exclude '.state' \
    "${REPO_ROOT}/" "${WIKI_ROOT}/"
else
  echo "Using repo in place at ${WIKI_ROOT}"
fi

mkdir -p "${WIKI_ROOT}/queue/processed" "${WIKI_ROOT}/.state/logs"

# 2. Hooks
echo "Installing hook scripts to ${CURSOR_HOOKS}..."
mkdir -p "${CURSOR_HOOKS}/lib"
install -m 755 "${WIKI_ROOT}/hooks/wiki-session-start.sh" "${CURSOR_HOOKS}/"
install -m 755 "${WIKI_ROOT}/hooks/wiki-session-end.sh" "${CURSOR_HOOKS}/"
install -m 755 "${WIKI_ROOT}/hooks/wiki-doc-deps.sh" "${CURSOR_HOOKS}/"
install -m 644 "${WIKI_ROOT}/hooks/lib/wiki-common.sh" "${CURSOR_HOOKS}/lib/"

if [[ ! -f "${HOME}/.cursor/hooks.json" ]]; then
  echo "Creating ~/.cursor/hooks.json from example..."
  cp "${WIKI_ROOT}/examples/hooks.json" "${HOME}/.cursor/hooks.json"
else
  echo "NOTE: ~/.cursor/hooks.json already exists — merge manually from examples/hooks.json"
fi

# 3. Cursor rule
echo "Installing ~/.cursor/rules/llm-wiki.mdc..."
mkdir -p "${CURSOR_RULES}"
cp "${WIKI_ROOT}/examples/llm-wiki.mdc" "${CURSOR_RULES}/llm-wiki.mdc"

# 4. Karpathy skill
if [[ ! -f "${HOME}/.agents/skills/karpathy-llm-wiki/SKILL.md" ]]; then
  echo "Installing karpathy-llm-wiki skill..."
  npx --yes add-skill Astro-Han/karpathy-llm-wiki || echo "WARN: skill install failed — run: npx add-skill Astro-Han/karpathy-llm-wiki"
fi

# 4b. Vendored wiki-doc-deps skill (dependency-metadata convention)
echo "Installing wiki-doc-deps skill..."
mkdir -p "${HOME}/.agents/skills/wiki-doc-deps"
cp "${WIKI_ROOT}/skills/wiki-doc-deps/SKILL.md" "${HOME}/.agents/skills/wiki-doc-deps/SKILL.md"

# 4c. wiki-deps CLI onto PATH (best-effort; prefers a writable dir already on PATH)
for bindir in /opt/homebrew/bin /usr/local/bin "${HOME}/.local/bin"; do
  if [[ -d "${bindir}" && -w "${bindir}" ]] || { [[ "${bindir}" == "${HOME}/.local/bin" ]] && mkdir -p "${bindir}" 2>/dev/null; }; then
    ln -sf "${WIKI_ROOT}/scripts/wiki-deps" "${bindir}/wiki-deps"
    echo "Linked wiki-deps CLI -> ${bindir}/wiki-deps"
    break
  fi
done

# 4d. Search backend: Python venv (qdrant-client + ollama) for semantic search.
VENV="${WIKI_ROOT}/.venv"
if [[ ! -x "${VENV}/bin/python" ]]; then
  PYBIN=""
  # Prefer 3.11-3.13 for the widest qdrant-client/grpcio/numpy wheel coverage.
  for c in python3.12 python3.13 python3.11 python3; do
    command -v "$c" >/dev/null 2>&1 && { PYBIN="$c"; break; }
  done
  if [[ -n "${PYBIN}" ]]; then
    echo "Creating search venv (${PYBIN}) at ${VENV}..."
    "${PYBIN}" -m venv "${VENV}"
    "${VENV}/bin/python" -m pip install --quiet --upgrade pip
    "${VENV}/bin/python" -m pip install --quiet -r "${WIKI_ROOT}/requirements.txt" \
      || echo "WARN: search deps failed to install — semantic search disabled until 'pip install -r requirements.txt' succeeds."
  else
    echo "WARN: no python3 found — skipping search venv (lexical search + deps still work)."
  fi
fi

# 4e. Ollama embedding model (local semantic search backend).
EMBED_MODEL="${WIKI_EMBED_MODEL:-qwen3-embedding:8b}"
if command -v ollama >/dev/null 2>&1; then
  if ! ollama list 2>/dev/null | grep -q "${EMBED_MODEL%%:*}"; then
    echo "Pulling Ollama embedding model ${EMBED_MODEL} (~4.7 GB, one-time)..."
    ollama pull "${EMBED_MODEL}" \
      || echo "WARN: 'ollama pull ${EMBED_MODEL}' failed — pull it manually for semantic search."
  fi
else
  cat <<EOF
NOTE: Ollama is not installed — semantic search is disabled (lexical search + deps still work).
  1. Install: https://ollama.com/download   (macOS: brew install ollama)
  2. Start:   ollama serve                   (or open the Ollama app)
  3. Model:   ollama pull ${EMBED_MODEL}
  4. Index:   wiki-deps reindex
EOF
fi

# 5. Worker deps
echo "Installing npm dependencies..."
cd "${WIKI_ROOT}/scripts"
npm install --silent

# 6. Env
if [[ ! -f "${WIKI_ROOT}/.env" ]]; then
  cp "${WIKI_ROOT}/.env.example" "${WIKI_ROOT}/.env"
  echo "Created ${WIKI_ROOT}/.env — add CURSOR_API_KEY for background ingest"
fi

# 7. Optional launchd (macOS)
if [[ "$(uname)" == "Darwin" ]] && [[ "${INSTALL_LAUNCHD:-}" == "1" ]]; then
  echo "Installing launchd jobs to ${LAUNCH_AGENTS}..."
  for plist in ingest backup lint backlog-sync; do
    src="${WIKI_ROOT}/launchd/com.USER.llm-wiki-${plist}.plist"
    dst="${LAUNCH_AGENTS}/${USER_LABEL}-${plist}.plist"
    sed "s|__HOME__|${HOME}|g; s|com.USER|com.${USER}|g" "${src}" > "${dst}"
    launchctl bootout "gui/$(id -u)/${USER_LABEL}-${plist}" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "${dst}"
  done
  echo "Launchd jobs loaded (ingest every 15m, backup daily 04:00, lint Sun 03:00, backlog Sat 02:00)"
else
  echo "Skipping launchd (set INSTALL_LAUNCHD=1 to install background workers)"
fi

# 8. Git init for personal backup
if [[ ! -d "${WIKI_ROOT}/.git" ]]; then
  git -C "${WIKI_ROOT}" init -b main
  echo "Initialized git repo in ${WIKI_ROOT}"
fi

# 8b. Wiki-repo post-commit hook: keep the embedding index fresh after commits.
PC="${WIKI_ROOT}/.git/hooks/post-commit"
if [[ ! -e "${PC}" ]]; then
  install -m 755 "${WIKI_ROOT}/hooks/wiki-reindex-post-commit" "${PC}"
  echo "Installed wiki post-commit reindex hook"
elif ! grep -q build-wiki-index "${PC}" 2>/dev/null; then
  echo "NOTE: ${PC} exists — chain in hooks/wiki-reindex-post-commit manually"
fi

# 9. Build the doc-dependency index (best-effort)
if command -v python3 >/dev/null 2>&1; then
  python3 "${WIKI_ROOT}/scripts/build-doc-deps-index.py" --tld "${HOME}/src" --quiet || true
fi

# 9b. Build the embedding/BM25 search index (best-effort; needs venv + Ollama).
if [[ -x "${VENV}/bin/python" ]]; then
  echo "Building wiki embedding index (first build may take a few minutes)..."
  "${VENV}/bin/python" "${WIKI_ROOT}/scripts/build-wiki-index.py" \
    || echo "WARN: embedding index build failed — run 'wiki-deps reindex' once Ollama is set up."
fi

# 9c. Register the unified 'wiki' MCP server (search + RAG + deps).
MCP_JSON="${HOME}/.cursor/mcp.json"
if [[ ! -f "${MCP_JSON}" ]]; then
  cat > "${MCP_JSON}" <<EOF
{
  "mcpServers": {
    "wiki": {
      "command": "${VENV}/bin/python",
      "args": ["${WIKI_ROOT}/scripts/wiki_mcp.py"]
    }
  }
}
EOF
  echo "Created ${MCP_JSON} with the 'wiki' MCP server"
else
  echo "NOTE: ${MCP_JSON} exists — ensure it includes the 'wiki' server:"
  echo "      \"wiki\": { \"command\": \"${VENV}/bin/python\", \"args\": [\"${WIKI_ROOT}/scripts/wiki_mcp.py\"] }"
fi

echo ""
echo "=== Done ==="
echo "Next steps:"
echo "  1. Edit ${WIKI_ROOT}/schema/topics.json for your repos"
echo "  2. Edit ${CURSOR_HOOKS}/lib/wiki-common.sh path keywords"
echo "  3. Add CURSOR_API_KEY to ${WIKI_ROOT}/.env"
echo "  4. Read BOOTSTRAP.md — seed wiki content (Phase 3)"
echo "  5. Install repo reminder hooks: wiki-deps install-hooks --tld ~/src (see DOC-DEPENDENCIES.md)"
echo "  6. Semantic search: ensure Ollama is running (ollama serve) + model pulled (${EMBED_MODEL}); then 'wiki-deps reindex'"
echo "  7. Restart Cursor so the 'wiki' MCP server (search + RAG + deps) loads"
echo "  8. Optional: INSTALL_LAUNCHD=1 ./install.sh"
