#!/usr/bin/env python3
"""Make the docs' package counts derive from envs/*.lock.txt instead of being retyped.

WHY THIS EXISTS. The reconciler rewrites `envs/<env>.lock.txt` every time conda-forge
drifts, but the package counts in README.md, docs/llms.txt and docs/index.html were
hand-written, and nothing reconciled them. So they rotted silently. Measured on
2026-10-08, after ~6 weeks of normal operation, eight of fifteen were wrong:

    geospatial         doc 125  lock 110
    earth-observation  doc 261  lock 246   (index.html said 236 — a third value)
    geo-ml             doc 381  lock 367
    pointcloud         doc 245  lock 234   (index.html said 287)
    comp-chem          doc 210  lock 207
    cfd-fv             doc  57  lock  56
    viz                doc 245  lock 244
    dft                doc 239  lock 236   (stale within hours of being written)

Nobody mistyped those. They were correct when written and the channel moved underneath
them, which is precisely the failure a daily reconciler creates: it keeps one artifact
current and leaves every claim about that artifact behind. The lock file is the only
thing that is regenerated from a real build, so it is the single source of truth here
and the prose is generated from it.

USAGE
    python builder/sync-doc-counts.py            # rewrite the docs in place
    python builder/sync-doc-counts.py --check    # exit 1 if any doc disagrees

`--check` is the CI guard: it makes a hand-edited count a build failure rather than a
thing someone notices months later. The writing mode runs once per publish, in a job
that depends on the whole build matrix — NOT inside the per-env lock commit, because
that matrix runs up to six envs concurrently and each leg is designed to touch only its
own lock file. Six legs editing one README is a rebase conflict, so the sync is a single
follow-up job instead.

SCOPE, deliberately narrow: this tool owns numbers that are mechanically derivable from
the locks — per-env package counts, and the count of published envs (which is written in
three places: the status line, the headline stat, and the "which project has it" router's
unit row, where it had already rotted to 16 against 20 published). It does not touch
prose, package lists, or the caveats, because those carry judgement a script cannot
regenerate and silently rewriting them would be worse than letting them age.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENVS = ROOT / "envs"

# Lock header lines that are not packages. build-env.sh writes exactly four.
LOCK_HEADER_LINES = 4


def env_counts() -> dict[str, int]:
    """{env: package count} straight from the committed locks."""
    out: dict[str, int] = {}
    for lock in sorted(ENVS.glob("*.lock.txt")):
        name = lock.name[: -len(".lock.txt")]
        # Prefer the count the builder recorded in the header; fall back to counting
        # body lines. The header is authoritative because build-env.sh derives it from
        # the same resolved set it hashed.
        text = lock.read_text()
        m = re.search(r"packages:\s*(\d+)", text)
        if m:
            out[name] = int(m.group(1))
        else:
            out[name] = max(0, sum(1 for ln in text.splitlines() if ln.strip())
                            - LOCK_HEADER_LINES)
    return out


def published_envs() -> list[str]:
    """Env specs that are actually published, honouring `# aarchsci-unpublished:`.

    Same marker reconcile.yml uses, so there is one definition of "published" rather
    than a second list to keep in step.
    """
    names = []
    for spec in sorted(ENVS.glob("*.yaml")):
        name = spec.stem
        header = spec.read_text()
        if re.search(r"^#\s*aarchsci-unpublished:", header, re.M):
            continue
        names.append(name)
    return names


def _sub(text: str, pattern: str, repl, label: str, edits: list[str]) -> str:
    def go(m: re.Match) -> str:
        new = repl(m)
        if new != m.group(0):
            edits.append(f"{label}: {m.group(0)[:60]!r} -> {new[:60]!r}")
        return new
    return re.sub(pattern, go, text)


def sync(counts: dict[str, int], n_published: int) -> dict[Path, tuple[str, list[str]]]:
    """Return {path: (new_text, [edit descriptions])} for every doc that needs changing."""
    result: dict[Path, tuple[str, list[str]]] = {}

    # --- README.md: table rows `| [`env`](envs/env.yaml) | NNN | ... |`
    p = ROOT / "README.md"
    s = orig = p.read_text()
    edits: list[str] = []
    s = _sub(s, r"\| \[`([a-z0-9-]+)`\]\(envs/\1\.yaml\) \| (\d+) \|",
             lambda m: (f"| [`{m.group(1)}`](envs/{m.group(1)}.yaml) | "
                        f"{counts.get(m.group(1), int(m.group(2)))} |"),
             "README row", edits)
    # `**N verified, signed, public env images**`
    s = _sub(s, r"\*\*(\d+) verified, signed, public env images\*\*",
             lambda m: f"**{n_published} verified, signed, public env images**",
             "README status", edits)
    # The "which project has it" router's unit row: `a curated multi-package env (N)`.
    # Same number, written in a second place and already rotted once — it said 16 while
    # 20 envs were published. Appears in README.md and docs/index.html both.
    s = _sub(s, r"a curated multi-package env \((\d+)\)",
             lambda m: f"a curated multi-package env ({n_published})",
             "README router", edits)
    if s != orig:
        result[p] = (s, edits)

    # --- docs/llms.txt: `  - `env` (NNN pkgs` and `- N env images (`
    p = ROOT / "docs" / "llms.txt"
    s = orig = p.read_text()
    edits = []
    s = _sub(s, r"- `([a-z0-9-]+)` \((\d+) pkgs",
             lambda m: f"- `{m.group(1)}` ({counts.get(m.group(1), int(m.group(2)))} pkgs",
             "llms entry", edits)
    s = _sub(s, r"- (\d+) env images \(",
             lambda m: f"- {n_published} env images (",
             "llms facts", edits)
    if s != orig:
        result[p] = (s, edits)

    # --- docs/index.html: `>env</span><br>` then `<strong>NNN packages</strong>`,
    #     plus the headline stat `<div class="stat-num green">N</div>`
    p = ROOT / "docs" / "index.html"
    s = orig = p.read_text()
    edits = []
    s = _sub(s, r">([a-z0-9-]+)</span><br>(\s*)<strong>(\d+) packages</strong>",
             lambda m: (f">{m.group(1)}</span><br>{m.group(2)}"
                        f"<strong>{counts.get(m.group(1), int(m.group(3)))} packages</strong>"),
             "index card", edits)
    s = _sub(s, r'<div class="stat-num green">(\d+)</div>',
             lambda m: f'<div class="stat-num green">{n_published}</div>',
             "index stat", edits)
    s = _sub(s, r"a curated multi-package env \((\d+)\)",
             lambda m: f"a curated multi-package env ({n_published})",
             "index router", edits)
    if s != orig:
        result[p] = (s, edits)

    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 if any doc count disagrees with the locks")
    args = ap.parse_args()

    counts = env_counts()
    pub = published_envs()
    if not counts:
        print("sync-doc-counts: no envs/*.lock.txt found — refusing to rewrite docs "
              "from an empty source of truth", file=sys.stderr)
        return 2

    pending = sync(counts, len(pub))

    print(f"sync-doc-counts: {len(counts)} locks, {len(pub)} published envs "
          f"({', '.join(pub)})")
    if not pending:
        print("sync-doc-counts: docs already match the locks.")
        return 0

    for path, (_text, edits) in pending.items():
        rel = path.relative_to(ROOT)
        for e in edits:
            print(f"  {rel}: {e}")

    if args.check:
        print(f"\nsync-doc-counts: FAIL — {sum(len(e) for _, e in pending.values())} "
              f"count(s) in {len(pending)} file(s) disagree with envs/*.lock.txt.\n"
              f"Run `python builder/sync-doc-counts.py` to fix.", file=sys.stderr)
        return 1

    for path, (text, _edits) in pending.items():
        path.write_text(text)
    print(f"\nsync-doc-counts: updated {len(pending)} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
