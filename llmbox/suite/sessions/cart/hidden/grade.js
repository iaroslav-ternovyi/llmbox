const { execFileSync } = require("child_process");
let out = "";
try { out = execFileSync(process.execPath, ["--test", "--test-reporter=tap", "_hidden_tests/hidden.test.js"], { encoding: "utf8", env: process.env }); }
catch (e) { out = (e.stdout || "") + (e.stderr || ""); }
const results = {};
for (const m of out.matchAll(/^\s*(ok|not ok) \d+ - (.+?)(\s+# SKIP.*)?$/gm)) if (!m[3]) results[m[2].trim()] = m[1] === "ok";
console.log("RESULTS " + JSON.stringify(results));
