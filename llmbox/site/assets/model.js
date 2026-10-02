// a model's page: speed and fit predicted for the visitor's box
(function () {   // the visitor's box (picked on the home page): speed and fit predicted for it
  const box = savedBox(DATA);
  if (!box || !DATA.sh || !DATA.sh.layers || sameClassAs(box, DATA.ref)) return;
  const f = forBox(DATA.sh, box), sp = document.querySelector("#vspd"), ft = document.querySelector("#vfit"), yb = document.querySelector("#yourbox");
  if (f.fits) {
    sp.querySelector("b").innerHTML = `~${Math.round(f.t2)}<small> tok/s</small>`;
    sp.querySelector(".sub").textContent = `short chat · ~${Math.round(f.td)} at 32k of context`;
    ft.querySelector("b").textContent = `✓ ${Math.round(f.ctx / 1024)}k`;
    yb.innerHTML = `On your box (${boxLabel(box)}): <b>~${Math.round(f.t2)} tok/s</b> in a short chat, <b>~${Math.round(f.td)}</b> at 32k of context, up to ${Math.round(f.ctx / 1024)}k context. Predicted; the bars below are measured on our test PC.`;
  } else {
    sp.querySelector("b").textContent = "—"; sp.querySelector(".sub").textContent = "does not fit on your box";
    ft.querySelector("b").innerHTML = '<span class="red">✗ too big</span>';
    yb.innerHTML = `On your box (${boxLabel(box)}) this model does not fit, even with a smaller context. The bars below are our test PC.`;
  }
  sp.querySelector(".src").textContent = "predicted for your box"; ft.querySelector(".sub").textContent = "context on your box";
  ft.querySelector(".src").textContent = boxLabel(box); yb.hidden = false;
})();
