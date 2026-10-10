// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// @notice Stores only salted evidence commitments for AgentSafe demo runs.
/// @dev Intentionally limited to Avalanche Fuji. Event contents and salts stay off-chain.
contract AuditAnchor {
    error FujiOnly(uint256 actualChainId);
    error EmptyRoot();
    error RootAlreadyAnchored(bytes32 root);

    event RootAnchored(
        bytes32 indexed root,
        address indexed submittedBy,
        uint64 anchoredAt
    );

    mapping(bytes32 root => uint64 timestamp) public anchoredAt;

    constructor() {
        if (block.chainid != 43_113) revert FujiOnly(block.chainid);
    }

    function anchorRoot(bytes32 root) external {
        if (root == bytes32(0)) revert EmptyRoot();
        if (anchoredAt[root] != 0) revert RootAlreadyAnchored(root);

        uint64 timestamp = uint64(block.timestamp);
        anchoredAt[root] = timestamp;
        emit RootAnchored(root, msg.sender, timestamp);
    }
}
