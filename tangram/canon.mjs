// Usage: node canon.mjs '<json array of states>'  -> prints validity and canonical-outline hash per state
import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
const { tangramAdapter, TANGRAM_CONFIG } = await import(pathToFileURL("D:/projects/TDS/tangram/board/adapter.js").href);
const states = JSON.parse(process.argv[2]);
for (const st of states) {
  const state = tangramAdapter.decodeCandidate(new TextEncoder().encode(JSON.stringify(st)), TANGRAM_CONFIG);
  const v = tangramAdapter.validate(state, TANGRAM_CONFIG);
  if (v.invalid) { console.log("INVALID", JSON.stringify(v.errors), JSON.stringify(st)); continue; }
  const canon = tangramAdapter.canonicalize(state, TANGRAM_CONFIG);
  const h = createHash("sha256").update(JSON.stringify(canon)).digest("hex").slice(0, 16);
  console.log(h, canon.segments.length, "segments", JSON.stringify(st));
}
