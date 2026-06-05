// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {ConfidentialCompute} from "../contracts/ConfidentialCompute.sol";

contract ConfidentialComputeTest is Test {
    ConfidentialCompute cc;
    uint256 teePk = 0xA11CE;
    address tee;

    // Mirror the contract event so we can use it in vm.expectEmit (Solidity 0.8.20
    // does not support qualified `emit ContractName.EventName(...)` syntax).
    event ComputeRequested(
        uint256 indexed id,
        string dealId,
        uint256 period,
        uint256 iaf,
        uint256 paf,
        address requester
    );

    function setUp() public {
        tee = vm.addr(teePk);
        cc = new ConfidentialCompute(tee);
    }

    function _sign(uint256 pk, bytes32 resultHash) internal pure returns (bytes memory) {
        bytes32 ethHash = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", resultHash)
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, ethHash);
        return abi.encodePacked(r, s, v);
    }

    function test_SubmitRequestIncrementsAndEmits() public {
        vm.expectEmit(true, false, false, true);
        emit ComputeRequested(1, "TEST_SEQ_2024", 1, 500000, 1000000, address(this));
        uint256 id = cc.submitRequest("TEST_SEQ_2024", 1, 500000, 1000000);
        assertEq(id, 1);
        assertEq(cc.requestCount(), 1);
    }

    function test_PostResultWithValidSignatureStores() public {
        uint256 id = cc.submitRequest("TEST_SEQ_2024", 1, 500000, 1000000);
        string memory resultJson = '{"period":1}';
        bytes32 resultHash = keccak256(bytes(resultJson));
        bytes memory sig = _sign(teePk, resultHash);

        cc.postResult(id, resultHash, resultJson, sig);

        (bool posted, bytes32 h, string memory j) = cc.getResult(id);
        assertTrue(posted);
        assertEq(h, resultHash);
        assertEq(j, resultJson);
    }

    function test_PostResultRevertsOnWrongSigner() public {
        uint256 id = cc.submitRequest("TEST_SEQ_2024", 1, 500000, 1000000);
        string memory resultJson = '{"period":1}';
        bytes32 resultHash = keccak256(bytes(resultJson));
        bytes memory sig = _sign(0xB0B, resultHash); // not the TEE key

        vm.expectRevert("Invalid TEE signature");
        cc.postResult(id, resultHash, resultJson, sig);
    }

    function test_PostResultRevertsOnHashMismatch() public {
        uint256 id = cc.submitRequest("TEST_SEQ_2024", 1, 500000, 1000000);
        string memory resultJson = '{"period":1}';
        bytes32 wrongHash = keccak256(bytes('{"period":2}'));
        bytes memory sig = _sign(teePk, wrongHash);

        vm.expectRevert("Hash mismatch");
        cc.postResult(id, wrongHash, resultJson, sig);
    }
}
