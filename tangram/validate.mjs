// Usage: node validate.mjs <path-to-adapter.js> '<state json>'
import { pathToFileURL } from "node:url";

const [adapterPath, stateJson] = process.argv.slice(2);
const { tangramAdapter, TANGRAM_CONFIG } = await import(pathToFileURL(adapterPath).href);

const bytes = new TextEncoder().encode(stateJson);
const state = tangramAdapter.decodeCandidate(bytes, TANGRAM_CONFIG);
const result = tangramAdapter.validate(state, TANGRAM_CONFIG);
console.log("validate:", JSON.stringify(result));
if (!result.invalid) {
  const canon = tangramAdapter.canonicalize(state, TANGRAM_CONFIG);
  console.log("canonical:", JSON.stringify(canon).slice(0, 1500));
}
