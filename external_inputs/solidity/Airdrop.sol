// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface IAirdropToken { function mint(address to, uint256 amount) external; }
contract AirdropExcerpt {
    event Claim(address to, uint256 amount);
    IAirdropToken public immutable token;
    bytes32 public immutable root;
    mapping(bytes32 => bool) public claimed;

    constructor(address tokenAddress, bytes32 merkleRoot) {
        token = IAirdropToken(tokenAddress);
        root = merkleRoot;
    }
    function claim(bytes32 leaf, address to, uint256 amount) external {
        require(!claimed[leaf], "already claimed");
        claimed[leaf] = true;
        token.mint(to, amount);
        emit Claim(to, amount);
    }
}
