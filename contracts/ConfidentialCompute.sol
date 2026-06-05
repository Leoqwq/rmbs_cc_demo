// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title ConfidentialCompute
/// @notice Minimal application contract for the RMBS confidential-compute demo.
///         Stores plaintext compute requests, emits events for the orchestrator,
///         and stores results only after verifying a TEE ECDSA signature over
///         keccak256(resultJson). The contract never computes the waterfall.
contract ConfidentialCompute {
    struct Request {
        string dealId;
        uint256 period;
        uint256 iaf;
        uint256 paf;
        address requester;
        bool resultPosted;
        bytes32 resultHash;
        string resultJson;
    }

    address public admin;
    address public teeAddress;
    uint256 public requestCount;
    mapping(uint256 => Request) public requests;

    event ComputeRequested(
        uint256 indexed id,
        string dealId,
        uint256 period,
        uint256 iaf,
        uint256 paf,
        address requester
    );
    event ResultPosted(uint256 indexed id, bytes32 resultHash, string resultJson);

    modifier onlyAdmin() {
        require(msg.sender == admin, "Only admin");
        _;
    }

    constructor(address _teeAddress) {
        admin = msg.sender;
        teeAddress = _teeAddress;
    }

    function setTEEAddress(address _teeAddress) external onlyAdmin {
        teeAddress = _teeAddress;
    }

    function submitRequest(
        string calldata dealId,
        uint256 period,
        uint256 iaf,
        uint256 paf
    ) external returns (uint256 id) {
        id = ++requestCount;
        Request storage r = requests[id];
        r.dealId = dealId;
        r.period = period;
        r.iaf = iaf;
        r.paf = paf;
        r.requester = msg.sender;
        emit ComputeRequested(id, dealId, period, iaf, paf, msg.sender);
    }

    function postResult(
        uint256 id,
        bytes32 resultHash,
        string calldata resultJson,
        bytes calldata sig
    ) external {
        Request storage r = requests[id];
        require(r.requester != address(0), "Unknown request");
        require(!r.resultPosted, "Already posted");
        require(keccak256(bytes(resultJson)) == resultHash, "Hash mismatch");

        bytes32 ethHash = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", resultHash)
        );
        require(_recover(ethHash, sig) == teeAddress, "Invalid TEE signature");

        r.resultPosted = true;
        r.resultHash = resultHash;
        r.resultJson = resultJson;
        emit ResultPosted(id, resultHash, resultJson);
    }

    function getResult(uint256 id)
        external
        view
        returns (bool posted, bytes32 resultHash, string memory resultJson)
    {
        Request storage r = requests[id];
        return (r.resultPosted, r.resultHash, r.resultJson);
    }

    function _recover(bytes32 hash, bytes memory sig) internal pure returns (address) {
        require(sig.length == 65, "Bad sig length");
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := mload(add(sig, 32))
            s := mload(add(sig, 64))
            v := byte(0, mload(add(sig, 96)))
        }
        if (v < 27) {
            v += 27;
        }
        require(v == 27 || v == 28, "Bad v");
        return ecrecover(hash, v, r, s);
    }
}
