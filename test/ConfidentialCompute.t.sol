// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {ConfidentialCompute} from "../contracts/ConfidentialCompute.sol";

contract ConfidentialComputeTest is Test {
    ConfidentialCompute cc;

    uint256 teePk = 0xA11CE;
    address tee;
    // three oracle keys (n=4 registered, threshold m=3; 4th used for negative test)
    uint256[4] oraclePks = [uint256(0xAA01), 0xAA02, 0xAA03, 0xAA04];
    address[] oracles;

    string constant DEAL = "TEST_SEQ_2024";
    string constant RJSON = '{"period":1}';

    function setUp() public {
        tee = vm.addr(teePk);
        for (uint256 i = 0; i < 4; i++) {
            oracles.push(vm.addr(oraclePks[i]));
        }
        cc = new ConfidentialCompute(tee, oracles, 3);
    }

    function _newRequest() internal returns (uint256 id) {
        id = cc.submitRequest(DEAL, 1, 500000, 1000000);
    }

    function _eth(bytes32 h) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", h));
    }

    function _teeSig(uint256 id, bytes32 rh) internal view returns (bytes memory) {
        bytes32 d = keccak256(abi.encode(id, DEAL, uint256(1), uint256(500000), uint256(1000000), rh));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(teePk, _eth(d));
        return abi.encodePacked(r, s, v);
    }

    function _oracleSig(uint256 pk, uint256 id, bytes32 rh) internal pure returns (bytes memory) {
        bytes32 d = keccak256(abi.encode(id, rh));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, _eth(d));
        return abi.encodePacked(r, s, v);
    }

    function test_QuorumFinalizesAtThreshold() public {
        uint256 id = _newRequest();
        bytes32 rh = keccak256(bytes(RJSON));
        bytes memory teeSig = _teeSig(id, rh);

        cc.attest(id, rh, RJSON, teeSig, _oracleSig(oraclePks[0], id, rh));
        (bool fin1,,,) = cc.getResult(id);
        assertFalse(fin1); // 1 of 3

        cc.attest(id, rh, "", "", _oracleSig(oraclePks[1], id, rh));
        (bool fin2, uint256 c2,,) = cc.getResult(id);
        assertFalse(fin2);
        assertEq(c2, 2);

        cc.attest(id, rh, "", "", _oracleSig(oraclePks[2], id, rh));
        (bool fin3, uint256 c3, bytes32 sh, string memory sj) = cc.getResult(id);
        assertTrue(fin3); // 3 of 3
        assertEq(c3, 3);
        assertEq(sh, rh);
        assertEq(sj, RJSON);
    }

    function test_RejectsNonOracleSignature() public {
        uint256 id = _newRequest();
        bytes32 rh = keccak256(bytes(RJSON));
        vm.expectRevert("not an oracle");
        cc.attest(id, rh, RJSON, _teeSig(id, rh), _oracleSig(0xBEEF, id, rh));
    }

    function test_RejectsDuplicateOracle() public {
        uint256 id = _newRequest();
        bytes32 rh = keccak256(bytes(RJSON));
        cc.attest(id, rh, RJSON, _teeSig(id, rh), _oracleSig(oraclePks[0], id, rh));
        vm.expectRevert("dup attestation");
        cc.attest(id, rh, "", "", _oracleSig(oraclePks[0], id, rh));
    }

    function test_RejectsBadTeeSig() public {
        uint256 id = _newRequest();
        bytes32 rh = keccak256(bytes(RJSON));
        // teeSig from a non-TEE key
        bytes32 d = keccak256(abi.encode(id, DEAL, uint256(1), uint256(500000), uint256(1000000), rh));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(0xBAD, _eth(d));
        vm.expectRevert("bad TEE sig");
        cc.attest(id, rh, RJSON, abi.encodePacked(r, s, v), _oracleSig(oraclePks[0], id, rh));
    }

    function test_RejectsResultHashMismatchOnSecond() public {
        uint256 id = _newRequest();
        bytes32 rh = keccak256(bytes(RJSON));
        cc.attest(id, rh, RJSON, _teeSig(id, rh), _oracleSig(oraclePks[0], id, rh));
        bytes32 other = keccak256(bytes('{"period":2}'));
        vm.expectRevert("result mismatch");
        cc.attest(id, other, "", "", _oracleSig(oraclePks[1], id, other));
    }
}
