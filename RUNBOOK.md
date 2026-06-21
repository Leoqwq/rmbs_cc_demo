# RMBS Confidential Compute — Oracle DON 端到端测试 Runbook（实测版）

本文是**踩过坑、可复现**的完整链上端到端流程，命令均经过实链联调验证。
对应 spec `docs/superpowers/specs/2026-06-03-rmbs-cc-waterfall-demo-design.md`、
plan `docs/superpowers/plans/2026-06-07-oracle-don-attestation.md`。

**组件在哪儿跑**：TEE 服务跑在 `tee-node`（GCP 机密 VM）；合约部署、oracle agent、提交/读取
CLI 都在**本地 Mac**，经 IAP 隧道连到云上。oracle agent 与 validator 节点**无绑定关系**——
它们是本地进程，只是用各自的 key。

## 步骤分类图例

- **【一次性】** 只需做一次；结果落在磁盘/链上，跨节点 stop/start 持久,**后续复现可跳过**。
- **【每次】** 每次测试都要做（实例、隧道、TEE 服务、agent 都不持久）。
- **【按需】** 仅当代码改了 / 要重新部署合约 / oracle 账户没 gas 时才做。

> **关键认识**：Besu 链状态(已部署的合约、oracle 账户余额、TEE 的签名 key)都**持久在磁盘上**。
> 所以做过一次完整部署后，**日常复现只需「启实例 → 起 TEE → 开隧道 → 起解密节点 → 起 agent → submit/read」**，
> 不必重新部署合约、不必重新充值、不必重新生成密钥。
> ⚠️ **前提是「只 stop/start 且用同一登录用户」**。若 tee-node 被 delete/重建、或**开启 OS Login 换了 SSH 用户名/home**，
> TEE 侧的 venv/代码/`tee/kd` key 会不在新 home 里（磁盘没丢，但 `~` 变了）→ 需重建 + 换新 `TEE_ADDRESS` 重部署。
> validator/Besu 因开机自启不受影响。

---

## ⚡ 复现速查（已完成首次部署后，每次测试照这个走）

```bash
# 1) 启实例（每次）
gcloud compute instances start bootnode-a validator-1 validator-4 --zone=us-central1-a
gcloud compute instances start bootnode-b validator-2 --zone=us-central1-b
gcloud compute instances start validator-3 --zone=us-central1-c   # 必须 ≥3 个 validator 在线
gcloud compute instances start tee-node --zone=us-central1-a

# 2) 起 TEE（每次）：SSH 进 tee-node，TERM=xterm-256color tmux 里 python -m tee.tee_service（见阶段 3）
# 3) 开 2 条隧道（每次）：链 8545 + TEE 8000，全用 127.0.0.1（见阶段 4）
# 3b) 【一次性】密钥设置 + 同步到 TEE：python keygen.py --shares 3 --threshold 2 →
#     scp kd/umbral_state.json 到 tee-node（见阶段 3b；漏同步会让 TEE 报 No such file）
# 4) 起解密节点（每次）：BASE_PORT=5005 python run_decryption_nodes.py（见阶段 5b；避开 macOS AirPlay 的 5000）
# 5) 起 4 个 oracle agent（每次，每个终端；务必先激活 venv）
set -a; source .env; set +a ; source .venv/bin/activate
ORACLE_ID=1 ORACLE_KEY=0x<key1> python oracle_agent.py   # 另 3 个终端同理 key2/3/4
# 6) 提交 + 读（先 source .venv/bin/activate）
python submit_request.py --iaf 500000 --paf 1000000
python read_result.py <返回的 id>     # 期望 finalized=True  attestations=3/3（n>m 时部分 oracle 不 attest 属正常）
# 7) 结束停机（每次）见阶段 9
```
若 `read_result` 一直空 / agent 报 `Known transaction` → 见末尾「故障排查」。

---

## 阶段 0 —【每次】启动云端资源 + 确认出块

> zone 直接写死在命令里（不再 `export ZA=...`，免得每开一个终端都要重设）：
> bootnode-a / validator-1 / validator-4 / tee-node 在 **us-central1-a**，
> bootnode-b / validator-2 在 **us-central1-b**，validator-3 在 **us-central1-c**。
```bash
gcloud compute instances start bootnode-a validator-1 validator-4 --zone=us-central1-a
gcloud compute instances start bootnode-b validator-2 --zone=us-central1-b
gcloud compute instances start validator-3 --zone=us-central1-c
gcloud compute instances start tee-node --zone=us-central1-a
```
**QBFT 4 个 validator 需至少 3 个在线才出块。** 等约 1–2 分钟，确认在出块（隧道已通后本地
`cast block-number --rpc-url http://127.0.0.1:8545` 更直接）：
```bash
gcloud compute ssh validator-1 --zone=us-central1-a --tunnel-through-iap \
  --command='curl -s -X POST -H "Content-Type: application/json" \
  --data "{\"jsonrpc\":\"2.0\",\"method\":\"eth_blockNumber\",\"params\":[],\"id\":1}" \
  http://127.0.0.1:8545 | jq'
```
间隔几秒再查，区块号在涨 = 共识正常。

---

## 阶段 1 —【一次性】tee-node 环境准备

Ubuntu 自带 python 不含 venv/pip：
```bash
gcloud compute ssh tee-node --zone=us-central1-a --tunnel-through-iap
#   --- 在 tee-node 内 ---
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip tmux
```

---

## 阶段 2 —【一次性 / 按需(代码更新时)】把 TEE 代码传到 tee-node

**首次（全量）：**
```bash
# 本地执行
gcloud compute scp --tunnel-through-iap --zone=us-central1-a --recurse \
  /Users/leo/Desktop/rmbs_cc_demo/tee \
  /Users/leo/Desktop/rmbs_cc_demo/abi_digest.py \
  /Users/leo/Desktop/rmbs_cc_demo/requirements.txt \
  tee-node:~/rmbs_cc_demo/
# 节点内建 venv 装依赖
gcloud compute ssh tee-node --zone=us-central1-a --tunnel-through-iap
cd ~/rmbs_cc_demo && python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip && pip install -r requirements.txt
```
> `abi_digest.py` 在仓库**根目录**（TEE 服务 `import abi_digest`），要放到 `~/rmbs_cc_demo/abi_digest.py`。

**【按需】只改了 TEE 相关代码时**（⚠️ **别 `--recurse tee/`**，会用本地 `tee/kd` 覆盖远端签名 key → `TEE_ADDRESS` 变掉）：
```bash
# 根目录文件（umbral_io.py、requirements.txt 都要带上）
gcloud compute scp --tunnel-through-iap --zone=us-central1-a \
  abi_digest.py umbral_io.py requirements.txt tee-node:~/rmbs_cc_demo/
# tee/ 下改过/新增的文件（enclave_keys.py 是加密层新增）
gcloud compute scp --tunnel-through-iap --zone=us-central1-a \
  tee/tee_service.py tee/signing.py tee/encryption_seam.py tee/enclave_keys.py \
  tee-node:~/rmbs_cc_demo/tee/
# 若 requirements.txt 变了（如新增 umbral），远端要重装依赖：
gcloud compute ssh tee-node --zone=us-central1-a --tunnel-through-iap \
  --command='cd ~/rmbs_cc_demo && source .venv/bin/activate && pip install -r requirements.txt'
```
> **若 tee-node 被重建过、或开启 OS Login 换了登录用户**（`~` 变成新的空 home）：远端会**整套缺失**
> （venv、`tee/compute.py`、`engine/` 等都没有，启动会报 `ModuleNotFoundError: tee.compute`）。这时改走「首次（全量）」：
> 重建 venv（`python3 -m venv .venv && pip install -r requirements.txt`）并把**完整 `tee/`** + 根目录
> `abi_digest.py umbral_io.py` 传过去（同样别 `--recurse` 覆盖已有签名 key；若是全新 home 则无所谓）。

---

## 阶段 3 —【每次】在 tee-node 用 tmux 启动 TEE，记下地址

```bash
gcloud compute ssh tee-node --zone=us-central1-a --tunnel-through-iap
#   --- 节点内 ---
tmux new -s tee
cd ~/rmbs_cc_demo && source .venv/bin/activate && python -m tee.tee_service
#   记下 "TEE signing address: 0x..."；Ctrl-b d 脱离
curl -s http://127.0.0.1:8000/tee_address     # 自测：{"success":true,"address":"0x..."}
curl -s http://127.0.0.1:8000/enclave_pubkey  # 自测：{"success":true,"pubkey":"..."}
```
> TEE 签名 key 持久在 `tee/kd/`，**只 stop/start 且用同一登录用户时**地址不变 → 与已部署合约里的
> `teeAddress` 一致，不必重部署。**首次部署**时把这个地址填进 `.env` 的 `TEE_ADDRESS`。
> ⚠️ 但若节点被 **delete/重建**、或开启 **OS Login 换了登录用户名/home**，旧 `tee/kd` 不在新 home 里 →
> 启动会生成**新地址** → 必须更新 `.env` 的 `TEE_ADDRESS` 并**用新地址重部署合约**。
> 想免掉这折腾：把 `tee/kd/tee_signing_key.json` + `tee/kd/enclave_enc_key.json` 备份到本地，重建后拷回即可复现同一地址。

---

## 阶段 3b —【一次性】密钥设置（Key setup）

> 仅首次做。`kd/umbral_state.json` 持久在本地磁盘，之后复现可跳过。
> 例外：① `tee/kd/enclave_enc_key.json` 被删除或重新生成（kfrag 绑定了 enclave 公钥）；
> ② **enclave 公钥变了**（节点重建/换 home → enclave key 重新生成）——都要重跑本阶段。

确认隧道已通（见阶段 4），`GET /enclave_pubkey` 能响应后执行：
```bash
source .venv/bin/activate
python keygen.py --shares 3 --threshold 2
# --shares = 解密节点数（须等于 DECRYPTION_NODE_URLS 列表长度，不一定等于 oracle 数）
# --threshold = umbral 重加密门限 m（≠ oracle 法定人数）
# 成功后写入 kd/umbral_state.json（master/authority/enclave 公钥 + 每个节点的 kfrag）
```
> ⚠️ **keygen 之后必须把 `kd/umbral_state.json` 同步到 TEE 节点**（原 RUNBOOK 漏了这步）：TEE 的
> `/compute` 要读它里面的公钥+threshold 来验证 cfrag 并解密；没同步会报
> `No such file or directory: …/kd/umbral_state.json`。
> ```bash
> gcloud compute ssh tee-node --zone=us-central1-a --tunnel-through-iap \
>   --command='mkdir -p ~/rmbs_cc_demo/kd'
> gcloud compute scp --tunnel-through-iap --zone=us-central1-a \
>   kd/umbral_state.json tee-node:~/rmbs_cc_demo/kd/umbral_state.json
> ```
> 无需重启 TEE（`/compute` 每次请求都重读 state，下一次请求即生效）。
> （安全注记：TEE 其实只需公钥+threshold；本 demo 直接整份拷过去含 kfrag，属已知简化。）
> **两个互相独立的门限，别混淆：**
> - `--shares` = 实际启动的解密节点数（须等于 `DECRYPTION_NODE_URLS` 列表长度）。
> - `--threshold` = **解密 DON 的重加密门限 m**（开启 enclave 解密所需的最少 cfrag 数），
>   写进 `umbral_state.json`，由 TEE / oracle 用来校验 cfrag 数量。
> 它**不是** `.env` 的 `THRESHOLD`——后者是 **oracle attestation 的法定人数**（m-of-n 签名），
> 走的是合约 `threshold`，与重加密门限毫无关系。本例两者取值也不同：
> umbral 门限=2（`--shares 3 --threshold 2`），oracle 法定人数=3（`.env THRESHOLD=3`，n=4）。

---

## 阶段 4 —【每次】建隧道（关键：全用 `127.0.0.1`，不要 `localhost`）

不用 `localhost` 的原因：① Besu RPC `host-allowlist` 只放行 `127.0.0.1`（否则 `403`）；
② TEE 转发里 `localhost` 在节点侧可能解析成 IPv6 `::1`，而 uvicorn 只听 IPv4 → `Connection refused`。

```bash
# T-链：RPC 隧道
gcloud compute start-iap-tunnel validator-1 8545 \
  --local-host-port=127.0.0.1:8545 --zone=us-central1-a
# T-TEE：端口转发（IPv4 目标 + 保活）
gcloud compute ssh tee-node --zone=us-central1-a --tunnel-through-iap \
  -- -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 8000:127.0.0.1:8000
```
> 4 个本地 oracle agent **共用这 1 条链隧道 + 1 条 TEE 隧道**即可。验证：
> `curl -s http://127.0.0.1:8000/tee_address` 返回地址即通。

---

## 阶段 5 —【一次性 / 按需】生成 oracle 密钥、部署 DON 合约、充值

> 这一整段在**首次部署**时做一次。链状态持久，之后复现可全跳过——除非：改了合约要重新部署、
> 或换了 oracle 账户/账户 gas 用尽要重新充值。

**5.1【一次性】生成 n=4 个 oracle 密钥**（生成后存好私钥，长期复用）：
```bash
cast wallet new      # 跑 4 次，记录 4 个 address 和 private key
```
填入 `.env`：
```bash
RPC_URLS=http://127.0.0.1:8545          # 做 failover 再加 8546/8547
RPC_URL=http://127.0.0.1:8545
CHAIN_ID=20260416
DEPLOYER_PRIVATE_KEY=0x<创世账户 0xcbA2…dc69 的私钥>
TEE_URL=http://127.0.0.1:8000
TEE_ADDRESS=0x<阶段 3 打印的地址>
ORACLE_ADDRESSES=0xOracle1,0xOracle2,0xOracle3,0xOracle4
THRESHOLD=3
CONTRACT_ADDRESS=                        # 部署后回填
```

**5.2【按需】部署 DON 合约**（不要 `--gas-price 0`）：
```bash
set -a; source .env; set +a
forge build      # 确保 out/ 是最新 ABI
forge script script/Deploy.s.sol:Deploy --rpc-url "$RPC_URL" --broadcast --legacy
# 记下 "ConfidentialCompute deployed at: 0x..."，回填 .env 的 CONTRACT_ADDRESS
set -a; source .env; set +a      # 回填后重新 source
cast call "$CONTRACT_ADDRESS" "oracleCount()(uint256)" --rpc-url "$RPC_URL"   # → 4
cast call "$CONTRACT_ADDRESS" "threshold()(uint256)"  --rpc-url "$RPC_URL"    # → 3
cast call "$CONTRACT_ADDRESS" "teeAddress()(address)" --rpc-url "$RPC_URL"    # == TEE 地址
```

**5.3【一次性 / 按需】给 oracle 账户充 gas**（⚠️ 漏了这步 → attest 交易卡 mempool、`read_result` 空）：
```bash
source .venv/bin/activate
python fund_oracles.py
# 校验任一 oracle 有余额：
cast balance 0xOracle1 --rpc-url "$RPC_URL"
```
> 余额在链上持久。只要 oracle 账户不变且 gas 没用尽，**复现时不必再充**。

---

## 阶段 5b —【每次】启动解密节点（Decryption DON）

> ⚠️ **macOS 的 AirPlay 接收器（ControlCenter）默认占用端口 5000** → 默认从 5000 起会
> `address already in use`。用 `BASE_PORT` 避开（5005 起）。
```bash
source .venv/bin/activate
BASE_PORT=5005 python run_decryption_nodes.py
# 每个 kfrag 对应一个进程，依次绑定 5005, 5006, 5007, ...
# 每个节点提供 POST /reencrypt，不发链上交易，无需充值
```
确认 `.env` 中已设置（端口数须与 `--shares` 一致、且与上面 BASE_PORT 对齐）：
```bash
DECRYPTION_NODE_URLS=http://127.0.0.1:5005,http://127.0.0.1:5006,http://127.0.0.1:5007
```
验证：节点只暴露 `POST /reencrypt`（无 `/health`）。确认进程在线可用
`curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5005/docs`（FastAPI 默认返回 `200`），
或检查 `run_decryption_nodes.py` 打印的 PID。

---

## 阶段 6 —【每次】启动 4 个 oracle agent

各开一个终端，`ORACLE_ID`/`ORACLE_KEY` 内联传（每个不同）：
```bash
set -a; source .env; set +a
source .venv/bin/activate
ORACLE_ID=1 ORACLE_KEY=0x<key1> python oracle_agent.py
# 另外三个终端：ORACLE_ID=2/3/4 ORACLE_KEY=0x<key2/3/4>
```
每个应打印（核对 `contract`、`expect <TEE地址>`、`Decryption nodes:[...] (threshold m)` 都对）：
`Oracle agent 'k' up. addr=0x... contract=0x... TEE=... (expect 0x...) Decryption nodes:[...] (threshold 2) Resuming from block ...`。

---

## 阶段 7 —【每次】提交请求 + 读结果

```bash
python submit_request.py --iaf 500000 --paf 1000000      # → Request submitted: id=N
```
> `submit_request.py` 在客户端加密输入（pyUmbral）后调用 `submitRequest(capsule, ciphertext)`；
> 明文 iaf/paf **不上链**。

各 agent 依次打印（顺序不定）`>>> [oracle 0x...] ComputeRequested id=N ... attested ok tx=0x...`；
≥3 个完成后合约触发 `ResultPosted`。
> **m-of-n 注记**：n=4 个 oracle 只有 m=3 个会真正 attest；第 4 个在凑齐 quorum 后发现
> `getResult` 已 `finalized`，按设计**跳过、不打印 `attested ok`**——这是正确行为，不是故障。

然后：
```bash
python read_result.py N
# 期望：finalized=True  attestations=3/3 (DON quorum)
#       ClassA 79,000,000 / B 15,000,000 / C 5,000,000 / IAF 70,833.33
```
**机密性验收（确认链上存的是密文、明文 iaf/paf 没上链）**：拿到正确结果只能*隐式*
证明解密成功；要*显式*证明输入保密，直接读回该请求在链上的 `capsule/ciphertext` 字节——
它们应是随机密文，且明文 JSON（dealId / iaf / paf）的 ascii 不出现在其中：
```bash
RAW=$(cast call "$CONTRACT_ADDRESS" \
  "requests(uint256)(bytes,bytes,address,bool,bool,bytes32,string,uint256)" N \
  --rpc-url "$RPC_URL")
echo "$RAW" | head -2     # 头两行 = capsule / ciphertext 的十六进制（随机字节，
                          # 长度与 submit_request 打印的 "capsule NB, ciphertext NB" 一致）
# 若加密被绕过、明文 JSON 直接上链，下面会命中 ascii 串；加密正常则查不到：
echo "$RAW" | grep -iq "353030303030\|544553545f534551" \
  && echo "!! 明文泄露" || echo "OK: 链上是密文，无明文 iaf/dealId"
# 353030303030 = ascii "500000"(iaf 值)，544553545f534551 = ascii "TEST_SEQ"(dealId)
```
**验收**：`python -m pytest tests/ -q`（29 passed）——链上数值与本地引擎一致 = spec §8 闭环。

---

## 阶段 8 —【可选】鲁棒性演练

**A. DON 容错（测的是 oracle，不是 validator）** — Ctrl-C **一个 oracle agent**，再 `submit_request`
一次 → 剩 3 个仍达成 `attestations=3/3 finalized=True`（m=3/n=4 容忍 1 个 oracle 掉线）。

**B. 链容错** — 关掉**一个 validator**（保持 ≥3 在线），链仍出块、流程照常（QBFT 容忍 1 个）。
> 注意 A 和 B 是**不同的层**：agent 与 validator 没有绑定。

**C. RPC failover** — 多开 8546/8547 两条隧道、`.env` 的 `RPC_URLS` 列三个，断掉链隧道之一 →
agent 自动切换可用 RPC。

**D. 幂等 + 断点续跑** — 停掉 agent，`submit_request`，再重启 agent → 从持久化区块续扫补处理，
已 attest 的查 `hasAttested`/`getResult` 跳过、不重复上链。

**E. 解密 DON 容错（测的是重加密层，对应 A 的加密侧）** — 两种演练（umbral 门限 m=2、n=3 个解密节点）：
- **坏节点容错**：让一个解密节点带 `CORRUPTED=1` 启动（翻转 cfrag 首字节），再 `submit_request` →
  oracle 的 `verify_cfrags` 丢弃这片伪造 cfrag，剩 2 片达门限，流程仍 `finalized=True`。
  ```bash
  # 用 run_decryption_nodes 起好后，单独再起一个坏节点覆盖某端口，或单进程演示：
  KFRAG=<node0 的 kfrag base64> CORRUPTED=1 \
    uvicorn decryption_node:app --host 0.0.0.0 --port 5005
  ```
- **低于门限会停滞并自愈**：Ctrl-C 掉解密节点至**仅剩 1 个**（< m=2）→ oracle 刷
  `only 1/2 valid cfrags; retry next loop`、不上链；补回第 2 个节点后下一轮自动补完、`finalized=True`。
> 注意 E 与 A 是不同层：A 容忍 oracle（attestation 层）掉线，E 容忍解密节点（重加密层）坏/掉线，
> 二者门限也不同（oracle 法定人数 vs umbral 重加密门限，见阶段 3b）。

---

## 阶段 9 —【每次】收尾控成本

```bash
gcloud compute instances stop bootnode-a validator-1 validator-4 tee-node --zone=us-central1-a
gcloud compute instances stop bootnode-b validator-2 --zone=us-central1-b
gcloud compute instances stop validator-3 --zone=us-central1-c
```

---

## 故障排查

- **`read_result` 一直空 / agent 刷 `-32000 Known transaction`** → **oracle 账户没 gas**。
  attest 交易进了 mempool 但无法被打包，agent 反复重发同一笔。修复：`python fund_oracles.py`，
  用 `cast balance <oracle>` 确认有余额；链恢复后 agent 会自动补完、自愈，无需重新 submit。
- **交易一直 pending / 链不出块** → 在线 validator < 3。QBFT 4 节点需 3 个出块；确认 1/2/4
  都 `RUNNING`：`gcloud compute instances describe <v> --zone=<z> --format="value(status)"`。
- **关了 validator，oracle agent 却还在跑** → 正常：agent 是本地进程，与 validator 无绑定。
  测 DON 容错请 Ctrl-C **agent**（阶段 8A）。
- **部署/交易 `-32009 Gas price below minimum`** → 别用 `--gas-price 0`；forge 用 `--legacy`，
  脚本已用 `w3.eth.gas_price`。
- **`403 Host not authorized` / TEE `Connection refused`** → 用 `127.0.0.1` 而非 `localhost`；
  TEE 转发用 `-L 8000:127.0.0.1:8000`。
- **改了 `.env` 不生效** → `set -a; source .env; set +a`（`load_dotenv` 默认不覆盖已 export 的值）。
- **TEE 隧道一断 TEE 就没了** → TEE 必须在 `tmux` 里跑，别用 SSH 前台。
- **`FileNotFoundError` / `No such file…kd/umbral_state.json`** → 分两种：① **本地**报错 = 还没跑 `keygen.py`
  或 state 被删（确认 TEE 在跑、隧道已通后执行阶段 3b）；② **TEE 节点**报错（路径含 `/home/<user>/…`）=
  keygen 产物**没同步到 TEE 节点**，按阶段 3b 末尾把 `kd/umbral_state.json` scp 过去。
- **`ModuleNotFoundError: No module named 'umbral'`（或其它依赖）** → 当前终端**没激活 venv**，用成了系统/conda
  `(base)` 的 Python。先 `source .venv/bin/activate`（提示符要看到 `(.venv)`）再跑 node/agent/submit/keygen。
- **`ModuleNotFoundError: No module named 'tee.compute'`（TEE 节点）** → 远端 `tee/` 不完整（节点重建/换 home 后只补传了部分文件）。
  按阶段 2「全量缺失」处理：重建 venv + 补全 `tee/` 和根目录 `umbral_io.py`。
- **解密节点 `address already in use`（端口 5000）** → macOS AirPlay 接收器占用 5000。用 `BASE_PORT=5005 python run_decryption_nodes.py`，
  并把 `.env` 的 `DECRYPTION_NODE_URLS` 同步成 5005–5007。
- **`tmux: missing or unsuitable terminal: xterm-ghostty`** → 远端 terminfo 不认本地 Ghostty 的 TERM。
  用 `TERM=xterm-256color tmux new -s tee`。
- **TEE「像被清空」（venv/代码/签名 key 全无），但你只 stop/start 过** → 多半是**开启了 OS Login**，SSH 登录用户名/home 变了，
  `~` 指向新的空 home（旧文件还在旧 home、磁盘没丢）。开机自启的 validator/Besu 不受影响。对策：始终用同一用户登录、
  备份 `tee/kd/*.json`、tee-node 只 stop/start 勿 delete（详见阶段 2/3 的 ⚠️）。
- **某个 oracle 没打印 `attested ok`** → 正常的 m-of-n：前 m 个达成 quorum 后，多出的 oracle 发现已 finalized 自动跳过（见阶段 7）。
- **oracle log 显示 "only k/m valid cfrags"** → 在线的解密节点少于 `threshold` 个（或某节点设了
  `CORRUPTED=1`）。确认 `run_decryption_nodes.py` 正在运行、端口正常，补齐节点再重试。
- **"bad TEE sig" / TEE 签名验证失败（加密功能上线后）** → 最常见原因：enclave 收到 key 重新生成后
  keygen 未重跑（kfrag 与新 enclave pubkey 不匹配），或 `UMBRAL_STATE` / `.env` 指向了旧的
  state 文件。删除 `kd/umbral_state.json` 并重新执行阶段 3b。

---

## 易踩坑速记

1. 先起 TEE → 拿地址填 `.env` → 再部署。
2. 全用 `127.0.0.1`。
3. 不要 `--gas-price 0`。
4. 改 `.env` 后 `set -a; source .env; set +a`。
5. TEE 用 `tmux` 常驻；隧道加 `ServerAliveInterval`。
6. **新 oracle 账户记得 `fund_oracles.py` 充 gas。**（解密节点无需充值）
7. 每条隧道各占一个终端，别关。
8. **首次运行或 enclave key 重新生成后，记得执行 `keygen.py`（阶段 3b）再起解密节点。**
9. **跑 node/agent/submit/keygen 前先 `source .venv/bin/activate`**（提示符要有 `(.venv)`，别用 conda base）。
10. **keygen 后把 `kd/umbral_state.json` 同步到 TEE 节点**（否则 TEE `/compute` 读不到 → 报 No such file）。
11. 解密节点用 `BASE_PORT=5005`（避开 macOS AirPlay 占用的 5000），`DECRYPTION_NODE_URLS` 同步成 5005–5007。
12. **tee-node 只 stop/start、勿 delete；统一用同一用户登录（OS Login 会换 home）；把 `tee/kd/*.json` 备份到本地。**
13. n>m 时多出的 oracle 不打印 `attested ok` 是正常的（已达 quorum 自动跳过）。

## 路 B — 隧道太不稳时：把 oracle agent 搬进 VPC

若本地 IAP 隧道频繁掉线，让 oracle agent 在对应 validator 主机上跑：连链走 validator 内网 IP
`http://10.20.1.21:8545`（Host 落在 `10.20.0.0/16` 白名单内），连 TEE 走 `http://127.0.0.1:8000`，
两跳都在 VPC 内、无需长隧道。需把 `oracle_agent.py abi_digest.py chain.py`、合约 ABI
（`out/ConfidentialCompute.sol/ConfidentialCompute.json`）和 `.env`（含该 oracle 的
`ORACLE_ID`/`ORACLE_KEY`）拷到节点；人只在提交/读取时开短隧道（或直接在节点上跑 CLI）。
