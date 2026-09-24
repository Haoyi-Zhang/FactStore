// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

contract EnglishAuctionExcerpt {
    event Bid(address indexed sender, uint256 amount);
    event Withdraw(address indexed bidder, uint256 amount);
    address payable public seller;
    uint256 public endAt;
    address public highestBidder;
    uint256 public highestBid;
    mapping(address => uint256) public bids;

    function bid() external payable {
        require(block.timestamp < endAt, "ended");
        require(msg.value > highestBid, "value below highest");
        if (highestBidder != address(0)) { bids[highestBidder] += highestBid; }
        highestBidder = msg.sender;
        highestBid = msg.value;
        emit Bid(msg.sender, msg.value);
    }
    function withdraw() external {
        uint256 balance = bids[msg.sender];
        bids[msg.sender] = 0;
        payable(msg.sender).transfer(balance);
        emit Withdraw(msg.sender, balance);
    }
}
