// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface IERC721ReceiverExcerpt {
    function onERC721Received(address,address,uint256,bytes calldata) external returns (bytes4);
}
contract ERC721Excerpt {
    event Transfer(address indexed from, address indexed to, uint256 indexed id);
    event Approval(address indexed owner, address indexed spender, uint256 indexed id);
    mapping(uint256 => address) internal ownerOfToken;
    mapping(address => uint256) internal balanceOfOwner;
    mapping(uint256 => address) internal approvals;

    function transferFrom(address from, address to, uint256 id) public {
        require(from == ownerOfToken[id], "from is not owner");
        require(to != address(0), "zero recipient");
        balanceOfOwner[from]--;
        balanceOfOwner[to]++;
        ownerOfToken[id] = to;
        delete approvals[id];
        emit Transfer(from, to, id);
    }
}
