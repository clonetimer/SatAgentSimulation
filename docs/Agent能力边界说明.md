# Agent capability and claim boundary

## 1. Product entry point

`src/sat_sim/agent_facade.py` is the formal Agent product entry point. The former `agent_orchestrator` API remains a compatibility wrapper and delegates to the facade. `capability_agent` remains the constrained draft backend; it is not an independent execution pipeline.

## 2. Allowed semantic tools

The Agent may request only registered semantic operations:

- list capabilities;
- read a capability contract;
- read the TaskSpec schema;
- validate or migrate a TaskSpec;
- compile a TaskSpec;
- export a reproducible script;
- run a registered simulation;
- inspect run artifacts.

## 3. Forbidden operations

The Agent may not:

- write or execute arbitrary Python;
- import private modules;
- construct Basilisk objects directly;
- modify simulation source code;
- write Basilisk output messages;
- bypass capability adapters or public runners;
- invent a capability, effect, parameter, or QoI;
- relax validation thresholds to obtain a pass result.

The machine-readable policy is written to every Agent bundle as `agent_tool_policy.json`.

## 4. Capability source of truth

Agent exposure is derived from Capability Registry lifecycle metadata. The Agent must not maintain a second hand-written list of active capabilities.

When an optional example template is absent, the deterministic template backend may synthesize a minimal draft only from the selected capability contract. The bundle records:

```text
reason_code: TEMPLATE_SYNTHESIZED
```

This fallback does not create a new capability or infer undocumented physical behavior.

## 5. Physical causal requirement guard

The Agent may identify required physical causal links from the user request, but it does not decide that a capability supports them. Detected links are written to `mission.required_couplings` and checked against the selected Capability integration contract.

Rules:

- every Capability declares its supported coupling set explicitly;
- a whole-spacecraft Capability is not treated as supporting every coupling by default;
- unsupported causal requirements block planning and list the missing coupling IDs;
- causal requirements remain in ResolvedSpec, ExecutionPlan and Run Bundle input evidence;
- the Agent must not remove a required coupling merely to make validation pass.

## 6. Parameter and claim guard

Claims cannot exceed the parameter evidence profile. In particular:

- `demo` cannot claim engineering, ground-calibrated, or flight-correlated validity;
- `engineering_estimate` cannot claim ground calibration;
- proxy events require explicit `assurance.allow_proxy: true`;
- a high-fidelity claim requires explicit support in the capability contract;
- flight validation, hardware calibration, and certification are forbidden unless a later evidence gate explicitly enables them.

Every Agent bundle contains `guard_report.json` with allowed claims, forbidden claims, and structured reason codes.

## 7. 当前工程边界

既有工程版本 已将 effect ownership、observability、dependencies、resource locks、retry semantics、claim ceilings、运行证据和 QoI 结论纳入确定性闭环。当前边界如下：

- `sat-agent doctor` 与 `release-check` 只汇总现有注册表、Golden Set、实际 Run 和制品证据，不创造新的物理可信度；
- 13 项 `source_native` 注册实现具备直接 QoI 证据，但不代表硬件标定、飞行相关或认证；
- `solar_panel.deployment_failure` 是显式 `PROJECT_PROXY`，需要 `assurance.allow_proxy=true`；
- `battery.self_discharge_rate_increase_pct` 为 `OUT_OF_SCOPE`，规划阶段返回 `MODEL_EXTENSION_REQUIRED`；
- 轻量 wheel 不携带大型 SPICE 内核，严格环境任务需使用 assets/full 制品或配置外部资源；
- 本地 API 不等同于生产控制面，不包含鉴权、TLS、多租户和分布式队列；
- 后续工作必须由新模型、真实数据、Provider 评测或部署需求驱动，不得通过放宽门禁提升通过率。
