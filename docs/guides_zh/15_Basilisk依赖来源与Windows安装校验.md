# Basilisk 2.11.0+satfix1 来源与 Windows 安装校验（#38 精简移植）

## 1. 两种 Basilisk wheel 不能混淆

当前项目 `pyproject.toml`、`constraints.txt`、`requirements.txt` 固定 `bsk==2.11.0+satfix1`。仓库里的 `scripts/build_bsk_compat_wheels.py` 经审计**只对经过官方 SHA-256 核验的 Basilisk 2.11.0 wheel 修改分发元数据**：

- 依赖 Pillow 上限从 `pillow<=12.2.0,>=10.4.0` 改为 `pillow<13,>=10.4.0`；
- wheel 本地版本从 `2.11.0` 标为 `2.11.0+satfix1`，同步重建符合 wheel 标准的 `RECORD`；
- **不修改任何 Basilisk 原生 C++ 模型、Python 模块、执行动态或其他载荷文件**。构建脚本检验二进制文件逐项散列一致。

请特别区分 R9-K：它是另外一份**重新编译 C++ SimpleBattery 快照接口的实验性 wheel**，其版本元数据为 `2.11.0`，只供独立 CI 单模块实验，不得取代生产使用的 metadata-only `satfix1`，也不得宣传为整星恢复可用。

## 2. 官方二进制锁定与本地校验

项目固定上游 AVSLab/Basilisk `v2.11.0`（提交 `6c1511f5385dccaa73c9ae24522a74adc24fe623`）；wheel 依赖源是 Python 官方 PyPI files.pythonhosted.org 的明确文件名与 SHA-256，不是第三方网盘或未经确认的 Mod/模型站点。

| 平台 | 官方 Wheel SHA-256 |
|---|---|
| Windows x64，cp39-abi3 | `5558268290474206a1a600c6bbf1847ed02e2c99c10a062e1d98a38076f58eb0` |
| Linux x86_64，cp39-abi3 manylinux | `d51fe1ffa1b0b03dc415034b210497b28a2504428d4d8e71b481542a08c08a0a` |

构建出 metadata-only wheel 后，新校验器 `scripts/bsk_wheel_provenance.py` 在**任何离线安装前**检查构建报告的官方来源、受批准的平台文件名、目标 wheel SHA-256、Pillow 元数据差异和 wheel 内的全部 `RECORD` 哈希及大小。不匹配则拒绝安装。

## 3. Windows 非程序员安装路径（源码工作区）

在 Windows PowerShell 中，打开项目根目录，先确认安装了 **64 位 Python 3.11**（项目可用版本 3.10～3.13），然后：

```powershell
# 首次安装
.\scripts\install_windows.ps1

# 安装后启动本地服务
.\scripts\start_windows.ps1

# 如已有损坏虚拟环境，再执行：
.\scripts\repair_windows_environment.ps1
```

`bootstrap_windows.ps1` 现在强制执行 `ensure_basilisk_runtime.py --require-satfix1`，即使机器上原来装了普通 `bsk==2.11.0` 也不会跳过。它先从本地已验证的 wheelhouse 复用，缺失时才从固定官方 URL 下载、核验源 SHA-256 并重建 metadata-only wheel，最后通过 `install_offline_bsk.py` 做 RECORD+版本验证后安装。构建本地 wheel **不需要 C++ 编译器**，并不改变 Basilisk 物理引擎。

离线方式可在有网络的机器上先生成（必须使用适配目标平台的官方 wheel）：

```powershell
python scripts\fetch_official_bsk_and_build_offline_bundle.py --platform windows_x86_64
python scripts\install_offline_bsk.py --check-only
```

然后把 `third_party/wheels` 中的 wheel 与 `bsk_2.11.0_satfix1_build_report.json` 转移到目标机的相同目录。**仅复制 Basilisk wheel 不代表完整离线安装包**：其余 Python 依赖仍需另外准备、校验并配置本地 wheelhouse。本轮 GitHub Windows CI 对有联网的洁净机器执行完整流程，不宣称已经交付全部依赖的离线安装包。

## 4. 验收与剩余边界

- 参见 `.github/workflows/basilisk-install-provenance.yml`：Windows x64 Python 3.12 / Linux x86_64 Python 3.13 的实际官方文件下载、metadata-only 重新打包、安装前篡改拒绝、Windows bootstrap、`bsk==2.11.0+satfix1` 导入、真实 Basilisk C++ `SimpleBattery` 两步计算，保留短期 wheel+清单工件。
- 参见 `tests/test_bsk_wheel_provenance.py`：非法来源、错误哈希、缺失 RECORD、跨平台替代、篡改 METADATA 的校验拒绝。这些是**校验器单元测试**，并非卫星物理数据集。
- 仍未验收所有 Python ABI/Windows GPU 环境、完全断网的所有依赖、或用户本地 24 小时真实墙钟可靠性。只有全部原生轮状态所有者恢复且新进程与不中断物理轨迹长期等价，才可改变恢复准入。

**整星中断恢复状态始终为 `BLOCKED`（12 owner / 0 fully restored），自动 TC 重放永久禁止进入未验收的发布路径。**


## 5. 2026-10-23 安全审计阻塞（不可将当前包命名为安全 Stable Release）

全量依赖审计在本次洁净安装 PR 中发现 **36 条漏洞记录**：Pillow 12.3.0（35 条，审计建议修复至 13.0.0）和 certifi 2026.6.17（1 条，审计建议修复至 2026.10.19）。证据由 `dependency-audit` 的 `pip_audit_raw.json` 保存，整改追踪在 [Issue #39](https://github.com/clonetimer/SatAgentSimulation/issues/39)。

**当前 `satfix1` 明确限定 Pillow<13，因此不能单纯 `pip install --upgrade pillow` 修复安全问题。** Pillow 13 同时不再支持 Python 3.10。下一阶段需要对经版本化的新 metadata-only 构建（例如不复用 `satfix1` 名称）做实际 Basilisk 数值回归，再对所有 Python 依赖运行完整的 0 漏洞门禁。

本页描述的是**现有兼容安装路径的可重复性**，不等同于已取得安全合规的二进制 Stable Release 资质。在 Issue #39 和所有必需的 CI 阶段完成之前，不发布正式安全 Stable 标签，不禁用依赖审计，也不把实验性的 Basilisk 电池 C++ wheel 替换生产 wheel。

## 6. 当前移植范围与安全边界（2026-10-24）

本次从旧 #38 提取**可验证的来源审计、首次安装和字符编码修复**，不继承其无条件安全发布目标，也不修改依赖锁文件。原版 Python 3.11 的条件 SciPy/Scikit-learn 约束修复因会触发已有锁文件漏洞审计，尚未随本 PR 进入主干；当前 Windows 源码洁净安装门禁选择 Python **3.12**。旧 #38 在安全门禁未清零时不可作为 Stable Release 直接合并。

已知锁文件漏洞仍由 [Issue #39](https://github.com/clonetimer/SatAgentSimulation/issues/39) 追踪，绝不以本页的 wheel 源校验、Basilisk C++ smoke 代替依赖安全审计。产品不得声称具备安全合规的 Stable 交付资格，整星物理断点恢复状态仍为 BLOCKED。
