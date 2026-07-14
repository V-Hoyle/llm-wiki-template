#!/usr/bin/env tsx
/**
 * Weekly wiki lint via Cursor SDK + karpathy-llm-wiki Lint workflow.
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { Agent, CursorAgentError } from "@cursor/sdk";

const WIKI_ROOT = join(homedir(), ".cursor", "llm-wiki");
const STATE_DIR = join(WIKI_ROOT, ".state");
const LAST_RUN_FILE = join(STATE_DIR, "lint-last-run.json");
const SKILL_PATH = join(homedir(), ".agents", "skills", "karpathy-llm-wiki", "SKILL.md");
const MIN_INTERVAL_MS = 6 * 24 * 60 * 60 * 1000; // 6 days — skip duplicate weekly runs

const dryRun = process.argv.includes("--dry-run");
const force = process.argv.includes("--force");

interface LastRunState {
  readonly at: string;
}

const loadLastRun = (): LastRunState | null => {
  if (!existsSync(LAST_RUN_FILE)) {
    return null;
  }
  try {
    return JSON.parse(readFileSync(LAST_RUN_FILE, "utf8")) as LastRunState;
  } catch {
    return null;
  }
};

const saveLastRun = (): void => {
  mkdirSync(STATE_DIR, { recursive: true });
  writeFileSync(LAST_RUN_FILE, JSON.stringify({ at: new Date().toISOString() }));
};

const buildPrompt = (): string => {
  const today = new Date().toISOString().slice(0, 10);

  return [
    "Run the Lint workflow on the local Karpathy LLM wiki.",
    "",
    `Wiki root: ${WIKI_ROOT}`,
    `Skill: ${SKILL_PATH}`,
    "",
    "Follow karpathy-llm-wiki SKILL.md — Lint section exactly:",
    "",
    "## Deterministic checks (auto-fix)",
    "- Index consistency: wiki/index.md vs actual wiki/ article files",
    "- Internal links: fix broken relative links when exactly one match exists",
    "- Raw references: fix broken Raw field links when exactly one match exists",
    "- See Also: add missing cross-refs within topic dirs; remove dead links",
    "",
    "## Heuristic checks (report only in log summary)",
    "- Factual contradictions, outdated claims, orphan pages, missing cross-topic refs",
    "",
    "## Post-lint",
    `Append to wiki/log.md: ## [${today}] lint | <N> issues found, <M> auto-fixed`,
    "Update wiki/index.md only when auto-fixing index entries.",
    "Do not modify raw/ files.",
    "Do not delete index entries for missing files — mark [MISSING] instead.",
  ].join("\n");
};

const main = async (): Promise<void> => {
  const lastRun = loadLastRun();
  if (!force && lastRun) {
    const elapsed = Date.now() - new Date(lastRun.at).getTime();
    if (elapsed < MIN_INTERVAL_MS) {
      console.log(
        `lint-worker: skipped — last run ${lastRun.at} (${Math.round(elapsed / 3600000)}h ago)`,
      );
      return;
    }
  }

  const apiKey = process.env.CURSOR_API_KEY;
  const prompt = buildPrompt();

  if (dryRun) {
    console.log("lint-worker: dry-run — would run lint");
    console.log(prompt.slice(0, 600), "...");
    return;
  }

  if (!apiKey) {
    console.error(
      "lint-worker: CURSOR_API_KEY not set. Add to ~/.cursor/llm-wiki/.env or shell profile.",
    );
    process.exit(1);
  }

  console.log("lint-worker: starting weekly lint");

  try {
    const result = await Agent.prompt(prompt, {
      apiKey,
      model: { id: "composer-2.5" },
      local: { cwd: WIKI_ROOT },
    });

    if (result.status === "error") {
      console.error("lint-worker: run failed", result.id);
      process.exit(2);
    }

    saveLastRun();
    console.log("lint-worker: success", result.id);
  } catch (err) {
    if (err instanceof CursorAgentError) {
      console.error("lint-worker: startup failed:", err.message, "retryable=", err.isRetryable);
      process.exit(1);
    }
    throw err;
  }
};

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
