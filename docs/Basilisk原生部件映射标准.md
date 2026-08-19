# NATIVE COMPONENT MAPPING STANDARD

## 1. 唯一能力分类

每个项目公开配置字段必须且只能属于以下一种类型：

1. `native_build_parameter`：创建或初始化 Basilisk 模块时写入。
2. `native_runtime_writable`：仿真运行过程中允许安全修改的 Basilisk 字段或 setter。
3. `native_input_message`：通过 Basilisk 输入消息注入状态、故障或退化。
4. `native_output_only`：Basilisk 计算结果，只允许记录和验收，不允许作为配置写入。
5. `native_chain_composition`：单个模块不足，需要多个 Basilisk 模块和消息形成链路。
6. `project_proxy`：Basilisk 不提供目标物理模型或当前明确保留项目代理。
7. `out_of_scope`：当前交付边界外，不作为阻塞项。
8. `invalid_or_unused`：字段不存在、语义错误、声明后未消费或写入无效目标。

## 2. 支持判定

不得仅凭 SWIG 对象上出现同名属性判定“已支持”。必须同时确认：

- 字段是构造参数、运行时字段、输入消息还是输出；
- 工程 schema 的单位、维度和语义与 Basilisk 一致；
- builder 在可执行代码中实际消费该字段；
- 运行时故障有开始、恢复和目标选择语义；
- 至少一个方向性 QoI 能证明参数影响了 Basilisk 仿真。

## 3. 层级所有权

```text
whole_spacecraft
    -> subsystem builder / subsystem fault scenario
        -> component builder / component fault / component degradation
            -> Basilisk native module, field, setter or input message
```

- 部件层拥有单部件创建、native 映射、部件故障和退化。
- 分系统层连接消息、组合部件和定义链路故障。
- 整星层只组织分系统、环境、任务模式和跨分系统消息。
- 禁止分系统或整星重复实现同一部件物理故障。

## 4. 禁止项

- 禁止修改 native 输出消息来伪造故障。
- 禁止通过 `hasattr(...): pass` 静默忽略不支持的参数。
- 禁止保留函数参数却不在函数体中消费。
- 禁止把输出 payload 字段描述成可写配置。
- 禁止把代理模型包装成 Basilisk native。
- 禁止在 storage 上设置不存在的 read/write rate 属性；速率必须来自数据节点消息。

## 5. Comm/Data 特别规则

基础固定码率链和 RF 感知链是两种验收路径：

```text
SimpleStorageUnit + AccessMsg -> SpaceToGroundTransmitter
```

```text
SimpleAntenna x2 -> LinkBudget -> DownlinkHandling -> DataNodeUsageMsg -> SimpleStorageUnit
```

BER、PER、CNR、EIRP、交付/丢弃速率属于输出，只能通过上游输入变化产生。
