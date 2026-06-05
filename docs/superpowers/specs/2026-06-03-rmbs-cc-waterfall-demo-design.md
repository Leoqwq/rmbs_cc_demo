# RMBS Confidential Compute Demo — Waterfall 计算管道设计

- **日期**: 2026-06-03
- **状态**: 已确认设计，待写实现计划
- **仓库**: `/Users/leo/Desktop/rmbs_cc_demo`

## 1. 背景与目标

参照 Chainlink Confidential Compute 白皮书 Figure 1 的"high-level architecture and
workflow"，以及现有的 `ccc-demo`（隐私预测市场），构建一个 **RMBS 版的 confidential
compute 演示**。

与 `ccc-demo` 的两点关键差异：

1. **省略加密（confidential）部分**：流程中各节点的 threshold re-encryption / Umbral
   代理重加密全部省略。本 demo 只需把整个流程跑通——用户数据按 Figure 1 的流程流过各环节、
   经过共识、最后由 TEE 返回计算结果。数据**全程明文**。
2. **业务从"预测"换成"waterfall 计算"**：把 `rmbs_platform` 的 waterfall 计算搬进来，
   由 TEE 执行 waterfall，演示 confidential compute 能正确计算 RMBS 瀑布。

**初衷**：验证 `rmbs_platform` 各项业务与 confidential compute 结合的可能性。当下只用
waterfall 验证，因此**不堆复杂逻辑**——只要能证明"confidential compute 可以正常计算
waterfall"即可，其余一切从简。

## 2. 既有资源（已核实）

### 2.1 私有链（共识层，已部署）
- Hyperledger Besu + **QBFT** 共识，6 节点（2 bootnode + 4 validator）。
- `chainId = 20260416`，**零 Gas**（`minGasPrice = 0`）。
- RPC 在 validator 的 `:8545`。
- **网络约束**：链跑在 GCP VPC 内，节点无外网 IP，RPC 仅 VPC 内可达。本地代码需通过
  `gcloud compute start-iap-tunnel validator-1 8545 --local-host-port=localhost:8545`
  把 RPC 转发到本地。
- 创世预存余额账户 `0xcbA2e7205C2A0cA14044a690A776A3D55AB9dc69`；其私钥由用户持有，
  通过配置变量填入（仓库内留空）。
- 合约部署采用 **Foundry（forge script）**。
- 节点当前停用，待实现完成后由用户启用（控制成本）。

### 2.2 Waterfall 引擎（业务层，复用 rmbs_platform）
- 入口：`WaterfallRunner(ExpressionEngine).run_period(state)`，`state = DealState(deal_def)`。
- 输入：deal 定义 + 当期现金流，经 `state.deposit_funds("IAF", interest)` /
  `state.deposit_funds("PAF", principal)` 注入。
- 输出：更新后的各档 bond 余额、利息/本金支付、shortfall。
- **依赖边界（闭合）**：`waterfall.py → compute.py, state.py, audit_trail.py`；
  `compute.py → state.py`；`state.py → loader.py`；`loader.py` 与 `audit_trail.py` 仅依赖
  标准库。即 TEE 服务只需 vendored 这 5 个文件：
  `loader.py, state.py, compute.py, audit_trail.py, waterfall.py`（来自
  `rmbs_platform/engine/`）。
- **内置样例 deal**：直接采用 `rmbs_platform/unit_tests/test_waterfall.py` 中的
  `basic_sequential_deal` fixture——三层 A1/A2/B，IAF/PAF 两个资金池，利息按票面利率、
  本金按顺序/比例偿付，无触发器、无 Net WAC、无损失分配启用。

### 2.3 TEE 机密虚拟机（运算节点，已创建）

TEE 作为 confidential compute 的运算节点，是 GCP 上的一台 **Confidential VM**，与 6 节点
私有链建在**同一 project、同一 VPC** 下。

**配置取舍（思考过程）：**
- 现有 6 节点用的是 `e2-custom-2-6144`（e2 系列），但 **e2 系列不支持机密计算**，所以 TEE
  不能简单"租一个一样大小的"，必须换到支持机密计算的机型。
- 机密计算技术三选一：**AMD SEV-SNP（N2D）** / Intel TDX（C3）/ AMD SEV（旧版）。选用
  **AMD SEV-SNP**——与现有 AMD 友好环境一致、`us-central1` 支持好、价格较低、支持远程证明
  (attestation，本 demo 暂不依赖，留作后续)。
- 机型选 **`n2d-standard-2`（2 vCPU / 8 GB）**——与 validator（2 vCPU/6 GB）最接近的"同等
  大小"机密机型（N2D 最小标准档即 8 GB）。该节点只跑一个轻量 FastAPI 算 waterfall，资源
  绰绰有余；本 demo 中 **TEE 只做计算、不连链**（连链的是本地编排器），故无需大磁盘/高性能。
- 维护策略强制 `TERMINATE`（机密 VM 不支持热迁移）。
- 沿用链节点的安全模型：**无外网 IP**、内网静态 IP、`ubuntu-2204-lts` 镜像、用完即停。

**已创建实例（事实记录）：**
- 名称 `tee-node`，zone `us-central1-a`，机型 `n2d-standard-2`，`--confidential-compute-type=SEV_SNP`，`--min-cpu-platform="AMD Milan"`，`--maintenance-policy=TERMINATE`。
- network `besu-net` / subnet `chain-a`，内网静态 IP **`10.20.1.30`**（避开已用的 .10/.21/.22），无外网 IP。
- 镜像 `ubuntu-2204-lts`，启动盘 30GB `pd-standard`，STATUS = RUNNING。

**连接方式：** 该 VM 无外网 IP，本地编排器经 **IAP SSH 本地端口转发**访问 TEE 服务端口
（8000），复用既有 `allow-ssh-iap`（tcp:22）规则，**无需新增防火墙规则**：
`gcloud compute ssh tee-node --zone=us-central1-a --tunnel-through-iap -- -L 8000:localhost:8000`，
编排器中 TEE 地址即填 `http://localhost:8000`。

完整创建命令与说明见 `/Users/leo/Desktop/Obsidian/RMBS/private_chain/TEE.md`。

## 3. 总体架构与数据流

对照 Figure 1：**Besu 私有链 = 去中心化共识那一环**；TEE 只负责算 waterfall；合约只存
请求/结果，不做任何瀑布计算。加密整条省略，数据全程明文。

```
[本地] submit_request.py
   │  ① 提交明文计算请求 {dealId, period, IAF, PAF}
   ▼
[GCP/Besu] ConfidentialCompute.sol (Application)
   │  QBFT 6 节点共识打包 + emit ComputeRequested 事件
   ▼
[本地] orchestrator.py (Oracle / 编排器)
   │  ② 监听事件 → ③ 明文转发请求给 TEE
   ▼
[云 / 暂本地] tee_service.py (Compute Enclave, FastAPI)
   │  ④ 加载内置 deal → deposit_funds(IAF/PAF) → WaterfallRunner.run_period
   │  ⑤ 返回 {bond_balances, shortfalls, ...} + TEE 私钥签名
   ▼
[本地] orchestrator.py
   │  ⑥ postResult(id, resultHash, resultJson, sig) 回链
   ▼
[GCP/Besu] 合约校验 TEE ECDSA 签名 → 存结果 → emit ResultPosted
```

**Figure 1 角色映射**

| Figure 1 角色 | 本 demo 实现 |
|---|---|
| Users | 本地 `submit_request.py`，提交明文 waterfall 请求 |
| Application | `ConfidentialCompute.sol`，部署在 6 节点 Besu 私有链 |
| Oracle + Decryption nodes（去中心化共识） | Besu 链的 QBFT 共识本身（输入上链由链共识确认并 emit 事件） |
| 编排器 | `orchestrator.py`，监听事件→调 TEE→结果回链 |
| Compute Enclave（TEE） | `tee_service.py`，跑 `WaterfallRunner.run_period`，对结果签名；现本地起，
  将来换云上 URL 只改 config |

## 4. 组件清单

| 文件/目录 | 角色 | 说明 |
|---|---|---|
| `contracts/ConfidentialCompute.sol` | Application | 存请求、emit 事件、校验 TEE ECDSA 签名后存结果。仿 `ccc-demo` 的 `PrivateBetting` 但极简、与业务无关 |
| `script/Deploy.s.sol` | 部署 | Foundry 脚本，部署合约并把 TEE 地址写入合约 |
| `foundry.toml` | 配置 | Foundry 项目配置 |
| `tee/tee_service.py` | Compute Enclave | FastAPI，`POST /compute`；import vendored 引擎跑 waterfall；用 ETH 私钥签名结果 |
| `tee/engine/` | 引擎（vendored） | 从 `rmbs_platform/engine/` 复制的 5 个文件 |
| `tee/sample_deal.py` | 样例 deal | 内置 `basic_sequential_deal` 字典 |
| `orchestrator.py` | Oracle / 编排器 | web3.py 监听 `ComputeRequested`→调 TEE→`postResult` 回链 |
| `submit_request.py` | 用户 | 发交易提交一期现金流 |
| `config.example.toml` / `.env.example` | 配置 | `RPC_URL`、`CHAIN_ID`、部署者私钥（**留空给用户填**）、TEE 地址/私钥、合约地址 |
| `README.md` | 文档 | 启动顺序 + IAP tunnel 命令 |

## 5. 合约接口（极简）

```solidity
event ComputeRequested(
    uint256 id, string dealId, uint256 period,
    uint256 iaf, uint256 paf, address requester
);
event ResultPosted(uint256 id, bytes32 resultHash, string resultJson);

function submitRequest(string dealId, uint256 period, uint256 iaf, uint256 paf)
    returns (uint256 id);

// 校验 ecrecover(ethSignedMessageHash(resultHash)) == teeAddress
function postResult(uint256 id, bytes32 resultHash, string resultJson, bytes sig);

function getResult(uint256 id) view returns (...);
```

- 金额用**整数**（美元，无小数）传，规避 Solidity 浮点问题；TEE 内部按 float 算 waterfall。
- TEE 签名方案沿用 `ccc-demo` 的模式：`eth_account` 对 `resultHash` 做
  `encode_defunct` + `sign_message`，合约侧用 `toEthSignedMessageHash` + `ecrecover` 校验。

## 6. 启动/运行顺序（纯 CLI）

1. 启动两条 IAP 隧道：
   - 链 RPC：`gcloud ... start-iap-tunnel validator-1 8545 --local-host-port=localhost:8545`；
   - TEE 服务：`gcloud compute ssh tee-node --zone=us-central1-a --tunnel-through-iap -- -L 8000:localhost:8000`。
2. `forge script Deploy.s.sol --rpc-url localhost:8545 --broadcast` 部署合约（带入 TEE 地址）。
3. 在 `tee-node` 上运行 `tee/tee_service.py`（监听 8000）。开发期也可先在本地起，联调时切到
   tee-node；编排器只认 `http://localhost:8000`（经隧道）。
4. `python orchestrator.py`（开始监听）。
5. `python submit_request.py --iaf 500000 --paf 1000000` → 终端逐段打印 ①~⑥ 流转，
   最后 `getResult` 打印 TEE 算出的各档余额。

## 7. 明确不做（YAGNI）

- ❌ 任何加密 / threshold re-encryption / Umbral / 多 relay 节点（共识由 Besu 链承担）。
- ❌ 复杂 waterfall 逻辑（触发器、Net WAC、多期、损失分配仅保留引擎自带能力，样例 deal 不启用）。
- ❌ 前端 UI、代币、claim/结算。
- ❌ TEE 硬件隔离（本地进程模拟，逻辑等价；将来部署到云上 TEE 只改 URL）。

## 8. 验证标准

跑通后，链上 `getResult(id)` 返回的各档 bond 余额，应与本地直接调
`WaterfallRunner.run_period`（同输入）的结果**完全一致**——证明"confidential compute（TEE）
能正确计算 waterfall 并把结果可信地（签名校验）写回链"。

## 9. 已知约束 / 待办前置

- 链节点当前停用，部署与联调需用户先启用并建立 IAP tunnel。
- 部署者私钥仅由用户填入配置，仓库内留空。
- TEE 机密 VM（`tee-node` / `10.20.1.30`）已创建但默认停机；联调前需 `instances start` 并建立
  IAP SSH 端口转发（8000）。TEE 的结果签名 ETH 私钥由 TEE 服务自身生成（与 VM 无关），其地址
  在部署时写入合约。
- 链节点与 TEE 节点用完即 `instances stop` 控制成本（仅余磁盘费）。
