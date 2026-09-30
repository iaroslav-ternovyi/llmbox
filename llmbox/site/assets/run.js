// a run's page: copy the server's command line
const c = document.getElementById("copy");
if (c) c.onclick = () => { navigator.clipboard.writeText(document.getElementById("argv").innerText.trim().replace(/\s*\n\s*/g, " ")).then(() => { c.textContent = "COPIED"; setTimeout(() => c.textContent = "COPY SETTINGS", 1500); }); };
