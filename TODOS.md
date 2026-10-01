# TODOS

Deferred on purpose, with the reason and what would bring each back. The launch scope is in docs/launch-checklist.md.

## After launch

- **AMD running and timing (Vulkan).** llmbox installs the Vulkan build on AMD and the site predicts AMD speeds, but
  `llmbox test` does not run and time models there yet. Deferred 2026-10-02: no AMD hardware to verify it on, and most
  AMD desktop owners use Windows, where Vulkan under WSL2 is experimental. Bring back with an AMD tester on Linux who
  can run repeated builds; say "Linux only" in the CLI on WSL2 with an AMD card.
- **Confirming "first measured here" credits.** At launch the credit goes to the first signed-in run whose card matches
  the picker. Deferred: an "unconfirmed" state until a second signed-in account in the same comparison set lands
  within 25%, a plausibility band against the prediction, and notices when a credit passes on. Bring back when the
  first fake shows up or a rare card's credit is disputed.
- **"Similar machines" on the result card.** At launch a card compares only exactly alike machines (card × RAM speed
  bucket × backend, same model file and engine). Deferred: borrowing "same card, other RAM" machines when there are
  fewer than 5. RAM speed matters for mixture-of-experts models, so a pooled percentile would rank unlike machines.
  Bring back with model-aware pooling (dense models only, or by measured RAM bandwidth).
- **A second render of the result card.** At launch a card is drawn once: at once for a speed-only run, and for a run
  with a quality test when its checks finish (or after 24 hours if the reader box is down). Deferred: drawing early and
  again later. A posted card should never show a "pending" status that later changed.
- **Public reference runs.** llama.cpp's published llama-bench runs as a labelled column on hardware pages. Deferred:
  a different method (zero context), common NVIDIA cards only, and the speed predictor already uses them.
