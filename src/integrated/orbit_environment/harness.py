"""Harness and file outputs for the v4.3 orbit/environment service."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from .basilisk_impl import run_basilisk_environment_smoke
from .model import generate_environment_profile, summarize_profile
from .schemas import OrbitEnvironmentConfig, OrbitEnvironmentProfile, OrbitEnvironmentSummary


def summary_to_dict(summary: OrbitEnvironmentSummary) -> dict:
    return asdict(summary)


def run_nominal_case() -> dict:
    profile = generate_environment_profile(OrbitEnvironmentConfig())
    summary = summarize_profile(profile)
    return summary_to_dict(summary)


def write_profile_csv(profile: OrbitEnvironmentProfile, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "time_s",
            "r_x_m", "r_y_m", "r_z_m",
            "v_x_m_s", "v_y_m_s", "v_z_m_s",
            "orbit_radius_m",
            "sun_x", "sun_y", "sun_z",
            "shadow_factor",
            "mag_x_t", "mag_y_t", "mag_z_t", "mag_norm_t",
            "ground_range_m", "ground_elevation_deg", "ground_has_access",
        ])
        for s in profile.samples:
            writer.writerow([
                f"{s.time_s:.6f}",
                *[f"{x:.9f}" for x in s.r_bn_n_m],
                *[f"{x:.9f}" for x in s.v_bn_n_m_s],
                f"{s.orbit_radius_m:.9f}",
                *[f"{x:.12f}" for x in s.sun_vector_n],
                f"{s.shadow_factor:.6f}",
                *[f"{x:.15e}" for x in s.magnetic_field_n_t],
                f"{s.magnetic_field_norm_t:.15e}",
                f"{s.ground_range_m:.9f}",
                f"{s.ground_elevation_deg:.9f}",
                int(s.ground_has_access),
            ])
    return path


def plot_profile(profile: OrbitEnvironmentProfile, path: str | Path) -> Path:
    import matplotlib.pyplot as plt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    times_min = [s.time_s / 60.0 for s in profile.samples]
    radius_km = [s.orbit_radius_m / 1000.0 for s in profile.samples]
    shadow = [s.shadow_factor for s in profile.samples]
    b_ut = [s.magnetic_field_norm_t * 1e6 for s in profile.samples]
    elev = [s.ground_elevation_deg for s in profile.samples]
    access = [1 if s.ground_has_access else 0 for s in profile.samples]

    fig, axes = plt.subplots(4, 1, figsize=(10, 8), sharex=True)
    axes[0].plot(times_min, radius_km)
    axes[0].set_ylabel("Orbit radius (km)")
    axes[0].grid(True, alpha=0.3)
    axes[1].plot(times_min, shadow)
    axes[1].set_ylabel("Shadow")
    axes[1].set_ylim(-0.05, 1.05)
    axes[1].grid(True, alpha=0.3)
    axes[2].plot(times_min, b_ut)
    axes[2].set_ylabel("|B| (uT)")
    axes[2].grid(True, alpha=0.3)
    axes[3].plot(times_min, elev, label="Elevation")
    axes[3].step(times_min, [x * max(max(elev), 1.0) for x in access], where="post", label="Access flag")
    axes[3].set_xlabel("Time (min)")
    axes[3].set_ylabel("Ground access")
    axes[3].grid(True, alpha=0.3)
    axes[3].legend()
    fig.suptitle("v4.3 Orbit/Environment Service Layer")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def run_and_save_nominal_case(save_dir: str | Path) -> dict:
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    profile = generate_environment_profile(OrbitEnvironmentConfig())
    summary = summarize_profile(profile)
    csv_path = write_profile_csv(profile, save_dir / "orbit_environment_profile.csv")
    png_path = plot_profile(profile, save_dir / "orbit_environment_overview.png")
    summary_path = save_dir / "orbit_environment_summary.json"
    payload = summary_to_dict(summary)
    payload.update({"csv": str(csv_path), "plot": str(png_path)})
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def run_backend(backend: str = "python", save_dir: str | Path | None = None) -> dict:
    if backend in {"python", "reference", "synthetic"}:
        if save_dir is None:
            return run_nominal_case()
        return run_and_save_nominal_case(save_dir)
    if backend == "basilisk":
        return run_basilisk_environment_smoke()
    raise ValueError(f"unsupported orbit/environment backend: {backend}")
