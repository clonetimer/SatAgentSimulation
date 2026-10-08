# 从最初 SAT source-only 工程升级到 Visual Composer V15

## 包大小为什么不同

`Visual Composer V15` 是源码树，不重复打包离线 Python wheel。最初会话中：

- `sat-platform-caas-source-only.zip`：约 4.7 MiB；
- `sat-platform-visual-composer-v15.zip`：约 5 MiB 量级（源码增加后略大）；
- Basilisk `bsk-2.11.0...whl` 单独约 86 MiB；
- Pillow wheel 单独约 5.8 MiB；FastAPI、SGP4、colorama 等也单独保存。

因此不要用“源码 ZIP 大小”判断是否包含 Basilisk runtime。V15 保持源码与离线依赖分离。

## 推荐安装方式

### 方式 A：新目录运行（最稳妥）

1. 保留原 SAT 项目作为备份；
2. 将 `sat-platform-visual-composer-v15.zip` 解压为新的项目根目录；
3. 原来的 wheel 文件继续放在独立 wheelhouse/安装目录，不必复制进源码树；
4. 按原工程的依赖安装方式安装这些 wheel 后，从 V15 根目录启动。

不要把整个 `sat-platform-visual-composer-v15/` 再作为一个子目录塞进旧项目根目录。

### 方式 B：直接升级你最初上传的 source-only 工程

使用累计补丁：

```bash
cd /path/to/your/sat-project
patch -p1 < sat-platform-original-to-v15.patch
```

或者使用 `sat-platform-original-to-v15-overlay.zip`，将其中 `overlay/` 下的文件按原相对路径覆盖到项目根目录。

累计升级只修改/新增 Visual Composer 所需的源码、Capability 合同与测试；不包含 wheel，也不删除原工程文件。

## 有本地修改时

如果你已经手工改过同一批文件（例如 `src/sat_sim/api.py`、`src/sat_sim/web/app.js`、Capability YAML），不要直接无备份覆盖。优先在 Git 分支中应用补丁并处理冲突。
