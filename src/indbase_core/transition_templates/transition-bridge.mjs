#!/usr/bin/env node
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";

function readInput() {
  const args = process.argv.slice(2);
  const requestIndex = args.indexOf("--request");
  if (requestIndex !== -1) {
    const requestPath = args[requestIndex + 1];
    return JSON.parse(readFileSync(requestPath, "utf8"));
  }
  return JSON.parse(readFileSync(0, "utf8"));
}

function sha256(value) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function normalizeOutsideProtected(markdown, spans) {
  if (!Array.isArray(spans) || spans.length === 0) {
    return markdown;
  }
  const ordered = [...spans].sort((a, b) => a.start - b.start);
  let cursor = 0;
  let output = "";
  for (const span of ordered) {
    const start = Number(span.start);
    const end = Number(span.end);
    const middle = markdown.slice(cursor, start);
    output += middle.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n");
    output += markdown.slice(start, end);
    cursor = end;
  }
  const tail = markdown.slice(cursor);
  output += tail.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n");
  return output;
}

function main() {
  const request = readInput();
  const cacheDir = request.cache_dir || ".";
  mkdirSync(cacheDir, { recursive: true });
  const normalized = normalizeOutsideProtected(request.markdown || "", request.protected_spans || []);
  const responsePathArgIndex = process.argv.indexOf("--response");
  const targets = Array.isArray(request.targets) ? request.targets : [];
  const targetResults = targets.map((target) => ({
    format: target.format,
    status: "skipped",
    error: "render target not available in scaffold bridge",
  }));
  const response = {
    contract_version: "1",
    status: targetResults.length ? "partial" : "succeeded",
    normalized_markdown: normalized,
    normalized_hash: sha256(normalized),
    targets: targetResults,
    evidence_paths: {
      manifest_path: resolve(cacheDir, "transition_manifest.json"),
      trace_path: resolve(cacheDir, "transition_trace.jsonl"),
      report_path: resolve(cacheDir, "transition_report.json"),
      config_path: resolve(cacheDir, "config.snapshot.json"),
    },
    errors: [],
  };
  writeFileSync(response.evidence_paths.manifest_path, JSON.stringify({ bridge: "scaffold" }, null, 2));
  writeFileSync(response.evidence_paths.trace_path, '{"event":"scaffold_bridge"}\n');
  writeFileSync(response.evidence_paths.report_path, JSON.stringify({ status: response.status }, null, 2));
  const payload = JSON.stringify(response);
  if (responsePathArgIndex !== -1) {
    const responsePath = process.argv[responsePathArgIndex + 1];
    mkdirSync(dirname(responsePath), { recursive: true });
    writeFileSync(responsePath, payload, "utf8");
  } else {
    process.stdout.write(payload);
  }
}

main();
