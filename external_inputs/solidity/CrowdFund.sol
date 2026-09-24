// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface ICrowdToken { function transferFrom(address,address,uint256) external returns (bool); }
contract CrowdFundExcerpt {
    event Launch(uint256 id, address indexed creator, uint256 goal);
    event Pledge(uint256 indexed id, address indexed caller, uint256 amount);
    struct Campaign { address creator; uint256 goal; uint256 pledged; uint32 endAt; bool claimed; }
    ICrowdToken public immutable token;
    mapping(uint256 => Campaign) public campaigns;
    mapping(uint256 => mapping(address => uint256)) public pledgedAmount;

    constructor(address tokenAddress) { token = ICrowdToken(tokenAddress); }
    function pledge(uint256 id, uint256 amount) external {
        Campaign storage campaign = campaigns[id];
        require(block.timestamp <= campaign.endAt, "ended");
        campaign.pledged += amount;
        pledgedAmount[id][msg.sender] += amount;
        token.transferFrom(msg.sender, address(this), amount);
        emit Pledge(id, msg.sender, amount);
    }
}
