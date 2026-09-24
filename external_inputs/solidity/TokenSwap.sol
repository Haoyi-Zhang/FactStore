// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface ISwapToken {
    function allowance(address owner, address spender) external view returns (uint256);
    function transferFrom(address sender, address recipient, uint256 amount) external returns (bool);
}
contract TokenSwapExcerpt {
    ISwapToken public token1;
    ISwapToken public token2;
    address public owner1;
    address public owner2;
    uint256 public amount1;
    uint256 public amount2;

    function swap() public {
        require(msg.sender == owner1 || msg.sender == owner2, "not authorized");
        require(token1.allowance(owner1, address(this)) >= amount1, "allowance one low");
        require(token2.allowance(owner2, address(this)) >= amount2, "allowance two low");
        require(token1.transferFrom(owner1, owner2, amount1), "transfer one failed");
        require(token2.transferFrom(owner2, owner1, amount2), "transfer two failed");
    }
}
