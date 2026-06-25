# Lessons from building aarchbio — read before writing the builder

aarchbio (the bioinformatics sibling) shipped 503 signed arm64 containers, a live
site, and a self-running daily reconciler. Building it surfaced a string of
non-obvious bugs and design calls. aarch.science reuses the same machinery, so
**inherit the fixes, don't rediscover them.** Each item below cost real debugging.

## Farm / build (SSH-driven native builds on janus amd64 + orion arm64)

1. **push-by-digest needs the `docker-container` buildx driver**, not the default
   `docker` driver (`push-by-digest is currently not implemented for docker
   driver`). On each build box: `docker buildx create --name <n> --driver
   docker-container --bootstrap && docker buildx use <n>`.
2. **SSH has no default timeout** — a wedged connection during a long remote build
   hangs the whole run forever. Use
   `-o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=30`
   (~15 min). We first set CountMax=4 (~2 min) and it **killed slow multi-arch
   builds mid-install** (quiet conda solves produce no ssh traffic). 15 min is the
   sweet spot: bounds a dead connection without killing a legit slow build.
3. **`ssh` inside a `while read` loop steals the loop's stdin** → only the first
   item processes. Redirect: `ssh ... </dev/null`.
4. **janus (Rocky Linux) has no `rsync`** — ship the builder dir with tar over ssh
   (`tar -cf - . | ssh host 'tar -C /dst -xf -'`), portable everywhere.
5. **Build legs that capture a digest from a parallel subshell are race-prone.**
   Build legs sequentially with a one-retry wrapper; far more reliable for bulk.
6. **Merge/registry ops fail transiently under farm load** — wrap manifest
   merge + imagetools-tag in a 3× retry with a short sleep.
7. **Robot-token sessions expire on multi-hour runs** (401 mid-run). Re-`docker
   login` all machines periodically (every ~25 tools).

## Shell / parsing gotchas (cost real time)

8. **macOS awk lacks `xor`/`lshift`** — don't use bitwise awk; a MINSTD LCG
   (`x=16807*x % 2147483647`) is portable for deterministic pseudo-random.
9. **An `emit()` helper like `[ -n "$X" ] && echo ...` returns non-zero when X is
   empty** — if it's the script's *last* statement, the whole script exits
   non-zero on SUCCESS. End such helpers with `return 0`. (This made merges look
   "failed" while images published fine — very confusing.)
10. **`grep ... | head -n` trips SIGPIPE under `set -o pipefail`** — a cosmetic
    display line can fail the script. Guard: `{ ...| head; } || true`.
11. **f-strings with `\t`/`\n` inside `{...}` break** in a bash-quoted Python
    heredoc; and **an apostrophe in a `# comment` closes the bash `'...'` quote.**
    Keep heredoc'd Python free of both.
12. **`uv` isn't on hosted CI runners** — scripts that shell to Python must fall
    back: `if command -v uv; then PY=(uv run python); else PY=(python3); fi`.
    (Project standard is `uv run python` locally.)
13. **The quay repository API paginates (~100/page)** — follow `next_page` or you
    silently miss most repos (we "saw 100" when 502 existed).

## Provenance / signing / trust (D6 in aarchbio)

14. **Keyless cosign needs CI OIDC** — the farm has no OIDC, so it builds+pushes
    *unsigned/private*; a separate CI `sign-existing` workflow keyless-signs +
    flips public. Heavy build local, light sign in CI. Keeps truthful
    `github.com/<org>/<repo>` build provenance in the signature.
15. **quay needs TWO credentials:** a **robot token** for `docker push`, and a
    separate **OAuth app token** (`repo:admin`) for the management API
    (`changevisibility` to make repos public). The robot token gets 403/CSRF on
    the management API.
16. **Tag from what was ACTUALLY installed, not a predicted hash.** The in-container
    resolver can pick a different build than a host solve predicts; tagging from
    the prediction makes the tag misreport its contents.

## GitHub Pages / domain (for the site)

17. **Pages source path must be `/` or `/docs`** — not arbitrary folders. Put the
    site in `docs/`.
18. **Private repo + free plan = no Pages.** Public repo required.
19. **The Pages API commits CNAME changes directly to the repo** → your local
    diverges; `git pull --rebase` before pushing after any domain toggle.
20. **Stuck HTTPS cert provisioning?** Don't rapid-toggle the domain (that wedges
    it). Do a *clean* cycle: clear custom domain → wait for the no-domain build to
    settle → re-add → it provisions (`cert=approved`) on the next check.
21. **You can't `https_enforced=true` until the cert exists** (404 "certificate
    does not exist yet"). Enforce HTTPS only after provisioning completes.

## Design calls worth keeping

- **Power-law scope:** build & deeply verify the head; document the tail (GAPS.md
  with provenance categorization). Don't chase dead-ends — a skip-list with
  `wontfix` (never retry) + `stuck` (monthly revisit) tiers stops futile work.
- **Don't compile from source** — build from blessed channel packages only; gaps
  go upstream. That's what keeps "verify the build" a tractable promise.
- **State that must survive reboots lives in the repo (gitignored), not `/tmp`** —
  macOS clears `/tmp` on restart; a long resumable run lost its state to a reboot.
- **The "verified" claim is earned by the smoke test, not the tag.** For
  aarch.science this is even more central than aarchbio (D3) — pip "solved" too.
