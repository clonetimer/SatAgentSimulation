# 外置 third-party 说明

本源码包采用 `source_slim` 配置，不内含 `third_party/`。

完整运行依赖由独立的 `R1-DC1 third_party` 制品提供。请使用交付工具按 `R1_DC1_COMPOSITE_MANIFEST.json` 校验并组装；源码清单与 third-party 清单分别保持独立完整性。

外置依赖沿用已核验的 Basilisk `2.11.0+satfix1` Wheel、SPICE 数据和 WMM2025 数据。R1-DC1 未修改这些第三方二进制或数据文件，只重新生成与本阶段身份对应的 third-party Manifest。
