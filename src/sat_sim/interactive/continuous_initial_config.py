"""Strict, provenance-aware pre-start configuration for continuous native whole spacecraft.

Only native-wired values are exposed. This is not a generic component editor:
a parameter must be read by the actual Basilisk unified graph and validated
before it can reach a fresh runtime. No arbitrary tasks, paths or Python code.
"""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class NativeOrbitInitial(_Strict):
    spacecraft_mass_kg: float = Field(
        default=100., ge=1., le=2000.,
        description="整星原生 Spacecraft.hub.mHub 总质量（kg）；质量与下面的惯量需分别配置，不从质量推算惯量。",
    )
    spacecraft_inertia_xx_kg_m2: float = Field(default=100., gt=0., le=10000., description="Basilisk Spacecraft.hub.IHubPntBc_B 机体系主惯量 Ixx（kg·m²），不自动由质量推算")
    spacecraft_inertia_yy_kg_m2: float = Field(default=80., gt=0., le=10000., description="Basilisk Spacecraft.hub.IHubPntBc_B 机体系主惯量 Iyy（kg·m²）")
    spacecraft_inertia_zz_kg_m2: float = Field(default=60., gt=0., le=10000., description="Basilisk Spacecraft.hub.IHubPntBc_B 机体系主惯量 Izz（kg·m²）")
    orbit_radius_m: float = Field(default=7_000_000., ge=6_500_000., le=20_000_000.)
    eccentricity: float = Field(default=.001, ge=0., le=.2)
    inclination_deg: float = Field(default=35., ge=0., le=180.)
    initial_true_anomaly_rad: float = Field(default=0., ge=0., le=6.283185307179586)

    @model_validator(mode="after")
    def validate_principal_inertia(self):
        xx, yy, zz = (self.spacecraft_inertia_xx_kg_m2,
                      self.spacecraft_inertia_yy_kg_m2,
                      self.spacecraft_inertia_zz_kg_m2)
        if xx >= yy + zz or yy >= xx + zz or zz >= xx + yy:
            raise ValueError("SPACECRAFT_PRINCIPAL_INERTIA_TRIANGLE_VIOLATION")
        return self


class NativeAdcsInitial(_Strict):
    imu_gyro_bias_rad_s_x: float = Field(default=0., ge=-0.1, le=0.1, description="原生 IMU X 轴陀螺恒定偏置（rad/s）")
    imu_gyro_bias_rad_s_y: float = Field(default=0., ge=-0.1, le=0.1, description="原生 IMU Y 轴陀螺恒定偏置（rad/s）")
    imu_gyro_bias_rad_s_z: float = Field(default=0., ge=-0.1, le=0.1, description="原生 IMU Z 轴陀螺恒定偏置（rad/s）")
    imu_accel_bias_m_s2_x: float = Field(default=0., ge=-1., le=1., description="原生 IMU X 轴加速度计恒定偏置（m/s²）")
    imu_accel_bias_m_s2_y: float = Field(default=0., ge=-1., le=1., description="原生 IMU Y 轴加速度计恒定偏置（m/s²）")
    imu_accel_bias_m_s2_z: float = Field(default=0., ge=-1., le=1., description="原生 IMU Z 轴加速度计恒定偏置（m/s²）")
    imu_gyro_max_rad_s: float = Field(default=0., ge=0., le=1000., description="原生 IMU 陀螺最大量程（rad/s，0=保持原生默认）")
    imu_accel_max_m_s2: float = Field(default=0., ge=0., le=1000., description="原生 IMU 加速度计最大量程（m/s²，0=保持原生默认）")
    star_tracker_noise_std_arcsec: float = Field(default=0., ge=0., le=3600., description="原生星敏逐轴白噪声标准差（arcsec，0=关闭）")
    imu_mount_yaw_deg: float = Field(default=0., ge=-180., le=180., description="IMU 平台相对机体系的航向安装角 (321 Euler; deg)")
    imu_mount_pitch_deg: float = Field(default=0., ge=-89., le=89., description="IMU 平台相对机体系的俯仰安装角 (321 Euler; deg)")
    imu_mount_roll_deg: float = Field(default=0., ge=-180., le=180., description="IMU 平台相对机体系的横滚安装角 (321 Euler; deg)")
    imu_position_b_x_m: float = Field(default=0., ge=-2., le=2., description="IMU 安装中心在机体系 B 中的 X 坐标 (m)")
    imu_position_b_y_m: float = Field(default=0., ge=-2., le=2., description="IMU 安装中心在机体系 B 中的 Y 坐标 (m)")
    imu_position_b_z_m: float = Field(default=0., ge=-2., le=2., description="IMU 安装中心在机体系 B 中的 Z 坐标 (m)")
    star_tracker_mount_yaw_deg: float = Field(default=0., ge=-180., le=180., description="星敏相对机体系的航向安装角 (321 Euler; deg)")
    star_tracker_mount_pitch_deg: float = Field(default=0., ge=-89., le=89., description="星敏相对机体系的俯仰安装角 (321 Euler; deg)")
    star_tracker_mount_roll_deg: float = Field(default=0., ge=-180., le=180., description="星敏相对机体系的横滚安装角 (321 Euler; deg)")
    sun_sensor_fov_deg: float = Field(
        default=80., gt=0., le=180.,
        description="原生 Basilisk CoarseSunSensor.fov 视场全角（deg），仅影响该光敏器观测，不代表所有敏感器的视场。",
    )
    initial_pointing_error_deg: float = Field(default=8., ge=0., le=90.)
    controller_k: float = Field(default=3.5, gt=0., le=100.)
    controller_p: float = Field(default=30., gt=0., le=300.)
    rw_configuration: Literal["orthogonal_3", "pyramid_4"] = "orthogonal_3"
    rw_friction_mode: Literal["disabled", "coulomb_viscous"] = Field(
        default="disabled", description="原生 RW 摩擦：关闭或启用库仑/粘滞摩擦；整轮组共用相同系数",
    )
    rw_coulomb_friction_nm: float = Field(
        default=0., ge=0., le=0.01,
        description="原生 rwFactory.create.fCoulomb，所有 RW 共用；N·m",
    )
    rw_viscous_friction_nms: float = Field(
        default=0., ge=0., le=0.01,
        description="原生 rwFactory.create.cViscous，所有 RW 共用；N·m·s/rad",
    )
    rw_mount_radius_m: float = Field(
        default=0., ge=0., le=0.25,
        description="原生 rwFactory.create.rWB_B，按每只轮安装轴乘以共同偏移距离；m（简化安装几何）",
    )
    rw_max_torque_nm: float = Field(
        default=.2, gt=0., le=.2,
        description="真实 Basilisk RW 电机 u_max 与指令故障管理器的共同限幅 (N·m)",
    )
    rw_max_momentum_nms_0: Literal[50, 75, 100] = Field(default=50, description="RW1 Honeywell HR16 额定动量容量（N·m·s）；原生 maxMomentum，决定转子 Js，只能选择 50/75/100")
    rw_max_momentum_nms_1: Literal[50, 75, 100] = Field(default=50, description="RW2 Honeywell HR16 额定动量容量（N·m·s）")
    rw_max_momentum_nms_2: Literal[50, 75, 100] = Field(default=50, description="RW3 Honeywell HR16 额定动量容量（N·m·s）")
    rw_max_momentum_nms_3: Literal[50, 75, 100] | None = Field(default=None, description="仅 4 轮金字塔构型可配置 RW4 动量容量（N·m·s）；不指定时为 50")
    rw_initial_speed_rad_s_0: float = Field(default=10.471975511965978, ge=-500., le=500., description="RW1 原生初始轮速 (rad/s)")
    rw_initial_speed_rad_s_1: float = Field(default=20.943951023931955, ge=-500., le=500., description="RW2 原生初始轮速 (rad/s)")
    rw_initial_speed_rad_s_2: float = Field(default=31.41592653589793, ge=-500., le=500., description="RW3 原生初始轮速 (rad/s)")
    rw_initial_speed_rad_s_3: float | None = Field(default=None, ge=-500., le=500.,
                                                   description="仅 4 轮金字塔构型可设置 RW4 初始轮速 (rad/s)")

    @model_validator(mode="after")
    def wheel_count_matches_hardware(self):
        if self.rw_configuration != "pyramid_4" and self.rw_initial_speed_rad_s_3 is not None:
            raise ValueError("RW4_INITIAL_SPEED_REQUIRES_PYRAMID_4")
        if self.rw_configuration != "pyramid_4" and self.rw_max_momentum_nms_3 is not None:
            raise ValueError("RW4_MAX_MOMENTUM_REQUIRES_PYRAMID_4")
        if self.rw_friction_mode == "disabled" and (
            self.rw_coulomb_friction_nm != 0 or self.rw_viscous_friction_nms != 0
        ):
            raise ValueError("RW_FRICTION_DISABLED_REQUIRES_ZERO_COEFFICIENTS")
        if self.rw_friction_mode == "coulomb_viscous" and (
            self.rw_coulomb_friction_nm == 0 and self.rw_viscous_friction_nms == 0
        ):
            raise ValueError("RW_FRICTION_ENABLED_REQUIRES_POSITIVE_COEFFICIENT")
        return self


class NativeEpsInitial(_Strict):
    battery_capacity_wh: float = Field(default=160., gt=0., le=2000.)
    initial_soc: float = Field(default=.62, ge=0.05, le=1.)
    solar_panel_area_m2: float = Field(default=2.5, gt=0., le=25.)
    solar_efficiency: float = Field(default=.28, gt=0., le=1.)
    solar_normal_b_x: float = Field(default=1., ge=-1., le=1., description="原生太阳电池板固定法线在机体系 B 的 X 分量；不表示自动追踪")
    solar_normal_b_y: float = Field(default=0., ge=-1., le=1., description="原生太阳电池板固定法线在机体系 B 的 Y 分量；三轴必须构成单位向量")
    solar_normal_b_z: float = Field(default=0., ge=-1., le=1., description="原生太阳电池板初始法线在机体系 B 的 Z 分量；三轴必须构成单位向量")
    solar_tracking_enabled: bool = Field(default=False, description="可选项目运动学跟踪控制；官方 Basilisk 太阳板仍负责发电，无真实铰链惯量/电机/反力矩")
    solar_tracking_max_slew_rad_s: float = Field(default=0., ge=0., le=1., description="太阳板法线最大角速度（rad/s）；启用跟踪时必须大于 0")
    bus_power_w: float = Field(default=18., ge=0., le=300.)

    @model_validator(mode="after")
    def check_panel_normal(self):
        norm_sq = sum(value * value for value in (
            self.solar_normal_b_x, self.solar_normal_b_y, self.solar_normal_b_z,
        ))
        if not 0.999 <= norm_sq <= 1.001:
            raise ValueError("SOLAR_PANEL_NORMAL_B_NOT_UNIT")
        if self.solar_tracking_enabled and self.solar_tracking_max_slew_rad_s <= 0.:
            raise ValueError("SOLAR_TRACKING_REQUIRES_POSITIVE_SLEW_RATE")
        if not self.solar_tracking_enabled and self.solar_tracking_max_slew_rad_s != 0.:
            raise ValueError("SOLAR_TRACKING_DISABLED_REQUIRES_ZERO_RATE")
        return self


class NativePayloadInitial(_Strict):
    payload_data_rate_bps: float = Field(default=2_500_000., ge=0., le=20_000_000.)
    payload_power_w: float = Field(default=38., ge=0., le=250.)


class NativeCommInitial(_Strict):
    storage_capacity_bits: float = Field(default=6_000_000_000., gt=0., le=100_000_000_000.)
    downlink_rate_bps: float = Field(default=1_500_000., gt=0., le=20_000_000.)


class NativePropulsionInitial(_Strict):
    """Official Basilisk thrusters + fuel tank; disabled unless opted in."""

    propulsion_enabled: bool = Field(default=False, description="是否在整星原生图中装配推进器、燃料箱与点火控制器")
    propulsion_thrust_n: float = Field(default=1.0, gt=0., le=50., description="每只 Basilisk 推进器的标称最大推力（N）")
    propulsion_isp_s: float = Field(default=200., gt=0., le=5000., description="推进器比冲（s）")
    propulsion_burn_start_s: float = Field(default=30., ge=0., le=86400., description="相对于会话仿真时间起点的点火起始时刻（s）")
    propulsion_burn_on_time_s: float = Field(default=0.5, gt=0., le=120., description="每只推进器的点火持续时长（s）")
    propulsion_initial_propellant_kg: float = Field(default=1., ge=0., le=100., description="原生 FuelTank 初始燃料质量（kg）")
    propulsion_tank_capacity_kg: float = Field(default=2., gt=0., le=100., description="原生 FuelTank 容量（kg），不得小于初始燃料质量")
    propulsion_min_burn_soc: float = Field(default=.30, ge=0., le=1., description="推进点火许可的最小电池 SOC")
    propulsion_power_w: float = Field(default=20., ge=0., le=500., description="每只点火中的推进器电功率估算（W，项目侧功率桥）")
    propulsion_min_pulse_s: float = Field(default=.05, gt=0., le=10., description="真实 Basilisk Thruster MinOnTime（s）；硬件最小脉冲，不等于计划点火时间")
    propulsion_thruster_0_direction_b_x: float = Field(default=1., ge=-1., le=1., description="原生推进器 1 的机体系 B 推力方向 X 分量（必须为单位向量）")
    propulsion_thruster_0_direction_b_y: float = Field(default=0., ge=-1., le=1., description="原生推进器 1 的机体系 B 推力方向 Y 分量（必须为单位向量）")
    propulsion_thruster_0_direction_b_z: float = Field(default=0., ge=-1., le=1., description="原生推进器 1 的机体系 B 推力方向 Z 分量（必须为单位向量）")
    propulsion_thruster_0_location_b_x_m: float = Field(default=0., ge=-2., le=2., description="推进器 1 的机体系 B 安装位置 X（m），相对整星质心坐标原点")
    propulsion_thruster_0_location_b_y_m: float = Field(default=.5, ge=-2., le=2., description="推进器 1 的机体系 B 安装位置 Y（m），相对整星质心坐标原点")
    propulsion_thruster_0_location_b_z_m: float = Field(default=0., ge=-2., le=2., description="推进器 1 的机体系 B 安装位置 Z（m），相对整星质心坐标原点")
    propulsion_thruster_1_direction_b_x: float = Field(default=1., ge=-1., le=1., description="原生推进器 2 的机体系 B 推力方向 X 分量（必须为单位向量）")
    propulsion_thruster_1_direction_b_y: float = Field(default=0., ge=-1., le=1., description="原生推进器 2 的机体系 B 推力方向 Y 分量（必须为单位向量）")
    propulsion_thruster_1_direction_b_z: float = Field(default=0., ge=-1., le=1., description="原生推进器 2 的机体系 B 推力方向 Z 分量（必须为单位向量）")
    propulsion_thruster_1_location_b_x_m: float = Field(default=0., ge=-2., le=2., description="推进器 2 的机体系 B 安装位置 X（m），相对整星质心坐标原点")
    propulsion_thruster_1_location_b_y_m: float = Field(default=-.5, ge=-2., le=2., description="推进器 2 的机体系 B 安装位置 Y（m），相对整星质心坐标原点")
    propulsion_thruster_1_location_b_z_m: float = Field(default=0., ge=-2., le=2., description="推进器 2 的机体系 B 安装位置 Z（m），相对整星质心坐标原点")

    @model_validator(mode="after")
    def check_propellant_capacity(self):
        if self.propulsion_initial_propellant_kg > self.propulsion_tank_capacity_kg:
            raise ValueError("PROPULSION_INITIAL_FUEL_EXCEEDS_TANK_CAPACITY")
        for index in (0, 1):
            direction = [getattr(self, f"propulsion_thruster_{index}_direction_b_{axis}")
                         for axis in "xyz"]
            norm_sq = sum(x * x for x in direction)
            if not 0.999 <= norm_sq <= 1.001:
                raise ValueError(f"PROPULSION_THRUSTER_{index}_DIRECTION_NOT_UNIT")
        return self


class NativeThermalInitial(_Strict):
    """Project thermal network executed inside the Basilisk task, not BSK-native."""

    payload_thermal_mass_kg: float = Field(default=3., gt=0., le=50., description="载荷热节点质量（kg），参与热容量 m×cp，项目热网络")
    payload_specific_heat_j_kg_k: float = Field(default=600., gt=0., le=3000., description="载荷热节点比热容（J/(kg·K)），项目热网络")
    payload_thermal_emissivity: float = Field(default=.75, ge=0., le=1., description="载荷节点辐射率 0–1，项目热网络")
    payload_radiator_area_m2: float = Field(default=.25, ge=0., le=5., description="载荷散热器面积（m²），项目热网络，不代表整星所有散热器")
    battery_heater_power_w: float = Field(default=12., ge=0., le=100., description="电池热节点加热器标称功率（W），项目热网络")
    battery_heater_setpoint_k: float = Field(default=286., ge=240., le=310., description="电池加热器目标温度（K），项目热网络滞回控制")

    initial_payload_temp_k: float = Field(default=292., ge=220., le=340., description="载荷热节点初始温度（K），项目热网络")
    initial_comm_temp_k: float = Field(default=295., ge=230., le=335., description="通信热节点初始温度（K），项目热网络")
    initial_propulsion_temp_k: float = Field(default=292., ge=220., le=350., description="推进热节点初始温度（K），项目热网络")
    comm_structure_conductance_w_k: float = Field(default=.35, ge=0., le=5., description="通信—结构导热系数（W/K），项目热网络")
    propulsion_structure_conductance_w_k: float = Field(default=.45, ge=0., le=5., description="推进—结构导热系数（W/K），项目热网络")


class ContinuousInitialConfiguration(_Strict):
    """Editable subsystems/devices of the *integrated* native spacecraft."""

    orbit: NativeOrbitInitial = Field(default_factory=NativeOrbitInitial)
    adcs: NativeAdcsInitial = Field(default_factory=NativeAdcsInitial)
    eps: NativeEpsInitial = Field(default_factory=NativeEpsInitial)
    payload: NativePayloadInitial = Field(default_factory=NativePayloadInitial)
    comm_data: NativeCommInitial = Field(default_factory=NativeCommInitial)
    propulsion: NativePropulsionInitial = Field(default_factory=NativePropulsionInitial)
    thermal: NativeThermalInitial = Field(default_factory=NativeThermalInitial)

    def native_values(self) -> dict[str, Any]:
        # Only explicitly chosen groups/values are forwarded. Omitted fields
        # retain existing Basilisk graph defaults, including future changes.
        values: dict[str, Any] = {}
        for group in ("orbit", "adcs", "eps", "payload", "comm_data", "propulsion", "thermal"):
            model = getattr(self, group)
            if group in self.model_fields_set:
                values.update(model.model_dump(exclude_unset=True))
        return values


_DISPLAY = {
    "orbit": "整星质量与轨道初始条件",
    "adcs": "ADCS：IMU、星敏安装、太阳敏感器与反作用轮",
    "eps": "电池与太阳电池板（EPS／单机，含项目限速跟踪）",
    "payload": "载荷设备",
    "comm_data": "通信数据存储",
    "propulsion": "推进分系统（Basilisk 原生执行机构／燃料箱）",
    "thermal": "热控分系统（项目热网络，非 Basilisk 原生热模型）",
}

_UNITS = {
    "spacecraft_mass_kg": "kg", "sun_sensor_fov_deg": "deg",
    "spacecraft_inertia_xx_kg_m2": "kg·m²", "spacecraft_inertia_yy_kg_m2": "kg·m²",
    "spacecraft_inertia_zz_kg_m2": "kg·m²",
    "imu_gyro_bias_rad_s_x": "rad/s",
    "imu_gyro_bias_rad_s_y": "rad/s",
    "imu_gyro_bias_rad_s_z": "rad/s",
    "imu_accel_bias_m_s2_x": "m/s²",
    "imu_accel_bias_m_s2_y": "m/s²",
    "imu_accel_bias_m_s2_z": "m/s²",
    "imu_gyro_max_rad_s": "rad/s",
    "imu_accel_max_m_s2": "m/s²",
    "star_tracker_noise_std_arcsec": "arcsec",
    "imu_mount_yaw_deg": "deg",
    "imu_mount_pitch_deg": "deg",
    "imu_mount_roll_deg": "deg",
    "imu_position_b_x_m": "m",
    "imu_position_b_y_m": "m",
    "imu_position_b_z_m": "m",
    "star_tracker_mount_yaw_deg": "deg",
    "star_tracker_mount_pitch_deg": "deg",
    "star_tracker_mount_roll_deg": "deg",
    "orbit_radius_m": "m", "eccentricity": "1", "inclination_deg": "deg",
    "initial_true_anomaly_rad": "rad",
    "initial_pointing_error_deg": "deg", "controller_k": "1", "controller_p": "1",
    "rw_configuration": "枚举", "rw_max_torque_nm": "N·m",
    "rw_friction_mode": "枚举", "rw_coulomb_friction_nm": "N·m",
    "rw_viscous_friction_nms": "N·m·s/rad", "rw_mount_radius_m": "m",
    "rw_max_momentum_nms_0": "N·m·s", "rw_max_momentum_nms_1": "N·m·s",
    "rw_max_momentum_nms_2": "N·m·s", "rw_max_momentum_nms_3": "N·m·s",
    "rw_initial_speed_rad_s_0": "rad/s", "rw_initial_speed_rad_s_1": "rad/s",
    "rw_initial_speed_rad_s_2": "rad/s", "rw_initial_speed_rad_s_3": "rad/s",
    "battery_capacity_wh": "Wh", "initial_soc": "1", "solar_panel_area_m2": "m²",
    "solar_efficiency": "1", "bus_power_w": "W",
    "solar_normal_b_x": "1", "solar_normal_b_y": "1", "solar_normal_b_z": "1",
    "solar_tracking_enabled": "布尔", "solar_tracking_max_slew_rad_s": "rad/s",
    "payload_data_rate_bps": "bit/s", "payload_power_w": "W",
    "storage_capacity_bits": "bit", "downlink_rate_bps": "bit/s",
    "propulsion_enabled": "布尔", "propulsion_thrust_n": "N", "propulsion_isp_s": "s",
    "propulsion_burn_start_s": "s", "propulsion_burn_on_time_s": "s",
    "propulsion_initial_propellant_kg": "kg", "propulsion_tank_capacity_kg": "kg",
    "propulsion_min_burn_soc": "1", "propulsion_power_w": "W",
    "propulsion_min_pulse_s": "s",
    "propulsion_thruster_0_direction_b_x": "1",
    "propulsion_thruster_0_location_b_x_m": "m",
    "propulsion_thruster_0_direction_b_y": "1",
    "propulsion_thruster_0_location_b_y_m": "m",
    "propulsion_thruster_0_direction_b_z": "1",
    "propulsion_thruster_0_location_b_z_m": "m",
    "propulsion_thruster_1_direction_b_x": "1",
    "propulsion_thruster_1_location_b_x_m": "m",
    "propulsion_thruster_1_direction_b_y": "1",
    "propulsion_thruster_1_location_b_y_m": "m",
    "propulsion_thruster_1_direction_b_z": "1",
    "propulsion_thruster_1_location_b_z_m": "m",
    "payload_thermal_mass_kg": "kg", "payload_specific_heat_j_kg_k": "J/(kg·K)",
    "payload_thermal_emissivity": "1", "payload_radiator_area_m2": "m²",
    "battery_heater_power_w": "W", "battery_heater_setpoint_k": "K",
    "initial_payload_temp_k": "K", "initial_comm_temp_k": "K",
    "initial_propulsion_temp_k": "K", "comm_structure_conductance_w_k": "W/K",
    "propulsion_structure_conductance_w_k": "W/K",
}


def initial_configuration_catalog() -> dict[str, Any]:
    """Single source for form defaults, supported controls and native scope."""
    groups = []
    for name, cls in (
        ("orbit", NativeOrbitInitial),
        ("adcs", NativeAdcsInitial),
        ("eps", NativeEpsInitial),
        ("payload", NativePayloadInitial),
        ("comm_data", NativeCommInitial),
        ("propulsion", NativePropulsionInitial),
        ("thermal", NativeThermalInitial),
    ):
        template = cls()
        fields = []
        for key, field in cls.model_fields.items():
            fields.append({
                "name": key, "default": getattr(template, key),
                "unit": _UNITS[key],
                "description": field.description or key.replace("_", " "),
                "native_value_key": key,
                "validation": str(field.annotation),
            })
        groups.append({"group": name, "label": _DISPLAY[name], "fields": fields})
    return {
        "schema_version": "sat-native-initial-configuration.v1",
        "capability_id": "whole_spacecraft.unified_native.v1",
        "model_source": "sat_sim.bsk_engine.unified_native.UnifiedNativeRuntime",
        "graph_scope": "INTEGRATED_WHOLE_NATIVE_ONLY",
        "user_configurable": True,
        "configuration_time": "PREPARE_ONLY",
        "standalone_subsystem_continuous_simulation_supported": False,
        "arbitrary_component_types_supported": False,
        "experimental_full_native_resume_supported": False,
        "groups": groups,
    }
