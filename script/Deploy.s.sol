// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Script, console2} from "forge-std/Script.sol";
import {ConfidentialCompute} from "../contracts/ConfidentialCompute.sol";

/// @notice Deploys ConfidentialCompute with the TEE address baked in.
/// Env vars:
///   DEPLOYER_PRIVATE_KEY  (0x-prefixed) — funded genesis account on the Besu chain
///   TEE_ADDRESS           — printed by the TEE service on startup
contract Deploy is Script {
    function run() external {
        uint256 pk = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address tee = vm.envAddress("TEE_ADDRESS");

        vm.startBroadcast(pk);
        ConfidentialCompute cc = new ConfidentialCompute(tee);
        vm.stopBroadcast();

        console2.log("ConfidentialCompute deployed at:", address(cc));
        console2.log("TEE address:", tee);
    }
}
