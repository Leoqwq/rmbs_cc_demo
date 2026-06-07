// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Script, console2} from "forge-std/Script.sol";
import {ConfidentialCompute} from "../contracts/ConfidentialCompute.sol";

/// @notice Deploy with the TEE address, the oracle set, and the m-of-n threshold.
/// Env vars:
///   DEPLOYER_PRIVATE_KEY  (0x-prefixed)
///   TEE_ADDRESS           (from the TEE service)
///   ORACLE_ADDRESSES      (comma-separated 0x addresses)
///   THRESHOLD             (m)
contract Deploy is Script {
    function run() external {
        uint256 pk = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address tee = vm.envAddress("TEE_ADDRESS");
        address[] memory oracles = vm.envAddress("ORACLE_ADDRESSES", ",");
        uint256 threshold = vm.envUint("THRESHOLD");

        vm.startBroadcast(pk);
        ConfidentialCompute cc = new ConfidentialCompute(tee, oracles, threshold);
        vm.stopBroadcast();

        console2.log("ConfidentialCompute deployed at:", address(cc));
        console2.log("TEE:", tee);
        console2.log("threshold:", threshold);
        console2.log("oracles:", oracles.length);
    }
}
