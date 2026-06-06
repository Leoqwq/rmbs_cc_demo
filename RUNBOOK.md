# RMBS Confidential Compute — 端到端测试 Runbook（实测版）

本文是**踩过坑、可复现**的完整链上端到端流程，命令均经过一次成功的实链联调验证。
对应 spec `docs/superpowers/specs/2026-06-03-rmbs-cc-waterfall-demo-design.md`
和实现 plan `docs/superpowers/plans/2026-06-05-rmbs-cc-waterfall-demo.md` 的 Task 8。

**组件在哪儿跑**：TEE 服务跑在 `tee-node`（GCP 机密 VM）；合约部署、编排器、提交/读取
CLI 都在**本地 Mac**，经 IAP 隧道连到云上。

> 前置（本地）：`pip install -r requirements.txt`、`forge install foundry-rs/forge-std`、
> `forge build` 已完成；`.env` 由 `.env.example` 复制而来待填。开多个终端，标注 T1/T2/…；
> 带 `start-iap-tunnel` / `ssh -L` 的终端是长驻的，别关。

---

## 阶段 0 — 启动云端资源（T1）

```bash
export ZA=us-central1-a ZB=us-central1-b ZC=us-central1-c
gcloud compute instances start bootnode-a validator-1 validator-4 --zone=$ZA
gcloud compute instances start bootnode-b validator-2 --zone=$ZB
gcloud compute instances start validator-3 --zone=$ZC
gcloud compute instances start tee-node --zone=$ZA
```

等约 1–2 分钟（节点 systemd 自启 Besu），确认在出块：
```bash
gcloud compute ssh validator-1 --zone=$ZA --tunnel-through-iap \
  --command='curl -s -X POST -H "Content-Type: application/json" \
  --data "{\"jsonrpc\":\"2.0\",\"method\":\"eth_blockNumber\",\"params\":[],\"id\":1}" \
  http://127.0.0.1:8545 | jq'
```
间隔几秒再查，区块号在涨 = 共识正常。

---

## 阶段 1 — tee-node 一次性环境准备（仅首次）

Ubuntu 自带 python 不含 venv/pip，先装（tee-node 有 Cloud NAT 可联网）：
```bash
gcloud compute ssh tee-node --zone=$ZA --tunnel-through-iap
#   --- 在 tee-node 内 ---
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip tmux
```

把 TEE 代码拷上去（**别把本地 tee/kd 旧密钥带上**；没有就忽略）：
```bash
# 本地执行
gcloud compute scp --tunnel-through-iap --zone=$ZA --recurse \
  /Users/leo/Desktop/rmbs_cc_demo/tee \
  /Users/leo/Desktop/rmbs_cc_demo/requirements.txt \
  tee-node:~/rmbs_cc_demo/
```
在 tee-node 内建 venv 装依赖：
```bash
cd ~/rmbs_cc_demo
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 阶段 2 — 在 tee-node 用 tmux 常驻 TEE 服务

**用 tmux**，这样 SSH/隧道掉线也不会杀掉 TEE：
```bash
# 在 tee-node 内
tmux new -s tee
cd ~/rmbs_cc_demo && source .venv/bin/activate && python -m tee.tee_service
#   按 Ctrl-b 再按 d 脱离；服务继续后台跑
```
启动日志里记下：
```
TEE signing address: 0x....    ← 部署合约要用
```
节点内自测（确认监听 IPv4）：
```bash
curl -s http://127.0.0.1:8000/tee_address     # → {"success":true,"address":"0x..."}
```
> 想让地址可复现：启动前 `export TEE_PRIVATE_KEY=0x...`；否则在 `tee/kd/` 自动生成并持久化
> （重启会复用同一把，地址不变）。

---

## 阶段 3 — 建隧道（关键：全部用 `127.0.0.1`，不要 `localhost`）

为什么不用 `localhost`：① Besu 的 RPC `host-allowlist` 只放行 `127.0.0.1`，用 `localhost`
会 `403 Host not authorized`；② TEE 转发里 `localhost` 在节点侧可能解析成 IPv6 `::1`，而
uvicorn 只听 IPv4 `0.0.0.0`，导致 `connect failed: Connection refused`。

**T2 — 链 RPC 隧道：**
```bash
gcloud compute start-iap-tunnel validator-1 8545 \
  --local-host-port=127.0.0.1:8545 --zone=us-central1-a
```

**T3 — TEE 端口转发（IPv4 目标 + SSH 保活）：**
```bash
gcloud compute ssh tee-node --zone=us-central1-a --tunnel-through-iap \
  -- -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 8000:127.0.0.1:8000
```

本地验证两条都通：
```bash
curl -s http://127.0.0.1:8000/tee_address     # TEE 通
# 链稍后由部署/编排器验证
```

> 若 IAP 隧道频繁掉线（websocket 重连失败、Broken pipe），见末尾「路 B」：把编排器也搬进 VPC，
> 彻底不依赖长隧道。

---

## 阶段 4 — 填 `.env` 并部署合约（T5，本地仓库根）

编辑 `.env`（注意全用 `127.0.0.1`）：
```bash
RPC_URLS=http://127.0.0.1:8545          # 最小路径先填一条；做 failover 再加 8546/8547
RPC_URL=http://127.0.0.1:8545
CHAIN_ID=20260416
DEPLOYER_PRIVATE_KEY=0x<创世账户 0xcbA2…dc69 的私钥>
TEE_URL=http://127.0.0.1:8000
TEE_ADDRESS=0x<阶段 2 打印的 TEE 地址>
CONTRACT_ADDRESS=                        # 部署后回填
```

部署（**不要 `--gas-price 0`**——这条链的 validator 没设 `--min-gas-price=0`，零 gas 交易会被
`-32009` 拒绝；forge legacy 会自动用节点 gas price）：
```bash
set -a; source .env; set +a              # export，让 forge 读到
forge script script/Deploy.s.sol:Deploy --rpc-url "$RPC_URL" --broadcast --legacy
```
输出里记下 `ConfidentialCompute deployed at: 0x...`，回填到 `.env` 的 `CONTRACT_ADDRESS`。

**两项校验：**
```bash
set -a; source .env; set +a
cast code "$CONTRACT_ADDRESS" --rpc-url "$RPC_URL"               # 返回一长串字节码 = 真上链了
cast call "$CONTRACT_ADDRESS" "teeAddress()(address)" --rpc-url "$RPC_URL"   # 应 == TEE 地址
```
> 若 `teeAddress` 与 TEE 实际地址不一致（比如换了 TEE key），不用重部署，用 admin 改：
> ```bash
> cast send "$CONTRACT_ADDRESS" "setTEEAddress(address)" <TEE地址> \
>   --rpc-url "$RPC_URL" --private-key "$DEPLOYER_PRIVATE_KEY" --legacy
> ```

---

## 阶段 5 — 跑通主流程

**T6 — 编排器**（本地，venv 激活；改过 `.env` 后务必重新 `source`）：
```bash
set -a; source .env; set +a
source .venv/bin/activate
python orchestrator.py
```
预期：
```
Orchestrator up. chain_id=20260416 contract=0x...
Admin=0xcbA2...dc69  TEE=http://127.0.0.1:8000  RPCs=['http://127.0.0.1:8545']
Resuming from block 0, 0 requests already completed.
```

**T7 — 提交请求 + 读结果：**
```bash
source .venv/bin/activate
python submit_request.py --iaf 500000 --paf 1000000      # → Request submitted: id=1
```
T6 编排器应打印：`forwarding to TEE... → TEE result {...} → postResult ok tx=0x...`。
（`postResult ok` 即合约 `ecrecover==teeAddress` 验签通过。）然后：
```bash
python read_result.py 1
```
预期 `posted=True`，`resultJson` 解析出：
```
ClassA 79,000,000 / ClassB 15,000,000 / ClassC 5,000,000
cash_remaining: IAF 70,833.33 / PAF 0.0 / RESERVE 0.0
```

---

## 阶段 6 — 验收（链上结果 == 本地引擎）

```bash
python -m pytest tests/ -q       # 14 passed —— 测试里写死的就是上面这组数
```
链上 `getResult(1)` 的各档余额与本地 `WaterfallRunner.run_period` 同输入逐字节一致 →
**证明 confidential compute 正确计算了 waterfall 并把签名校验过的结果写回了链**（spec §8 闭环）。

---

## 阶段 7 —（可选）鲁棒性演练

**A. RPC failover（#2）** — 先多开两条隧道，`.env` 改
`RPC_URLS=http://127.0.0.1:8545,http://127.0.0.1:8546,http://127.0.0.1:8547`，重启编排器：
```bash
gcloud compute start-iap-tunnel validator-2 8545 --local-host-port=127.0.0.1:8546 --zone=us-central1-b
gcloud compute start-iap-tunnel validator-3 8545 --local-host-port=127.0.0.1:8547 --zone=us-central1-c
```
运行中断掉 T2（validator-1 隧道），再 `submit_request` 一次 → 编排器应打印
`[failover] switched RPC -> http://127.0.0.1:8546` 并照常处理完。（QBFT 容忍 1 个 validator 掉线。）

**B. 幂等 + 断点续跑（#3）** — 编排器 Ctrl-C 停掉，再 `submit_request`（此刻没人处理），然后
重启 `python orchestrator.py`。它从持久化区块续扫、补处理挂起请求；已完成的请求查 `getResult`
跳过、不重复上链。可查看 `orchestrator_state.json` 的 `last_scanned_block` / `completed_ids`。

---

## 阶段 8 — 收尾控成本

```bash
gcloud compute instances stop bootnode-a validator-1 validator-4 tee-node --zone=us-central1-a
gcloud compute instances stop bootnode-b validator-2 --zone=us-central1-b
gcloud compute instances stop validator-3 --zone=us-central1-c
```

---

## 易踩的坑（实测总结）

1. **顺序**：先起 TEE → 拿地址填 `.env` → 再部署（合约把 TEE 地址固化进去）。
2. **全用 `127.0.0.1`**：`localhost` 会触发 Besu 403 / IPv6 连接被拒。
3. **不要 `--gas-price 0`**：链非零 gas；forge 用 `--legacy`，脚本用 `w3.eth.gas_price`。
4. **改完 `.env` 必须 `set -a; source .env; set +a`**：否则旧值（含空字符串）残留在 shell，
   `load_dotenv()` 默认不覆盖，会读到旧值。
5. **TEE 用 tmux/nohup 常驻**：别在 SSH 前台跑，否则隧道一断 TEE 就被杀。
6. **TEE 隧道目标用 `-L 8000:127.0.0.1:8000`**，并加 `ServerAliveInterval` 保活。
7. **隧道终端别关**：每条 `start-iap-tunnel` / `ssh -L` 各占一个终端。

## 路 B — 隧道太不稳时：把编排器搬进 VPC

若本地 IAP 隧道频繁掉线，让编排器也在 `tee-node` 上跑：连链走 validator 内网 IP
`http://10.20.1.21:8545`（Host 落在 `10.20.0.0/16` 白名单内），连 TEE 走 `http://127.0.0.1:8000`，
两跳都在 VPC 内、无需长隧道。需把 `orchestrator.py chain.py submit_request.py read_result.py`、
合约 ABI（`out/ConfidentialCompute.sol/ConfidentialCompute.json`）和 `.env` 一并拷到节点；人只在
提交/读取时开短隧道（或直接在节点上敲）。
