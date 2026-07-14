#!/usr/bin/env tsx
/**
 * Process one pending item from ~/.cursor/llm-wiki/queue/pending.jsonl
 * using the Cursor SDK and karpathy-llm-wiki skill.
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync, appendFileSync } from "node:fs";
import { execSync } from "node:child_process";
import { homedir } from "node:os";
import { join } from "node:path";
import { Agent, CursorAgentError } from "@cursor/sdk";

const WIKI_ROOT = join(homedir(), ".cursor", "llm-wiki");
const QUEUE_FILE = join(WIKI_ROOT, "queue", "pending.jsonl");
const PROCESSED_DIR = join(WIKI_ROOT, "queue", "processed");
const STATE_DIR = join(WIKI_ROOT, ".state");
const RATE_LIMIT_FILE = join(STATE_DIR, "ingest-ratelimit.json");
const SKILL_PATH = join(homedir(), ".agents", "skills", "karpathy-llm-wiki", "SKILL.md");
const MAX_PER_HOUR = 4;

interface QueueEntry {
  readonly ts: string;
  readonly conversation_id: string;
  readonly workspace: string;
  readonly topics: readonly string[];
  readonly transcript_path: string;
  readonly transcript_excerpt: string;
  readonly status: string;
  readonly ingest_kind?: string;
  readonly raw_path?: string;
}

interface RateLimitState {
  readonly hour: string;
  readonly count: number;
}

const dryRun = process.argv.includes("--dry-run");
const skipRateLimit = process.argv.includes("--no-rate-limit");
const skipCommit = process.argv.includes("--no-commit");

const parseCountArg = (): number => {
  const eqArg = process.argv.find((a) => a.startsWith("--count="));
  if (eqArg) {
    const n = Number.parseInt(eqArg.split("=")[1] ?? "1", 10);
    return Number.isFinite(n) && n > 0 ? n : 1;
  }
  const idx = process.argv.indexOf("--count");
  if (idx >= 0) {
    const n = Number.parseInt(process.argv[idx + 1] ?? "1", 10);
    return Number.isFinite(n) && n > 0 ? n : 1;
  }
  return 1;
};

const maxCount = parseCountArg();

const loadRateLimit = (): RateLimitState => {
  const hour = new Date().toISOString().slice(0, 13);
  if (!existsSync(RATE_LIMIT_FILE)) {
    return { hour, count: 0 };
  }
  try {
    const state = JSON.parse(readFileSync(RATE_LIMIT_FILE, "utf8")) as RateLimitState;
    if (state.hour !== hour) {
      return { hour, count: 0 };
    }
    return state;
  } catch {
    return { hour, count: 0 };
  }
};

const saveRateLimit = (state: RateLimitState): void => {
  mkdirSync(STATE_DIR, { recursive: true });
  writeFileSync(RATE_LIMIT_FILE, JSON.stringify(state));
};

const readOldestPending = (): { entry: QueueEntry; line: string } | null => {
  if (!existsSync(QUEUE_FILE)) {
    return null;
  }
  const content = readFileSync(QUEUE_FILE, "utf8").trim();
  if (!content) {
    return null;
  }
  const lines = content.split("\n").filter(Boolean);
  const line = lines[0];
  return { entry: JSON.parse(line) as QueueEntry, line };
};

const removeLineFromQueue = (lineToRemove: string): void => {
  if (!existsSync(QUEUE_FILE)) {
    return;
  }
  const lines = readFileSync(QUEUE_FILE, "utf8").split("\n").filter(Boolean);
  const remaining = lines.filter((l) => l !== lineToRemove);
  writeFileSync(QUEUE_FILE, remaining.length ? `${remaining.join("\n")}\n` : "");
};

const buildPrompt = (entry: QueueEntry): string => {
  let transcript = entry.transcript_excerpt ?? "";
  if (!transcript && entry.transcript_path && existsSync(entry.transcript_path)) {
    const raw = readFileSync(entry.transcript_path, "utf8");
    transcript = raw.slice(-12000);
  }

  const isAdhoc = entry.ingest_kind === "adhoc";
  const topicHint = (entry.topics ?? []).join(", ") || "decisions";
  const rawPath = entry.raw_path ?? "";

  const lines = isAdhoc
    ? [
        "Ingest this ad-hoc source file into the local Karpathy LLM wiki.",
        "",
        `Wiki root: ${WIKI_ROOT}`,
        `Skill: ${SKILL_PATH}`,
        "",
        "The source is already copied to raw/. Follow karpathy-llm-wiki SKILL.md — Ingest workflow:",
        "1. Do NOT re-copy raw/ — use the existing raw file below",
        "2. Compile durable facts into wiki/ (preferred topic below)",
        "3. Update wiki/index.md and append wiki/log.md",
        "4. Focus on durable facts; skip fluff",
        "",
        `Source file: ${entry.workspace}`,
        `Raw path: ${rawPath}`,
        `Topics hint: ${topicHint}`,
        `Queued: ${entry.ts}`,
      ]
    : [
        "Ingest this Cursor session into the local Karpathy LLM wiki.",
        "",
        `Wiki root: ${WIKI_ROOT}`,
        `Skill: ${SKILL_PATH}`,
        "",
        "Follow karpathy-llm-wiki SKILL.md exactly:",
        "1. Save session source to raw/decisions/ (redact any secrets)",
        "2. Compile durable facts into wiki/decisions/ or the most relevant topic dir",
        "3. Update wiki/index.md and append wiki/log.md",
        "4. Skip trivial chit-chat; focus on decisions, runbooks, architecture, repo facts",
        "",
        `Session timestamp: ${entry.ts}`,
        `Conversation ID: ${entry.conversation_id}`,
        `Workspace: ${entry.workspace}`,
        `Topics hint: ${topicHint}`,
      ];

  return [
    ...lines,
    "",
    isAdhoc ? "--- Source content ---" : "--- Transcript excerpt ---",
    transcript.slice(0, 10000) || "(no content available)",
  ].join("\n");
};

type ProcessOutcome =
  | { readonly status: "done"; readonly conversationId: string }
  | { readonly status: "empty" }
  | { readonly status: "rate_limited" }
  | { readonly status: "error" };

const commitWiki = (processedIds: readonly string[]): void => {
  if (dryRun || skipCommit || processedIds.length === 0) {
    return;
  }

  const preview = processedIds.slice(0, 3).join(", ");
  const suffix = processedIds.length > 3 ? ` (+${processedIds.length - 3} more)` : "";
  const message = `ingest: ${processedIds.length} item(s) — ${preview}${suffix}`;

  try {
    execSync(`bash "${join(WIKI_ROOT, "scripts", "run-git-backup.sh")}" "${message.replace(/"/g, '\\"')}"`, {
      cwd: WIKI_ROOT,
      stdio: "inherit",
    });
    console.log("ingest-worker: committed wiki changes");
  } catch {
    console.error("ingest-worker: git backup failed (wiki changes left uncommitted)");
  }
};

const processOne = async (rate: RateLimitState): Promise<ProcessOutcome> => {
  const pending = readOldestPending();
  if (!pending) {
    console.log("ingest-worker: queue empty");
    return { status: "empty" };
  }

  if (!skipRateLimit && rate.count >= MAX_PER_HOUR) {
    console.log(`ingest-worker: rate limit (${MAX_PER_HOUR}/hour) — skipping`);
    return { status: "rate_limited" };
  }

  const apiKey = process.env.CURSOR_API_KEY;
  const prompt = buildPrompt(pending.entry);

  if (dryRun) {
    console.log("ingest-worker: dry-run — would process:", pending.entry.conversation_id);
    console.log(prompt.slice(0, 500), "...");
    return { status: "done", conversationId: pending.entry.conversation_id };
  }

  if (!apiKey) {
    console.error(
      "ingest-worker: CURSOR_API_KEY not set. Add to ~/.cursor/llm-wiki/.env or shell profile.",
    );
    process.exit(1);
  }

  console.log("ingest-worker: processing", pending.entry.conversation_id);

  try {
    const result = await Agent.prompt(prompt, {
      apiKey,
      model: { id: "composer-2.5" },
      local: { cwd: WIKI_ROOT },
    });

    if (result.status === "error") {
      console.error("ingest-worker: run failed", result.id);
      appendFileSync(
        join(WIKI_ROOT, "wiki", "log.md"),
        `\n## [${new Date().toISOString().slice(0, 10)}] ingest-worker | FAILED ${pending.entry.conversation_id}\n`,
      );
      return { status: "error" };
    }

    const processedPath = join(
      PROCESSED_DIR,
      `${pending.entry.conversation_id || Date.now()}.json`,
    );
    writeFileSync(
      processedPath,
      JSON.stringify({ ...pending.entry, processed_at: new Date().toISOString(), status: "done" }, null, 2),
    );
    removeLineFromQueue(pending.line);
    saveRateLimit({ hour: rate.hour, count: rate.count + 1 });

    console.log("ingest-worker: success", pending.entry.conversation_id);
    return { status: "done", conversationId: pending.entry.conversation_id };
  } catch (err) {
    if (err instanceof CursorAgentError) {
      console.error("ingest-worker: startup failed:", err.message, "retryable=", err.isRetryable);
      return { status: "error" };
    }
    throw err;
  }
};

const main = async (): Promise<void> => {
  mkdirSync(PROCESSED_DIR, { recursive: true });

  let processed = 0;
  let rate = loadRateLimit();
  const processedIds: string[] = [];

  while (processed < maxCount) {
    const outcome = await processOne(rate);
    if (outcome.status === "empty" || outcome.status === "rate_limited") {
      break;
    }
    if (outcome.status === "error") {
      if (processedIds.length > 0) {
        commitWiki(processedIds);
      }
      process.exit(2);
    }
    if (!dryRun) {
      processed += 1;
      processedIds.push(outcome.conversationId);
    }
    rate = loadRateLimit();
  }

  commitWiki(processedIds);

  console.log(`ingest-worker: finished — processed ${processed} item(s), ${maxCount - processed} remaining in batch cap`);
};

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
