#!/usr/bin/env python3
# apptainer.smoke.py — the D3 verification for the `apptainer` env.
#
# Same contract as every other env (assemble + do real work, inside the built arm64
# image), but this env has no Python packages to import, so "assemble" means something
# different: the artifact under test is a native aarch64 container runtime, and the real
# work is BUILDING A CONTAINER IMAGE AND READING IT BACK.
#
# What the test does, all as the image's unprivileged user and with no container
# privileges of any kind: check the runtime's content-addressed identity against the
# sha256 pinned in envs/apptainer.yaml, confirm it is the non-suid build conda-forge
# claims to ship, then pack a directory into a real SIF, verify the container format
# recorded an arm64 squashfs payload, extract that payload back out and compare the
# bytes. If the SIF round-trips, squashfs generation and the SIF writer work native on
# aarch64 — which is the thing a version string cannot tell you.
#
# ONE THING THIS TEST DELIBERATELY DOES NOT CLAIM: that `apptainer exec` works. Running
# a SIF needs a user namespace, and Docker's default seccomp profile denies
# CLONE_NEWUSER, so the builder cannot exercise it. The test probes it anyway and
# reports the outcome as a NOTE rather than a pass or a fail, because pretending either
# way would be exactly the unearned "verified" D3 exists to prevent. Verifying exec is
# the Graviton-host leg of issue #6, step 2. Pure stdlib. Exit 0 = functionally sound.
import glob
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

FAILURES = []
NOTES = []

PREFIX = Path(sys.prefix)
APPTAINER = PREFIX / "bin" / "apptainer"

# The pin from envs/apptainer.yaml. Asserted, not trusted — see section 1.
PIN_VERSION = "1.5.3"
PIN_BUILD = "h990128b_0"
PIN_SHA256 = "6f901be0d24192f8639805b9d6d21adb584ae67a49fe83245a80743e092cf148"
PIN_SUBDIR = "linux-aarch64"

EM_AARCH64 = 0xB7
MARKER = b"aarchsci-sif-round-trip-ok\n"


def check(name):
    def wrap(fn):
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as exc:  # noqa: BLE001
            FAILURES.append((name, exc))
            print(f"  FAIL {name}: {exc!r}")
            traceback.print_exc()
        return fn
    return wrap


def run(*args, **kw):
    """Run a command, returning CompletedProcess with text output captured."""
    return subprocess.run(args, capture_output=True, text=True, **kw)


def apptainer_env():
    """Env for apptainer as an unprivileged user: give it writable tmp/cache dirs.

    Without these it falls back to $HOME, which in this image is not guaranteed
    writable — and a permission error there would look like a runtime defect.
    """
    env = dict(os.environ)
    tmp = Path(tempfile.gettempdir()) / f"aarchsci-appt-{os.getuid()}"
    (tmp / "cache").mkdir(parents=True, exist_ok=True)
    env["APPTAINER_TMPDIR"] = str(tmp)
    env["APPTAINER_CACHEDIR"] = str(tmp / "cache")
    return env


print("[smoke] 1. runtime identity and provenance")


@check("apptainer binary exists and is a native aarch64 ELF")
def _bin():
    assert APPTAINER.is_file(), f"{APPTAINER} missing"
    with APPTAINER.open("rb") as fh:
        head = fh.read(20)
    assert head[:4] == b"\x7fELF", f"not an ELF: {head[:4]!r}"
    machine = struct.unpack_from("<H", head, 18)[0]
    assert machine == EM_AARCH64, (
        f"e_machine 0x{machine:x}, expected 0x{EM_AARCH64:x} (AArch64) — this is an "
        "arm64 image, so a non-aarch64 runtime here means emulation crept in")


@check(f"apptainer reports version {PIN_VERSION}")
def _version():
    cp = run(str(APPTAINER), "--version")
    assert cp.returncode == 0, f"`apptainer --version` exited {cp.returncode}: {cp.stderr}"
    out = cp.stdout.strip()
    assert PIN_VERSION in out, f"expected {PIN_VERSION}, got {out!r}"
    print(f"       ({out})")


@check("installed package matches the sha256 pinned in envs/apptainer.yaml")
def _sha():
    # This is the one check that makes the yaml's sha256 comment a fact rather than a
    # decoration: conda records the package's own content hash in conda-meta, so the
    # exact artifact that was installed can be compared against the pin.
    metas = glob.glob(str(PREFIX / "conda-meta" / "apptainer-*.json"))
    assert len(metas) == 1, f"expected exactly one apptainer conda-meta record, found {metas}"
    meta = json.loads(Path(metas[0]).read_text())
    assert meta["version"] == PIN_VERSION, f"version {meta['version']} != {PIN_VERSION}"
    assert meta["build"] == PIN_BUILD, f"build {meta['build']} != {PIN_BUILD}"
    assert meta["subdir"] == PIN_SUBDIR, f"subdir {meta['subdir']} != {PIN_SUBDIR}"
    assert meta["sha256"] == PIN_SHA256, (
        f"sha256 {meta['sha256']} != pinned {PIN_SHA256} — the channel served a "
        "different artifact under the same name; do not trust this image")
    print(f"       ({meta['subdir']}/apptainer-{meta['version']}-{meta['build']}.conda "
          f"sha256 {meta['sha256'][:16]}…)")


print("[smoke] 2. build variant: non-suid by construction")


@check("ships `starter` and NOT `starter-suid` (rootless-only, as conda-forge builds it)")
def _nosuid():
    # conda-forge configures this with `--without-suid`. That is a property of the
    # build, not of the version, which is why envs/apptainer.yaml pins the build
    # string — and why it is worth asserting here rather than assuming.
    binroot = PREFIX / "libexec" / "apptainer" / "bin"
    starter = binroot / "starter"
    suid = binroot / "starter-suid"
    assert starter.is_file(), f"{starter} missing — the runtime cannot start containers"
    assert not suid.exists(), (
        f"{suid} exists — this is NOT the non-suid build this env pins, and a setuid "
        "starter has materially different security properties on a shared machine")
    # Not asserted, only recorded: conda-forge builds this as a dynamically linked
    # Go+CGO binary rather than the static binary upstream's release tarballs ship, so
    # it depends on the env's own libseccomp/libarchive/openssl. Worth knowing when
    # copying the binary out of the image, which will not work.
    blob = APPTAINER.read_bytes()
    interp = b"/lib/ld-linux-aarch64.so.1"
    print(f"       (starter present, no starter-suid; dynamically linked: "
          f"{interp.decode() if interp in blob else 'no PT_INTERP found'})")


@check("the unprivileged-mount helpers apptainer depends on are all present")
def _helpers():
    # squashfuse is what mounts a SIF with no privilege; unsquashfs is how this test
    # reads one back without mounting at all; fuse-overlayfs backs --writable-tmpfs.
    # A dependency trim that dropped any of these would not fail the solve.
    wanted = ["squashfuse", "squashfuse_ll", "unsquashfs", "mksquashfs", "fuse-overlayfs"]
    missing = [w for w in wanted if not (PREFIX / "bin" / w).is_file()]
    assert not missing, f"missing helper binaries: {missing}"
    # `lib/cni`, NOT the `libexec/cni` an upstream install would use: conda-forge
    # carries `0003-Use-external-CNI.patch` so apptainer uses the channel's own
    # cni-plugins package instead of vendoring its own. Checked because that is a
    # packaging decision that could be revisited, and `--net` would break quietly.
    cni = PREFIX / "lib" / "cni"
    plugins = sorted(p.name for p in cni.iterdir()) if cni.is_dir() else []
    assert plugins, f"{cni} missing or empty (needed for --net)"
    for required in ("bridge", "portmap", "loopback"):
        assert required in plugins, f"cni plugin {required!r} missing from {cni}"
    netd = PREFIX / "etc" / "cni" / "net.d"
    assert netd.is_dir() and any(netd.glob("*.conflist")), \
        f"{netd} has no network configs — apptainer ships these, so their absence is a gap"
    print(f"       ({', '.join(wanted)}; {len(plugins)} cni plugins in lib/cni, "
          f"{len(list(netd.glob('*.conflist')))} net configs)")


print("[smoke] 3. functional: build a SIF and read the payload back out")

SIF = {}


@check("apptainer packs a directory into a SIF")
def _build():
    tmp = Path(tempfile.mkdtemp(prefix="aarchsci-sif-"))
    SIF["tmp"] = tmp
    rootfs = tmp / "rootfs"
    (rootfs / "aarchsci").mkdir(parents=True)
    (rootfs / "aarchsci" / "marker").write_bytes(MARKER)
    sif = tmp / "test.sif"
    cp = run(str(APPTAINER), "build", str(sif), str(rootfs), env=apptainer_env())
    assert cp.returncode == 0, (
        f"`apptainer build` exited {cp.returncode}\nstdout: {cp.stdout}\nstderr: {cp.stderr}")
    assert sif.is_file(), f"{sif} was not created despite a zero exit"
    assert sif.stat().st_size > 4096, f"SIF is implausibly small ({sif.stat().st_size} bytes)"
    SIF["path"] = sif
    print(f"       (built {sif.stat().st_size} bytes)")


@check("the SIF container format records an arm64 squashfs payload")
def _format():
    sif = SIF.get("path")
    assert sif, "skipped: no SIF was built"
    cp = run(str(APPTAINER), "sif", "list", str(sif), env=apptainer_env())
    assert cp.returncode == 0, f"`apptainer sif list` exited {cp.returncode}: {cp.stderr}"
    fs_rows = [ln for ln in cp.stdout.splitlines() if "FS " in ln]
    assert fs_rows, f"no filesystem partition in the SIF:\n{cp.stdout}"
    row = fs_rows[0]
    assert "Squashfs" in row, f"payload is not squashfs: {row.strip()}"
    assert "arm64" in row, (
        f"SIF payload architecture is not arm64: {row.strip()} — a SIF built here "
        "must be runnable on Graviton, and the architecture is baked into the header")
    print(f"       ({row.strip()})")


@check("the payload extracts back out and the bytes match what went in")
def _roundtrip():
    # The end-to-end proof, and the reason this test is functional rather than
    # cosmetic: dump the squashfs partition out of the SIF and unpack it. Note the
    # payload does NOT start at offset 0 of the SIF, so pointing unsquashfs at the SIF
    # directly reads nothing and silently succeeds — hence `sif dump` first.
    sif, tmp = SIF.get("path"), SIF.get("tmp")
    assert sif, "skipped: no SIF was built"
    payload = tmp / "payload.squashfs"
    with payload.open("wb") as fh:
        cp = subprocess.run([str(APPTAINER), "sif", "dump", "3", str(sif)],
                            stdout=fh, stderr=subprocess.PIPE, text=True,
                            env=apptainer_env())
    assert cp.returncode == 0, f"`apptainer sif dump` exited {cp.returncode}: {cp.stderr}"
    assert payload.stat().st_size > 0, "dumped payload is empty"
    out = tmp / "unpacked"
    cp = run(str(shutil.which("unsquashfs") or PREFIX / "bin" / "unsquashfs"),
             "-q", "-d", str(out), str(payload))
    assert cp.returncode == 0, f"unsquashfs exited {cp.returncode}: {cp.stderr or cp.stdout}"
    got = (out / "aarchsci" / "marker").read_bytes()
    assert got == MARKER, f"payload corrupted: {got!r} != {MARKER!r}"
    print(f"       (round-tripped {len(MARKER)} bytes through squashfs intact)")


@check("apptainer reads its own image metadata back")
def _inspect():
    sif = SIF.get("path")
    assert sif, "skipped: no SIF was built"
    cp = run(str(APPTAINER), "inspect", "--json", str(sif), env=apptainer_env())
    assert cp.returncode == 0, f"`apptainer inspect` exited {cp.returncode}: {cp.stderr}"
    json.loads(cp.stdout)  # must be parseable; content is apptainer's business


print("[smoke] 4. running a SIF: probed, and reported honestly")


def probe_exec():
    """Attempt `apptainer exec` and classify the outcome. Never asserts."""
    sif = SIF.get("path")
    if not sif:
        NOTES.append("exec not probed: no SIF was built")
        return
    cp = run(str(APPTAINER), "exec", str(sif), "/bin/true", env=apptainer_env())
    err = (cp.stderr or "") + (cp.stdout or "")
    if cp.returncode == 0:
        NOTES.append("exec WORKS here: `apptainer exec` ran a command inside the SIF")
        return
    # The rootfs this test builds is a bare directory with no /bin/true in it, so
    # "no such file or directory" for the COMMAND means the image was mounted
    # successfully — which is the interesting half of exec working.
    if re.search(r"/bin/true.*(no such file|not found)", err, re.I):
        NOTES.append("exec MOUNTED the SIF successfully (the test rootfs has no "
                     "/bin/true to run, which is expected)")
        return
    if re.search(r"user namespace|userns", err, re.I):
        NOTES.append(
            "exec NOT VERIFIED — no user namespace available. Expected inside a "
            "container: Docker's default seccomp profile denies CLONE_NEWUSER "
            "(`--security-opt seccomp=unconfined` lifts it). This is precisely the "
            "check that needs a real Graviton host; see issue #6 step 2.")
        return
    if "invalid argument" in err.lower():
        NOTES.append(
            "exec NOT VERIFIED — execve returned EINVAL from the squashfs mount. "
            "Measured artifact of nested containers on Apple-silicon Docker Desktop; "
            "`--sandbox` exec of an identical rootfs works there, so this is the "
            "host, not the package. Still needs a real host to settle.")
        return
    NOTES.append(f"exec NOT VERIFIED — unrecognised failure (rc={cp.returncode}): "
                 f"{err.strip().splitlines()[-1] if err.strip() else 'no output'}")


probe_exec()
for note in NOTES:
    print(f"  note {note}")

print("[smoke] " + "-" * 50)
if FAILURES:
    names = ", ".join(n for n, _ in FAILURES)
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): {names}")
    sys.exit(1)
print("[smoke] PASSED: apptainer assembles and builds+reads a native arm64 SIF — "
      "verified. Running a SIF is reported above, not claimed here.")
