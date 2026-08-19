# 远程 Worker 部署指南

## 1. 适用场景

远程 Worker 用于：

- 夜间 GPU 服务器；
- 独立高核 CPU 仿真服务器；
- 与控制面隔离的计算节点；
- 批量任务或模型 Provider 节点。

控制面只下发已准备且不可变的 Run Bundle。Worker 不接受任意 Python 源码执行请求。

## 2. 协议流程

```text
Worker 注册
→ 认领任务并获得租约
→ 下载 Prepared Run Bundle ZIP
→ 安全解压并校验
→ 独立子进程执行 Basilisk
→ 生成并密封结果 Bundle
→ 上传结果 ZIP
→ 控制面校验 run_id、计划哈希、SEALED 和制品哈希
→ 写入正式终态
```

## 3. 控制面准备

认证文件中的 Worker Token 必须绑定同名 `worker_id`：

```json
{
  "token_id": "worker-01",
  "principal_id": "worker-01",
  "worker_id": "worker-01",
  "token_env": "SAT_SIM_WORKER_01_TOKEN",
  "roles": ["worker"]
}
```

## 4. Worker 主机安装

```bash
sudo useradd --system --home /opt/sat-sim --shell /usr/sbin/nologin sat-worker
sudo mkdir -p /opt/sat-sim /etc/sat-sim /var/lib/sat-sim-worker
sudo chown -R sat-worker:sat-worker /opt/sat-sim /var/lib/sat-sim-worker
```

复制源码或安装 Wheel：

```bash
sudo -u sat-worker python3 -m venv /opt/sat-sim/.venv
sudo -u sat-worker /opt/sat-sim/.venv/bin/python -m pip install \
  /opt/sat-sim/dist/satellite_simulation_platform-0.5.6.6-py3-none-any.whl
```

若 Wheel 不含完整 SPICE 资产，应配置共享资产目录，或使用完整源码包。

## 5. Worker 环境文件

```bash
sudo cp configs/deployment/remote_worker.env.example /etc/sat-sim/worker.env
sudo chmod 600 /etc/sat-sim/worker.env
```

必须修改：

```text
SAT_SIM_CONTROL_PLANE_URL
SAT_SIM_WORKER_ID
SAT_SIM_WORKER_TOKEN
```

## 6. 前台测试

```bash
export SAT_SIM_WORKER_TOKEN='实际Token'
python -m sat_sim.cli remote-worker \
  --control-plane https://sim.example.internal \
  --worker-id worker-01 \
  --token-env SAT_SIM_WORKER_TOKEN \
  --work-root /var/lib/sat-sim-worker \
  --poll-interval 2 \
  --heartbeat-interval 10 \
  --lease-seconds 60 \
  --max-jobs 1
```

只有开发环境自签名证书排查时才可临时使用：

```text
--insecure-skip-tls-verify
```

正式环境禁止使用该参数。

## 7. systemd

```bash
sudo cp deploy/production/systemd/sat-sim-remote-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sat-sim-remote-worker
sudo systemctl status sat-sim-remote-worker
journalctl -u sat-sim-remote-worker -f
```

## 8. 验收

1. 控制面 Worker 列表出现 `worker-01`；
2. 心跳时间持续更新；
3. 提交 5～20 秒整星任务；
4. 任务状态依次进入 `CLAIMED/RUNNING/SUCCEEDED`；
5. 正式 Run Bundle 存在 `SEALED.json`；
6. `verify-run` 返回完整性通过；
7. Worker 本地临时目录不包含长期保留的明文 Token。
