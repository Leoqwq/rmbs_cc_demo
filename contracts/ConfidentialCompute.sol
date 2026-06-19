// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title ConfidentialCompute
/// @notice Application contract for the RMBS confidential-compute demo.
///         Stores plaintext compute requests; accepts a result only after the
///         enclave attestation (TEE signature bound to the request) AND an
///         m-of-n oracle DON quorum have been verified on-chain (DON-attested).
contract ConfidentialCompute {
    struct Request {
        bytes capsule;
        bytes ciphertext;
        address requester;
        bool resultStored;        // TEE-attested result recorded
        bool finalized;           // DON quorum reached
        bytes32 resultHash;
        string resultJson;
        uint256 attestationCount;
    }

    address public admin;
    address public teeAddress;
    uint256 public threshold;                 // m
    address[] public oracles;                 // n
    mapping(address => bool) public isOracle;
    uint256 public requestCount;
    mapping(uint256 => Request) public requests;
    mapping(uint256 => mapping(address => bool)) public hasAttested;

    event ComputeRequested(
        uint256 indexed id, bytes capsule, bytes ciphertext, address requester
    );
    event Attested(uint256 indexed id, address indexed oracle, uint256 count);
    event ResultPosted(uint256 indexed id, bytes32 resultHash, string resultJson); // DON-attested

    modifier onlyAdmin() {
        require(msg.sender == admin, "Only admin");
        _;
    }

    constructor(address _teeAddress, address[] memory _oracles, uint256 _threshold) {
        admin = msg.sender;
        teeAddress = _teeAddress;
        require(_threshold > 0 && _threshold <= _oracles.length, "bad threshold");
        threshold = _threshold;
        for (uint256 i = 0; i < _oracles.length; i++) {
            require(!isOracle[_oracles[i]], "dup oracle");
            isOracle[_oracles[i]] = true;
            oracles.push(_oracles[i]);
        }
    }

    function setTEEAddress(address a) external onlyAdmin {
        teeAddress = a;
    }

    function oracleCount() external view returns (uint256) {
        return oracles.length;
    }

    function submitRequest(bytes calldata capsule, bytes calldata ciphertext)
        external
        returns (uint256 id)
    {
        id = ++requestCount;
        Request storage r = requests[id];
        r.capsule = capsule;
        r.ciphertext = ciphertext;
        r.requester = msg.sender;
        emit ComputeRequested(id, capsule, ciphertext, msg.sender);
    }

    /// @notice One oracle's attestation of a TEE result. The first valid call for an
    ///         id records the result and verifies the enclave attestation; every call
    ///         adds the caller's oracle signature. Finalizes at `threshold` oracles.
    /// @param resultJson  required on the first call; ignored afterwards
    /// @param teeSig      required on the first call; enclave signature over the
    ///                    request-bound digest; ignored afterwards
    /// @param oracleSig   this oracle's signature over keccak256(abi.encode(id, resultHash))
    function attest(
        uint256 id,
        bytes32 resultHash,
        string calldata resultJson,
        bytes calldata teeSig,
        bytes calldata oracleSig
    ) external {
        Request storage r = requests[id];
        require(r.requester != address(0), "unknown request");
        require(!r.finalized, "finalized");

        if (!r.resultStored) {
            require(keccak256(bytes(resultJson)) == resultHash, "hash mismatch");
            bytes32 ciphertextHash = keccak256(abi.encodePacked(r.capsule, r.ciphertext));
            bytes32 teeDigest = keccak256(abi.encode(id, ciphertextHash, resultHash));
            require(_recover(_ethSigned(teeDigest), teeSig) == teeAddress, "bad TEE sig");
            r.resultHash = resultHash;
            r.resultJson = resultJson;
            r.resultStored = true;
        } else {
            require(resultHash == r.resultHash, "result mismatch");
        }

        bytes32 oracleDigest = keccak256(abi.encode(id, resultHash));
        address signer = _recover(_ethSigned(oracleDigest), oracleSig);
        require(isOracle[signer], "not an oracle");
        require(!hasAttested[id][signer], "dup attestation");
        hasAttested[id][signer] = true;
        r.attestationCount += 1;
        emit Attested(id, signer, r.attestationCount);

        if (r.attestationCount >= threshold) {
            r.finalized = true;
            emit ResultPosted(id, r.resultHash, r.resultJson);
        }
    }

    function getResult(uint256 id)
        external
        view
        returns (bool finalized, uint256 attestationCount, bytes32 resultHash, string memory resultJson)
    {
        Request storage r = requests[id];
        return (r.finalized, r.attestationCount, r.resultHash, r.resultJson);
    }

    function _ethSigned(bytes32 h) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", h));
    }

    function _recover(bytes32 hash, bytes memory sig) internal pure returns (address) {
        require(sig.length == 65, "bad sig length");
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
        require(v == 27 || v == 28, "bad v");
        return ecrecover(hash, v, r, s);
    }
}
