# cubesandbox-swe 中文说明

本仓库定位为 CubeSandbox 执行 SWE-INFINITE 任务的独立集成层，不直接把
CubeSandbox 和 affinetes 源码合并进主项目维护；它们放在 `third_party/`
并保持 upstream 原样。兼容逻辑应放在 `cubesandbox_swe/` 中，而不是修改依赖。

常用命令：

```sh
cubesandbox-swe --help
cubesandbox-swe doctor
cubesandbox-swe templates prepare --dry-run
cubesandbox-swe templates smoke --dry-run
scripts/cubesandbox-wsl-start.sh
cubesandbox-swe doctor --runtime --runtime-smoke
cubesandbox-swe templates smoke --limit 1
cubesandbox-swe verify \
  --task-json results/swe_infinite_task_1.json \
  --fix-patch results/task1_fix_patch.diff \
  --template-id swe-task1-rubocop-runner-v1
cubesandbox-swe solve --codex-location sandbox --max-verify-attempts 1
```

`results/`、`swe-e2e-runs/` 和 `logs/` 是本地运行产物，体积可能很大，
默认不进入 Git。完整实验结果应发布到 GitHub Release、对象存储或数据集平台，
源码仓库只保留 `artifacts/manifest.json`、schema 和少量脱敏样例。

WSL 本地服务启动脚本保留在：

```sh
scripts/cubesandbox-wsl-start.sh
```

该脚本会补齐 CubeShim 快照恢复所需的 `/data/cube-shim/snapshot` 标志，并为
本机节点写入 Cubemaster metrics。运行真实 smoke 或 e2e 前应先执行它。

默认 `sandbox` solve 会让 Codex 进程留在 host 上访问模型，但所有任务仓库读写、
命令执行和补丁应用都通过 CubeSandbox MCP 工具在 sandbox 的 `/app` 内完成。
任务仓库不会在 host 侧建立 mirror。Codex 二进制和模型 API key 不会复制进任务
sandbox。

verify 路径会真实检查 sandbox 生命周期：创建 sandbox、通过 cubecli 上传 verifier
脚本、调用 upstream `pause()` 暂停、用 `Sandbox.connect()` 重新连接，然后在恢复后
运行 verifier。成功结果应包含：

```text
status=ok
score=1.0
state_after_save=paused
state_after_restore=running
```

solve 会先运行 Codex/model preflight，确认模型端点可用后再创建 sandbox。
只有在已单独确认端点可用时才使用 `--skip-model-preflight`。
可用下面的命令只检查模型端点，不启动 sandbox：

```sh
cubesandbox-swe doctor --model --model-preflight-timeout 60
```

只检查 MCP runtime 到 CubeSandbox `/app` 的执行路径：

```sh
cubesandbox-swe doctor --runtime --codex-runtime-smoke
```

完整验证步骤见英文文档：

```text
docs/validation.md
```
