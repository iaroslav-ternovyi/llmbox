// install.html: the copy button of the one-line installer
document.querySelectorAll("[data-copy]").forEach(b => b.addEventListener("click", () => {
  const t = document.getElementById(b.dataset.copy).textContent;
  (navigator.clipboard ? navigator.clipboard.writeText(t) : Promise.reject()).then(() => { b.textContent = "COPIED"; setTimeout(() => b.textContent = "COPY", 1500); }, () => {});
}));
