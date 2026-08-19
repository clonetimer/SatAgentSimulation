"""Antenna builder module.

Provides both Python model config builders and Basilisk-native component factories.

Basilisk integration notes
---------------------------
Basilisk 2.11.0 provides simpleAntenna.SimpleAntenna.  This module now provides:
- a native SimpleAntenna factory for Comm/Data RF-chain validation;
- the legacy analytical pointing helper retained as an explicitly labeled project helper.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import AntennaConfig, SimpleAntennaNativeConfig

import math
from dataclasses import dataclass, replace

from .degradation import AntennaDegradation, AntennaDegradationRate
from .degradation import apply_antenna_degradation, compute_degradation_state
from .faults import apply_antenna_faults
from .faults import FaultSpec


_messaging = None
_sysModel = None

try:
    from Basilisk.architecture import messaging, sysModel
    _messaging = messaging
    _sysModel = sysModel
except ImportError as exc:
    record_runtime_diagnostic(
        code='OPTIONAL_DEPENDENCY_IMPORT_UNAVAILABLE',
        category=DiagnosticCategory.OPTIONAL_DEPENDENCY_PROBE,
        location='src/components/antenna/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )




@dataclass(frozen=True)
class AntennaResult:
    '''
    计算结果类。
    Attributes:
        antenna_gain_dbi: 实际天线增益，单位为dBBi
        pointing_loss_db: 实际指向损失，单位为dB数
        boresight_ok: 是否在天线有效指向范围内
        limit_violation: 是否违反天线有效指向范围
    '''
    antenna_gain_dbi: float
    pointing_loss_db: float
    boresight_ok: bool
    limit_violation: bool


def compute_antenna_gain(off_boresight_deg: float, config: AntennaConfig) -> AntennaResult:
    """使用二次损失模型,基于离轴角计算天线增益和指向损失.

    Args:
        off_boresight_deg: 天线指向角度，单位为度数
        config: 天线配置

    Attributes:
        antenna_gain_dbi: 实际天线增益，单位为dBBi
        pointing_loss_db: 实际指向损失，单位为dB数
        boresight_ok: 是否在天线有效指向范围内
        limit_violation: 是否违反天线有效指向范围

    Returns:
        AntennaResult: 天线增益和指向损失结果
    """
    denom = max(config.half_power_beamwidth_deg, 1e-9)
    loss = min(config.max_pointing_loss_db, 3.0 * (off_boresight_deg / denom) ** 2)
    gain = config.peak_gain_dbi - loss
    ok = loss <= 3.0
    return AntennaResult(antenna_gain_dbi=gain, pointing_loss_db=loss, boresight_ok=ok, limit_violation=not ok)


def _build_nominal_antenna_config_base_impl(
    peak_gain_dbi: float = 8.0,
    half_power_beamwidth_deg: float = 30.0,
    max_pointing_loss_db: float = 18.0,
    degradation: AntennaDegradation | None = None,
    degradation_rate: AntennaDegradationRate | None = None,
    years_elapsed: float = 0.0,
    fault_specs: list[FaultSpec] | None = None,
) -> AntennaConfig:
    """构建标称天线配置，支持退化叠加和故障应用.

    Args:
        peak_gain_dbi: 峰值增益，单位为dBBi
        half_power_beamwidth_deg: 半功率束宽，单位为度数
        max_pointing_loss_db: 最大指向损失，单位为dB数  
        degradation: 天线退化状态，可选
        degradation_rate: 天线退化速率，可选（与degradation互斥，优先使用degradation）
        years_elapsed: 经过时间（年），当使用degradation_rate时有效，默认0年
        fault_specs: 故障规范列表，可选

    Returns:
        AntennaConfig: 标称天线配置
    """
    config = AntennaConfig(
        peak_gain_dbi=peak_gain_dbi,
        half_power_beamwidth_deg=half_power_beamwidth_deg,
        max_pointing_loss_db=max_pointing_loss_db,
    )
    if degradation is not None:
        config = apply_antenna_degradation(config, degradation)
    elif degradation_rate is not None and years_elapsed > 0:
        degradation_state = compute_degradation_state(degradation_rate, years_elapsed)
        config = apply_antenna_degradation(config, degradation_state)
    if fault_specs is not None:
        config = apply_antenna_faults(config, fault_specs)
    return config


def basilisk_available() -> bool:
    """返回Basilisk messaging/sysModel模块是否可用."""
    return _messaging is not None and _sysModel is not None


def require_basilisk() -> None:
    """如果Basilisk模块不可用，抛出RuntimeError异常."""
    if not basilisk_available():
        raise RuntimeError("Basilisk messaging/sysModel modules are unavailable")


class AntennaBasilisk(_sysModel.SysModel if basilisk_available() else object):
    """Basilisk天线指向模型.

    模块:
    - 从状态消息读取航天器姿态
    - 读取命令指向方向
    - 计算离轴角和天线增益
    - 写入 Comm子系统

    Attributes
    ----------
    ModelTag : str
        Basilisk模型名称
    config : AntennaConfig
        天线配置
    """

    def __init__(self, model_tag: str = "Antenna", config: AntennaConfig | None = None):
        if basilisk_available():
            super().__init__()
        self.ModelTag = model_tag
        self.config = config or AntennaConfig()

        self._off_boresight_deg = 0.0
        self._last_result = AntennaResult(
            antenna_gain_dbi=self.config.peak_gain_dbi,
            pointing_loss_db=0.0,
            boresight_ok=True,
            limit_violation=False,
        )

        if basilisk_available():
            self.scStateInMsg = _messaging.SCStatesMsgReader()
            self.attitudeErrorInMsg = _messaging.AttMsgReader()
            self.antennaStatusOutMsg = _messaging.AntennaMsg()

    def Reset(self, CurrentSimNanos: int) -> None:
        """重置天线状态.""" 
        self._off_boresight_deg = 0.0
        self._last_result = AntennaResult(
            antenna_gain_dbi=self.config.peak_gain_dbi,
            pointing_loss_db=0.0,
            boresight_ok=True,
            limit_violation=False,
        )

    def UpdateState(self, CurrentSimNanos: int) -> None:
        """执行一次模拟步骤."""
        self._off_boresight_deg = self._compute_off_boresight()
        result = compute_antenna_gain(
            off_boresight_deg=self._off_boresight_deg,
            config=self.config,
        )
        self._last_result = result
        self._write_status()

    def _compute_off_boresight(self) -> float:
        """根据姿态消息计算离轴角."""
        if not basilisk_available():
            return 0.0

        try:
            att_data = self.attitudeErrorInMsg()
            if att_data is not None and hasattr(att_data, "sigma_BN"):
                sigma = att_data.sigma_BN
                if hasattr(sigma, "__iter__"):
                    return math.degrees(math.sqrt(sum(x*x for x in sigma)))
        except Exception as exc:
            record_runtime_diagnostic(
                code='COMPONENT_INPUT_MESSAGE_READ_FAILED',
                category=DiagnosticCategory.MESSAGE_READ_FAILURE,
                location='src/components/antenna/builder.py:_compute_off_boresight:01',
                exception=exc,
                strict=None,
            )
        return 0.0

    def _write_status(self) -> None:
        """将天线状态写入输出消息."""
        if not basilisk_available():
            return
        msg_payload = self.antennaStatusOutMsg.zeroMsgPayload
        msg_payload.antennaGain = self._last_result.antenna_gain_dbi
        msg_payload.pointingLoss = self._last_result.pointing_loss_db
        msg_payload.boresightOK = self._last_result.boresight_ok
        self.antennaStatusOutMsg.write(msg_payload, CurrentSimNanos=0)

    def set_off_boresight(self, angle_deg: float) -> None:
        """设置离轴角 (用于独立测试)."""
        self._off_boresight_deg = float(angle_deg)

    @property
    def last_result(self) -> AntennaResult:
        """获取最近一次计算结果."""
        return self._last_result

    @property
    def off_boresight_deg(self) -> float:
        """获取当前离轴角."""
        return self._off_boresight_deg


def create_antenna_basilisk(
    model_tag: str = "Antenna",
    config: AntennaConfig | None = None,
) -> AntennaBasilisk:
    """创建Basilisk天线模型.

    Args:
        model_tag: 模型标签，默认"Antenna"
        config: 天线配置，默认None

    Returns:
        AntennaBasilisk: Basilisk天线模型实例   
    """
    require_basilisk()
    return AntennaBasilisk(model_tag=model_tag, config=config)

# Component fault/degradation compatibility wrappers
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_antenna_config_base = _build_nominal_antenna_config_base_impl

def build_nominal_antenna_config(
    *args,
    degradation: AntennaDegradation | None = None,
    degradation_rate: AntennaDegradationRate | None = None,
    years_elapsed: float = 0.0,
    fault_specs: list[_ComponentFaultSpec] | None = None,
    **kwargs,
):
    """Build config with canonical degradation-rate, degradation-state and fault support."""
    if "degradation" in kwargs:
        degradation = kwargs.pop("degradation")
    if "degradation_rate" in kwargs:
        degradation_rate = kwargs.pop("degradation_rate")
    if "years_elapsed" in kwargs:
        years_elapsed = kwargs.pop("years_elapsed")
    if "fault_specs" in kwargs:
        fault_specs = kwargs.pop("fault_specs")
    config = _build_nominal_antenna_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_antenna_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_antenna_faults(config, fault_specs)
    return config



# ---------------------------------------------------------------------------
# Basilisk native SimpleAntenna path (COMMDATA-RF-NATIVE-1)
# ---------------------------------------------------------------------------


def _antenna_state_value(operating_mode: str = "rxtx", *, available: bool = True) -> int:
    """Return Basilisk ``AntennaStateEnum`` values used by RF modules.

    ``messaging.AVAILABLE``/``UNAVAILABLE`` belong to generic availability
    payloads and are not valid substitutes for ``SimpleAntenna`` operating
    states.  LinkBudget and DownlinkHandling require OFF/RX/TX/RXTX.
    """
    require_basilisk()
    from Basilisk.simulation import simpleAntenna

    if not bool(available):
        return int(simpleAntenna.ANTENNA_OFF)
    normalized = str(operating_mode or "rxtx").strip().lower().replace("-", "").replace("_", "")
    states = {
        "off": simpleAntenna.ANTENNA_OFF,
        "rx": simpleAntenna.ANTENNA_RX,
        "receive": simpleAntenna.ANTENNA_RX,
        "tx": simpleAntenna.ANTENNA_TX,
        "transmit": simpleAntenna.ANTENNA_TX,
        "rxtx": simpleAntenna.ANTENNA_RXTX,
        "txrx": simpleAntenna.ANTENNA_RXTX,
    }
    if normalized not in states:
        raise ValueError(f"unsupported SimpleAntenna operating_mode: {operating_mode!r}")
    return int(states[normalized])


def _available_state_value(available: bool) -> int:
    """Backward-compatible availability helper: available maps to RXTX."""
    return _antenna_state_value("rxtx", available=available)


def simple_antenna_native_config_from_project(
    config: AntennaConfig,
    *,
    model_tag: str = "SimpleAntenna",
    antenna_name: str = "antenna",
) -> SimpleAntennaNativeConfig:
    """Translate the project antenna config into the native SimpleAntenna contract."""
    return SimpleAntennaNativeConfig(
        model_tag=model_tag,
        antenna_name=antenna_name,
        frequency_hz=float(config.native_frequency_hz),
        bandwidth_hz=float(config.native_bandwidth_hz),
        directivity_db=float(config.native_directivity_db),
        hpbw_ratio=float(config.native_hpbw_ratio),
        tx_power_w=float(config.native_tx_power_w),
        rx_power_w=float(config.native_rx_power_w),
        radiation_efficiency=float(config.native_radiation_efficiency),
        equivalent_noise_temp_k=float(config.native_equivalent_noise_temp_k),
        environment_temp_k=float(config.native_environment_temp_k),
        position_b_m=tuple(float(x) for x in config.native_position_b_m),
        orientation_b=tuple(float(x) for x in config.native_orientation_b),
        available=bool(config.native_available),
        operating_mode=str(config.native_operating_mode),
        use_haslam_map=bool(config.native_use_haslam_map),
    )


def build_simple_antenna_native(config: SimpleAntennaNativeConfig):
    """Create a Basilisk ``simpleAntenna.SimpleAntenna`` module.

    This is the primary Comm/Data RF-chain antenna path.  The legacy
    ``AntennaBasilisk`` helper above remains an analytical project helper and is
    not used to close native RF-chain acceptance.
    """
    require_basilisk()
    from Basilisk.simulation import simpleAntenna

    if config.frequency_hz <= 0.0:
        raise ValueError("frequency_hz must be positive")
    if config.bandwidth_hz <= 0.0:
        raise ValueError("bandwidth_hz must be positive")
    if config.directivity_db <= 9.0:
        raise ValueError("directivity_db must be greater than 9 dB for Basilisk SimpleAntenna")
    if config.hpbw_ratio <= 0.0:
        raise ValueError("hpbw_ratio must be positive")
    if config.tx_power_w <= 1.0e-6:
        raise ValueError("tx_power_w must be greater than Basilisk SimpleAntenna's 1e-6 W threshold")
    if config.rx_power_w <= 1.0e-6:
        raise ValueError("rx_power_w must be greater than Basilisk SimpleAntenna's 1e-6 W threshold")
    if config.radiation_efficiency <= 0.0:
        raise ValueError("radiation_efficiency must be positive")
    if config.equivalent_noise_temp_k <= 0.0 or config.environment_temp_k <= 0.0:
        raise ValueError("antenna temperatures must be positive")

    antenna = simpleAntenna.SimpleAntenna()
    antenna.ModelTag = str(config.model_tag)
    antenna.setAntennaName(str(config.antenna_name))
    antenna.setAntennaFrequency(float(config.frequency_hz))
    antenna.setAntennaBandwidth(float(config.bandwidth_hz))
    antenna.setAntennaDirectivity_dB(float(config.directivity_db))
    antenna.setAntennaHpbwRatio(float(config.hpbw_ratio))
    antenna.setAntennaP_Tx(float(config.tx_power_w))
    antenna.setAntennaP_Rx(float(config.rx_power_w))
    antenna.setAntennaRadEfficiency(float(config.radiation_efficiency))
    antenna.setAntennaEquivalentNoiseTemp(float(config.equivalent_noise_temp_k))
    antenna.setAntennaEnvironmentTemperature(float(config.environment_temp_k))
    antenna.setAntennaPositionBodyFrame([float(x) for x in config.position_b_m])
    antenna.setAntennaOrientationBodyFrame(list(config.orientation_b))
    antenna.setAntennaState(_antenna_state_value(config.operating_mode, available=config.available))
    antenna.setUseHaslamMap(bool(config.use_haslam_map))
    return antenna


def seed_simple_antenna_environment(antenna, environment: str, *, operating_mode: str = "rxtx", available: bool = True) -> None:
    """Seed ``AntennaLogMsg.environment`` before ``LinkBudget.Reset``.

    Basilisk LinkBudget classifies a link as space-space or space-ground during
    Reset, before scheduled SimpleAntenna modules publish their first samples.
    Pre-seeding only the placement/state fields prevents a transient zero
    payload from permanently selecting the wrong pointing-loss branch.
    """
    require_basilisk()
    normalized = str(environment).strip().lower()
    environment_values = {"space": 0, "earth": 1, "ground": 1}
    if normalized not in environment_values:
        raise ValueError(f"unsupported antenna environment: {environment!r}")
    payload = _messaging.AntennaLogMsgPayload()
    payload.environment = int(environment_values[normalized])
    payload.antennaState = _antenna_state_value(operating_mode, available=available)
    antenna.antennaOutMsg.write(payload)


def set_simple_antenna_operating_mode(antenna, operating_mode: str, *, available: bool = True) -> None:
    """Set the RF operating mode using Basilisk OFF/RX/TX/RXTX values."""
    antenna.setAntennaState(_antenna_state_value(operating_mode, available=available))


def set_simple_antenna_availability(antenna, available: bool) -> None:
    """Backward-compatible availability setter; enabled antennas use RXTX."""
    set_simple_antenna_operating_mode(antenna, "rxtx", available=available)


def build_antenna_power_node(model_tag: str = "AntennaPower", *, base_power_w: float = 0.0, node_status_msg=None):
    """Create Basilisk ``AntennaPower`` as a native EPS load node."""
    require_basilisk()
    from Basilisk.simulation import antennaPower

    node = antennaPower.AntennaPower()
    node.ModelTag = str(model_tag)
    node.basePowerNeed = abs(float(base_power_w))
    if node_status_msg is not None and hasattr(node, "nodeStatusInMsg"):
        node.nodeStatusInMsg.subscribeTo(node_status_msg)
    return node
