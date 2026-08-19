"""Visualization utilities for simulation results.

This module provides functions to visualize simulation results including:
- Battery state of charge (SOC) plots
- Thermal temperature plots
- Power profile plots
- Combined multi-variable plots

All functions return matplotlib figures that can be displayed or saved.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional, Tuple, Sequence
from dataclasses import is_dataclass, asdict

try:
    import matplotlib.pyplot as plt
    from matplotlib.figure import Figure
    HAS_MATPLOTLIB = True
except ImportError:
    plt = None
    Figure = object
    HAS_MATPLOTLIB = False

import numpy as np


def _ensure_matplotlib() -> None:
    if not HAS_MATPLOTLIB:
        raise ImportError("matplotlib is required for visualization. Install with: pip install matplotlib")


def plot_battery_profile(
    time_s: Sequence[float],
    soc: Sequence[float],
    storage_wh: Optional[Sequence[float]] = None,
    shunt_wh: Optional[Sequence[float]] = None,
    title: str = "Battery Profile",
    show: bool = False,
) -> Figure:
    """Plot battery simulation results.
    
    Args:
        time_s: Time array in seconds
        soc: State of charge array (0-1)
        storage_wh: Optional storage energy in watt-hours
        shunt_wh: Optional shunt dissipated energy in watt-hours
        title: Plot title
        show: Whether to display the plot
    
    Returns:
        matplotlib Figure object
    """
    _ensure_matplotlib()
    
    time_h = np.array(time_s) / 3600
    
    fig, axes = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    
    axes[0].plot(time_h, soc, label='SOC', color='#1f77b4')
    axes[0].set_ylabel('State of Charge')
    axes[0].set_ylim(-0.05, 1.05)
    axes[0].grid(True, alpha=0.3)
    axes[0].legend()
    
    if storage_wh is not None:
        axes[1].plot(time_h, storage_wh, label='Storage (Wh)', color='#ff7f0e')
    
    if shunt_wh is not None:
        axes[1].plot(time_h, shunt_wh, label='Shunt (Wh)', color='#2ca02c')
    
    axes[1].set_xlabel('Time (hours)')
    axes[1].set_ylabel('Energy (Wh)')
    axes[1].grid(True, alpha=0.3)
    axes[1].legend()
    
    fig.suptitle(title, fontsize=14)
    
    if show:
        plt.show()
    
    return fig


def plot_thermal_profile(
    time_s: Sequence[float],
    temp_k: Sequence[float],
    min_temp_k: Optional[float] = None,
    max_temp_k: Optional[float] = None,
    title: str = "Thermal Profile",
    show: bool = False,
) -> Figure:
    """Plot thermal simulation results.
    
    Args:
        time_s: Time array in seconds
        temp_k: Temperature array in Kelvin
        min_temp_k: Optional minimum temperature threshold
        max_temp_k: Optional maximum temperature threshold
        title: Plot title
        show: Whether to display the plot
    
    Returns:
        matplotlib Figure object
    """
    _ensure_matplotlib()
    
    time_h = np.array(time_s) / 3600
    temp_c = np.array(temp_k) - 273.15
    
    fig, ax = plt.subplots(figsize=(10, 5))
    
    ax.plot(time_h, temp_c, label='Temperature', color='#1f77b4')
    
    if min_temp_k is not None:
        ax.axhline(min_temp_k - 273.15, color='#d62728', linestyle='--', label='Min Temp')
    
    if max_temp_k is not None:
        ax.axhline(max_temp_k - 273.15, color='#ff7f0e', linestyle='--', label='Max Temp')
    
    ax.set_xlabel('Time (hours)')
    ax.set_ylabel('Temperature (°C)')
    ax.grid(True, alpha=0.3)
    ax.legend()
    ax.set_title(title)
    
    if show:
        plt.show()
    
    return fig


def plot_power_profile(
    time_s: Sequence[float],
    power_w: Sequence[float],
    title: str = "Power Profile",
    show: bool = False,
) -> Figure:
    """Plot power profile over time.
    
    Args:
        time_s: Time array in seconds
        power_w: Power array in watts
        title: Plot title
        show: Whether to display the plot
    
    Returns:
        matplotlib Figure object
    """
    _ensure_matplotlib()
    
    time_h = np.array(time_s) / 3600
    power = np.array(power_w)
    
    fig, ax = plt.subplots(figsize=(10, 5))
    
    ax.fill_between(time_h, power, 0, where=power >= 0, color='#2ca02c', alpha=0.3)
    ax.fill_between(time_h, power, 0, where=power < 0, color='#d62728', alpha=0.3)
    ax.plot(time_h, power, color='#1f77b4')
    
    ax.set_xlabel('Time (hours)')
    ax.set_ylabel('Power (W)')
    ax.grid(True, alpha=0.3)
    ax.set_title(title)
    
    if show:
        plt.show()
    
    return fig


def plot_multi_node_temperature(
    time_s: Sequence[float],
    temperatures: Dict[str, Sequence[float]],
    title: str = "Multi-Node Temperature Profile",
    show: bool = False,
) -> Figure:
    """Plot temperatures for multiple thermal nodes.
    
    Args:
        time_s: Time array in seconds
        temperatures: Dictionary mapping node names to temperature arrays (K)
        title: Plot title
        show: Whether to display the plot
    
    Returns:
        matplotlib Figure object
    """
    _ensure_matplotlib()
    
    time_h = np.array(time_s) / 3600
    
    fig, ax = plt.subplots(figsize=(12, 6))
    
    colors = plt.cm.tab10(np.linspace(0, 1, len(temperatures)))
    
    for i, (node_name, temps) in enumerate(temperatures.items()):
        temp_c = np.array(temps) - 273.15
        ax.plot(time_h, temp_c, label=node_name, color=colors[i])
    
    ax.set_xlabel('Time (hours)')
    ax.set_ylabel('Temperature (°C)')
    ax.grid(True, alpha=0.3)
    ax.legend()
    ax.set_title(title)
    
    if show:
        plt.show()
    
    return fig


def plot_combined_power_thermal(
    time_s: Sequence[float],
    power_w: Sequence[float],
    temp_k: Sequence[float],
    soc: Optional[Sequence[float]] = None,
    title: str = "Combined Power and Thermal",
    show: bool = False,
) -> Figure:
    """Plot combined power, temperature, and SOC.
    
    Args:
        time_s: Time array in seconds
        power_w: Power array in watts
        temp_k: Temperature array in Kelvin
        soc: Optional SOC array (0-1)
        title: Plot title
        show: Whether to display the plot
    
    Returns:
        matplotlib Figure object
    """
    _ensure_matplotlib()
    
    time_h = np.array(time_s) / 3600
    temp_c = np.array(temp_k) - 273.15
    power = np.array(power_w)
    
    num_plots = 2 if soc is None else 3
    fig, axes = plt.subplots(num_plots, 1, figsize=(10, 4 * num_plots), sharex=True)
    
    axes[0].fill_between(time_h, power, 0, where=power >= 0, color='#2ca02c', alpha=0.3)
    axes[0].fill_between(time_h, power, 0, where=power < 0, color='#d62728', alpha=0.3)
    axes[0].plot(time_h, power, color='#1f77b4')
    axes[0].set_ylabel('Power (W)')
    axes[0].grid(True, alpha=0.3)
    axes[0].set_title('Power')
    
    axes[1].plot(time_h, temp_c, color='#ff7f0e')
    axes[1].set_ylabel('Temperature (°C)')
    axes[1].grid(True, alpha=0.3)
    axes[1].set_title('Temperature')
    
    if soc is not None:
        axes[2].plot(time_h, soc, color='#1f77b4')
        axes[2].set_ylabel('SOC')
        axes[2].set_ylim(-0.05, 1.05)
        axes[2].grid(True, alpha=0.3)
        axes[2].set_title('State of Charge')
    
    axes[-1].set_xlabel('Time (hours)')
    
    fig.suptitle(title, fontsize=14)
    
    if show:
        plt.show()
    
    return fig


def plot_eclipse_cycle(
    time_s: Sequence[float],
    shadow: Sequence[float],
    solar_power: Sequence[float],
    title: str = "Eclipse Cycle",
    show: bool = False,
) -> Figure:
    """Plot eclipse cycle with shadow factor and solar power.
    
    Args:
        time_s: Time array in seconds
        shadow: Shadow factor array (0-1)
        solar_power: Solar power array in watts
        title: Plot title
        show: Whether to display the plot
    
    Returns:
        matplotlib Figure object
    """
    _ensure_matplotlib()
    
    time_h = np.array(time_s) / 3600
    
    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    
    axes[0].step(time_h, shadow, where='post', label='Shadow Factor', color='#7f7f7f')
    axes[0].set_ylabel('Shadow Factor')
    axes[0].set_ylim(-0.1, 1.1)
    axes[0].grid(True, alpha=0.3)
    axes[0].legend()
    axes[0].set_title('Shadow')
    
    axes[1].plot(time_h, solar_power, label='Solar Power', color='#ffd700')
    axes[1].set_xlabel('Time (hours)')
    axes[1].set_ylabel('Power (W)')
    axes[1].grid(True, alpha=0.3)
    axes[1].legend()
    axes[1].set_title('Solar Power')
    
    fig.suptitle(title, fontsize=14)
    
    if show:
        plt.show()
    
    return fig


def save_figure(fig: Figure, filename: str, dpi: int = 100) -> None:
    """Save a matplotlib figure to file.
    
    Args:
        fig: matplotlib Figure object
        filename: Output filename (supports .png, .pdf, .svg, etc.)
        dpi: Resolution in dots per inch
    """
    _ensure_matplotlib()
    fig.savefig(filename, dpi=dpi, bbox_inches='tight')
    plt.close(fig)


def create_summary_dashboard(
    results: Dict[str, Any],
    output_file: Optional[str] = None,
    show: bool = False,
) -> Figure:
    """Create a comprehensive summary dashboard from simulation results.
    
    Args:
        results: Dictionary containing simulation results
        output_file: Optional output filename
        show: Whether to display the dashboard
    
    Returns:
        matplotlib Figure object
    """
    _ensure_matplotlib()
    
    fig = plt.figure(figsize=(16, 12))
    gs = fig.add_gridspec(3, 3)
    
    plot_count = 0
    
    if 'time_s' in results and 'soc' in results:
        ax = fig.add_subplot(gs[0, :2])
        time_h = np.array(results['time_s']) / 3600
        ax.plot(time_h, results['soc'], label='SOC', color='#1f77b4')
        ax.set_ylabel('SOC')
        ax.set_ylim(-0.05, 1.05)
        ax.grid(True, alpha=0.3)
        ax.legend()
        ax.set_title('Battery SOC')
        plot_count += 1
    
    if 'time_s' in results and 'power_w' in results:
        ax = fig.add_subplot(gs[1, :2])
        time_h = np.array(results['time_s']) / 3600
        power = np.array(results['power_w'])
        ax.fill_between(time_h, power, 0, where=power >= 0, color='#2ca02c', alpha=0.3)
        ax.fill_between(time_h, power, 0, where=power < 0, color='#d62728', alpha=0.3)
        ax.plot(time_h, power, color='#1f77b4')
        ax.set_ylabel('Power (W)')
        ax.grid(True, alpha=0.3)
        ax.set_title('Power Profile')
        plot_count += 1
    
    if 'time_s' in results and 'temp_k' in results:
        ax = fig.add_subplot(gs[2, :2])
        time_h = np.array(results['time_s']) / 3600
        temp_c = np.array(results['temp_k']) - 273.15
        ax.plot(time_h, temp_c, color='#ff7f0e')
        ax.set_xlabel('Time (hours)')
        ax.set_ylabel('Temperature (°C)')
        ax.grid(True, alpha=0.3)
        ax.set_title('Temperature')
        plot_count += 1
    
    ax_summary = fig.add_subplot(gs[:, 2])
    summary_items = []
    if 'soc' in results:
        summary_items.append(('Min SOC', f"{min(results['soc']):.3f}"))
        summary_items.append(('Max SOC', f"{max(results['soc']):.3f}"))
        summary_items.append(('Final SOC', f"{results['soc'][-1]:.3f}"))
    if 'power_w' in results:
        avg_power = np.mean(results['power_w'])
        summary_items.append(('Avg Power', f"{avg_power:.2f} W"))
    if 'temp_k' in results:
        temp_c = np.array(results['temp_k']) - 273.15
        summary_items.append(('Min Temp', f"{min(temp_c):.1f} °C"))
        summary_items.append(('Max Temp', f"{max(temp_c):.1f} °C"))
    
    y_pos = np.arange(len(summary_items))
    ax_summary.barh(y_pos, [1] * len(summary_items), color='#1f77b4')
    for i, (label, value) in enumerate(summary_items):
        ax_summary.text(0.02, i, f"{label}: {value}", va='center', fontsize=10)
    
    ax_summary.set_xlim(0, 1)
    ax_summary.set_yticks([])
    ax_summary.set_xlabel('')
    ax_summary.set_title('Summary')
    ax_summary.spines['right'].set_visible(False)
    ax_summary.spines['top'].set_visible(False)
    ax_summary.spines['bottom'].set_visible(False)
    
    fig.suptitle('Simulation Summary Dashboard', fontsize=16)
    
    if output_file:
        save_figure(fig, output_file)
    
    if show:
        plt.show()
    
    return fig


def has_matplotlib() -> bool:
    """Check if matplotlib is available."""
    return HAS_MATPLOTLIB
