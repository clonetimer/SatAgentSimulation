# 10｜理解 TaskSpec 与生成代码

## 本课目标

在已经会用以后，再理解 V15 为什么不是一个“拖节点拼 Python”的玩具编辑器。

---

# 1. 真正的编译链

V15 的核心链路是：

```text
图形工程
  ↓
端口/模块合同校验
  ↓
Canonical TaskSpec
  ↓
Execution Plan
  ↓
Deterministic Script Exporter
  ↓
run_simulation.py
  ↓
Registered Capability Adapter
```

---

# 2. TaskSpec 是什么

TaskSpec 可以理解为：

> 与 UI 无关、与节点坐标无关的标准任务说明书。

所以：

```text
节点从左边拖到右边
```

不应该改变 TaskSpec。

而：

```text
Kp 从 0.08 改成 0.12
```

应该改变 TaskSpec。

这也是 V15 为什么能够正确判断“代码是否 stale”。

---

# 3. 为什么不能放任意 Python Block

如果允许任意节点直接执行任意 Python：

- 类型检查会失效；
- module contract 会失效；
- 生成程序无法稳定复现；
- 图上审核内容与实际执行代码可能不一致；
- 安全边界无法保证。

因此 V15 只允许注册能力和注册模块进入物理执行链。

---

# 4. module replacement 为什么有 fingerprint

端口合同或模块接口升级以后，旧工程不能默默继续使用新语义。

fingerprint 用于发现：

```text
这个图是按旧接口创建的
但现在模块合同已经变了
```

这时应该重新加载/保存/审核，而不是偷偷运行。

---

# 5. 什么时候需要读 TaskSpec

日常建模不需要。

建议在以下场景读取：

- 调试平台行为；
- 做自动化测试；
- 接入其他系统；
- 审计生成程序；
- 比较两个图为什么生成不同代码。
