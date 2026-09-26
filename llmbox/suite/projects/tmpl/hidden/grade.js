const { execFileSync } = require("child_process");
let out = "";
try { out = execFileSync(process.execPath, ["--test", "--test-reporter=tap", "_hidden_tests/hidden.test.js"], { encoding: "utf8" }); }
catch (e) { out = (e.stdout || "") + (e.stderr || ""); }
const results = {};
for (const m of out.matchAll(/^(ok|not ok) \d+ - (.+)$/gm)) results[m[2].trim()] = m[1] === "ok";
console.log("RESULTS " + JSON.stringify(results));
