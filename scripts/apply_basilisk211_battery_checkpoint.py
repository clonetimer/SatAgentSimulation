#!/usr/bin/env python3
"""Apply the *exact* Basilisk v2.11.0 SimpleBattery native state API.

This is a source patch, NOT a monkeypatch for installed bsk wheels. The
official bsk==2.11.0 wheel has no new interface. The source MUST be rebuilt
and the resulting SWIG Python bindings separately verified before any
runtime integration. This project must never substitute Python dynamics.

Usage:
    python scripts/apply_basilisk211_battery_checkpoint.py /path/to/basilisk
    python scripts/apply_basilisk211_battery_checkpoint.py /path/to/basilisk --check

Rejects source drift, dirty/modified files, incompatible versions and
duplicate applications; never silently edits an unrelated Basilisk version.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

UPSTREAM_COMMIT = "6c1511f5385dccaa73c9ae24522a74adc24fe623"
HEADER = "src/simulation/power/simpleBattery/simpleBattery.h"
SOURCE = "src/simulation/power/simpleBattery/simpleBattery.cpp"
PATCH_MARKER = "R9J_SIMPLE_BATTERY_CPP_CHECKPOINT_V1"

CHECKPOINT_DEFINITION = """
/* R9J_SIMPLE_BATTERY_CPP_CHECKPOINT_V1
 * Native C++ state transfer for a pinned Basilisk 2.11.0 source rebuild.
 * This is a *partial* power-owner checkpoint; node message histories,
 * input queue topology, scheduler and other subsystem owners remain missing.
 */
struct SimpleBatteryCheckpointState {
    int schemaVersion = 1;
    uint64_t captureTimeNs = 0;
    double storageCapacityJ = 0.0;
    double storedChargeJ = 0.0;
    double previousTimeS = 0.0;
    double currentTimestepS = 0.0;
    double currentPowerSumW = 0.0;
    double faultCapacityRatio = 1.0;
    double outputStorageCapacityJ = 0.0;
    double outputStorageLevelJ = 0.0;
    double outputNetPowerW = 0.0;
};
"""

DECLARATIONS = """
    /* R9-J: native source-rebuild-only, partial battery checkpoint API. */
    SimpleBatteryCheckpointState exportCheckpointState(uint64_t captureTimeNs) const;
    void importCheckpointState(const SimpleBatteryCheckpointState &snapshot,
                               uint64_t targetTimeNs);
"""

CPP_METHODS = r"""
/* R9J_SIMPLE_BATTERY_CPP_CHECKPOINT_V1
 * Export/import protected native state without invoking Reset(): Reset()
 * sets previousTime to 0 and cannot represent a checkpoint at t > 0.
 * Import validates every field BEFORE mutating any internal state.
 * It publishes one output message; no prior message queue is restored.
 */
SimpleBatteryCheckpointState SimpleBattery::exportCheckpointState(uint64_t captureTimeNs) const
{
    SimpleBatteryCheckpointState s;
    s.captureTimeNs = captureTimeNs;
    s.storageCapacityJ = this->storageCapacity;
    s.storedChargeJ = this->storedCharge;
    s.previousTimeS = this->previousTime;
    s.currentTimestepS = this->currentTimestep;
    s.currentPowerSumW = this->currentPowerSum;
    s.faultCapacityRatio = this->faultCapacityRatio;
    s.outputStorageCapacityJ = this->storageStatusMsg.storageCapacity;
    s.outputStorageLevelJ = this->storageStatusMsg.storageLevel;
    s.outputNetPowerW = this->storageStatusMsg.currentNetPower;
    if (!this->validCheckpointState(s, captureTimeNs)) {
        throw std::invalid_argument("R9J_EXPORT_NATIVE_BATTERY_INVALID");
    }
    return s;
}

bool SimpleBattery::validCheckpointState(const SimpleBatteryCheckpointState &s,
                                         uint64_t targetTimeNs) const
{
    if (s.schemaVersion != 1 || targetTimeNs == 0 ||
        s.captureTimeNs != targetTimeNs) {
        return false;
    }
    const double values[] = {
        s.storageCapacityJ, s.storedChargeJ, s.previousTimeS,
        s.currentTimestepS, s.currentPowerSumW, s.faultCapacityRatio,
        s.outputStorageCapacityJ, s.outputStorageLevelJ,
        s.outputNetPowerW, this->storageCapacity
    };
    for (double value : values) {
        if (!std::isfinite(value)) { return false; }
    }
    if (s.storageCapacityJ <= 0.0 ||
        s.storageCapacityJ != this->storageCapacity ||
        s.faultCapacityRatio < 0.0 || s.faultCapacityRatio > 1.0 ||
        s.storedChargeJ < 0.0 ||
        s.storedChargeJ > s.storageCapacityJ * s.faultCapacityRatio + 1e-8 ||
        s.currentTimestepS < 0.0 || s.previousTimeS < 0.0 ||
        s.previousTimeS > static_cast<double>(targetTimeNs) * 1.0e-9 + 1e-9 ||
        s.currentTimestepS > s.previousTimeS + 1e-9 ||
        s.outputStorageCapacityJ != s.storageCapacityJ ||
        std::abs(s.outputStorageLevelJ - s.storedChargeJ) > 1e-7 ||
        std::abs(s.outputNetPowerW - s.currentPowerSumW) > 1e-7) {
        return false;
    }
    return true;
}

void SimpleBattery::importCheckpointState(const SimpleBatteryCheckpointState &s,
                                          uint64_t targetTimeNs)
{
    if (!this->validCheckpointState(s, targetTimeNs)) {
        throw std::invalid_argument("R9J_IMPORT_NATIVE_BATTERY_INVALID");
    }
    // Do NOT call Reset(), which destroys the captured integrator phase.
    this->storedCharge = s.storedChargeJ;
    this->previousTime = s.previousTimeS;
    this->currentTimestep = s.currentTimestepS;
    this->currentPowerSum = s.currentPowerSumW;
    this->faultCapacityRatio = s.faultCapacityRatio;
    this->storageStatusMsg.storageCapacity = s.outputStorageCapacityJ;
    this->storageStatusMsg.storageLevel = s.outputStorageLevelJ;
    this->storageStatusMsg.currentNetPower = s.outputNetPowerW;
    // Publish a *single* snapshot at the restored native clock. This is
    // NOT a reproduction of power-node message or event histories.
    this->writeMessages(targetTimeNs);
}
"""

def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        check=False,
    )
    if proc.returncode:
        raise RuntimeError("R9J_UPSTREAM_GIT_INVALID: " + proc.stderr.strip())
    return proc.stdout.strip()


def _substitute_once(content: str, target: str, replacement: str, where: str) -> str:
    if content.count(target) != 1:
        raise RuntimeError("R9J_VERSION_SOURCE_DRIFT:" + where)
    return content.replace(target, replacement, 1)


def prepared_patch(root: Path) -> dict[Path, str]:
    if not (root / ".git").exists():
        raise RuntimeError("R9J_REQUIRES_REAL_UPSTREAM_GIT_CHECKOUT")
    if _git(root, "rev-parse", "HEAD") != UPSTREAM_COMMIT:
        raise RuntimeError("R9J_UPSTREAM_VERSION_NOT_2_11_0")
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("R9J_UPSTREAM_CHECKOUT_MUST_BE_CLEAN")
    header, cpp = root / HEADER, root / SOURCE
    old_h, old_c = header.read_text(), cpp.read_text()
    if PATCH_MARKER in old_h or PATCH_MARKER in old_c:
        raise RuntimeError("R9J_DUPLICATE_NATIVE_PATCH")

    header_text = _substitute_once(
        old_h, "/*! @brief simple battery class */",
        CHECKPOINT_DEFINITION + "\n/*! @brief simple battery class */", HEADER,
    )
    header_text = _substitute_once(
        header_text,
        "    void readInputMessage();",
        "    void readInputMessage();\n" + DECLARATIONS, HEADER,
    )
    header_text = _substitute_once(
        header_text, "\nprivate:\n    void customReset",
        "\nprivate:\n    bool validCheckpointState(const SimpleBatteryCheckpointState &snapshot,\n"
        "                              uint64_t targetTimeNs) const;\n"
        "    void customReset", HEADER,
    )
    cpp_text = _substitute_once(
        old_c, '#include "simpleBattery.h"',
        '#include "simpleBattery.h"\n#include <cmath>\n#include <stdexcept>', SOURCE,
    )
    cpp_text = _substitute_once(
        cpp_text, "/*! custom reset function.", CPP_METHODS + "\n/*! custom reset function.", SOURCE,
    )
    return {header: header_text, cpp: cpp_text}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream_root", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.upstream_root.resolve()
    patched = prepared_patch(root)
    if args.check:
        print("R9J_PATCH_CONTRACT_OK: exact upstream source and target anchors")
        return 0
    for path, contents in patched.items():
        path.write_text(contents)
    print("R9J_PATCH_APPLIED: real Basilisk 2.11.0 C++ sources")
    print("R9J_REBUILD_REQUIRED: installed bsk wheel remains unchanged")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
