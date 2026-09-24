// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

contract Create2FactoryExcerpt {
    event Deployed(address deployed, uint256 salt);
    function deploy(bytes memory bytecode, uint256 salt) public payable {
        address deployed;
        assembly {
            deployed := create2(callvalue(), add(bytecode, 0x20), mload(bytecode), salt)
            if iszero(extcodesize(deployed)) { revert(0, 0) }
        }
        emit Deployed(deployed, salt);
    }
    function getAddress(bytes memory bytecode, uint256 salt) public view returns (address) {
        bytes32 digest = keccak256(abi.encodePacked(bytes1(0xff), address(this), salt, keccak256(bytecode)));
        return address(uint160(uint256(digest)));
    }
}
