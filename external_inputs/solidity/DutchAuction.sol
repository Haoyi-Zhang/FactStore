// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface IAuctionNFT { function transferFrom(address,address,uint256) external; }
contract DutchAuctionExcerpt {
    uint256 private constant DURATION = 7 days;
    IAuctionNFT public immutable nft;
    address payable public immutable seller;
    uint256 public immutable startingPrice;
    uint256 public immutable startAt;
    uint256 public immutable discountRate;

    function getPrice() public view returns (uint256) {
        uint256 elapsed = block.timestamp - startAt;
        return startingPrice - discountRate * elapsed;
    }
    function buy(uint256 nftId) external payable {
        require(block.timestamp < startAt + DURATION, "auction expired");
        uint256 price = getPrice();
        require(msg.value >= price, "insufficient value");
        nft.transferFrom(seller, msg.sender, nftId);
        if (msg.value > price) { payable(msg.sender).transfer(msg.value - price); }
    }
}
