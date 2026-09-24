// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

contract ProxyExcerpt {
    event Deploy(address deployed);
    receive() external payable {}
    function deploy(bytes memory code) external payable returns (address addressCreated) {
        assembly { addressCreated := create(callvalue(), add(code, 0x20), mload(code)) }
        require(addressCreated != address(0), "deploy failed");
        emit Deploy(addressCreated);
    }
    function execute(address target, bytes memory data) external payable {
        (bool success,) = target.call{value: msg.value}(data);
        require(success, "call failed");
    }
}
