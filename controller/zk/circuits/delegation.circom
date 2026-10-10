pragma circom 2.1.6;

include "circomlib/circuits/poseidon.circom";
include "circomlib/circuits/comparators.circom";

template DelegationSubset() {
    // Public outputs commit to each private mandate. Public inputs bind the proof
    // to one mission and the two signing keys; policy fields stay private.
    signal input missionId;
    signal input parentAgentId;
    signal input childAgentId;
    signal input parentActions[8];
    signal input childActions[8];
    signal input parentResources[8];
    signal input childResources[8];
    signal input parentConditions[8];
    signal input childConditions[8];
    signal input parentBudgetCents;
    signal input childBudgetCents;
    signal input parentExpiresAt;
    signal input childExpiresAt;
    signal input parentCanDelegate;
    signal input childCanDelegate;
    signal input parentAllowExternal;
    signal input childAllowExternal;
    signal input parentNonce;
    signal input childNonce;

    signal output parentCommitment;
    signal output childCommitment;

    component parentScope = Poseidon(16);
    component childScope = Poseidon(16);
    component parentConditionHash = Poseidon(8);
    component childConditionHash = Poseidon(8);
    for (var i = 0; i < 8; i++) {
        parentScope.inputs[i] <== parentActions[i];
        parentScope.inputs[i + 8] <== parentResources[i];
        childScope.inputs[i] <== childActions[i];
        childScope.inputs[i + 8] <== childResources[i];
        parentConditionHash.inputs[i] <== parentConditions[i];
        childConditionHash.inputs[i] <== childConditions[i];
    }
    component parentFullScope = Poseidon(2);
    parentFullScope.inputs[0] <== parentScope.out;
    parentFullScope.inputs[1] <== parentConditionHash.out;
    component childFullScope = Poseidon(2);
    childFullScope.inputs[0] <== childScope.out;
    childFullScope.inputs[1] <== childConditionHash.out;

    component parentHash = Poseidon(8);
    parentHash.inputs[0] <== missionId;
    parentHash.inputs[1] <== parentAgentId;
    parentHash.inputs[2] <== parentFullScope.out;
    parentHash.inputs[3] <== parentBudgetCents;
    parentHash.inputs[4] <== parentExpiresAt;
    parentHash.inputs[5] <== parentCanDelegate;
    parentHash.inputs[6] <== parentAllowExternal;
    parentHash.inputs[7] <== parentNonce;
    parentCommitment <== parentHash.out;

    component childHash = Poseidon(8);
    childHash.inputs[0] <== missionId;
    childHash.inputs[1] <== childAgentId;
    childHash.inputs[2] <== childFullScope.out;
    childHash.inputs[3] <== childBudgetCents;
    childHash.inputs[4] <== childExpiresAt;
    childHash.inputs[5] <== childCanDelegate;
    childHash.inputs[6] <== childAllowExternal;
    childHash.inputs[7] <== childNonce;
    childCommitment <== childHash.out;

    component budgetLess = LessThan(64);
    budgetLess.in[0] <== childBudgetCents;
    budgetLess.in[1] <== parentBudgetCents;
    component budgetEqual = IsEqual();
    budgetEqual.in[0] <== childBudgetCents;
    budgetEqual.in[1] <== parentBudgetCents;
    budgetLess.out + budgetEqual.out === 1;

    component expiryLess = LessThan(64);
    expiryLess.in[0] <== childExpiresAt;
    expiryLess.in[1] <== parentExpiresAt;
    component expiryEqual = IsEqual();
    expiryEqual.in[0] <== childExpiresAt;
    expiryEqual.in[1] <== parentExpiresAt;
    expiryLess.out + expiryEqual.out === 1;

    parentCanDelegate * (parentCanDelegate - 1) === 0;
    childCanDelegate * (childCanDelegate - 1) === 0;
    childCanDelegate * (1 - parentCanDelegate) === 0;
    parentAllowExternal * (parentAllowExternal - 1) === 0;
    childAllowExternal * (childAllowExternal - 1) === 0;
    childAllowExternal * (1 - parentAllowExternal) === 0;

    component actionBlank[8];
    component actionMatch[8][8];
    signal actionMatches[8][8];
    signal matchedActions[8][8];
    component resourceBlank[8];
    component resourceMatch[8][8];
    signal resourceMatches[8][8];
    signal matchedResources[8][8];
    component parentConditionBlank[8];
    component conditionMatch[8][8];
    signal conditionMatches[8][8];
    signal matchedConditions[8][8];
    for (var c = 0; c < 8; c++) {
        actionBlank[c] = IsZero();
        actionBlank[c].in <== childActions[c];
        resourceBlank[c] = IsZero();
        resourceBlank[c].in <== childResources[c];
        parentConditionBlank[c] = IsZero();
        parentConditionBlank[c].in <== parentConditions[c];
        var actionMatchCount = 0;
        var resourceMatchCount = 0;
        var conditionMatchCount = 0;
        for (var p = 0; p < 8; p++) {
            actionMatch[c][p] = IsEqual();
            actionMatch[c][p].in[0] <== childActions[c];
            actionMatch[c][p].in[1] <== parentActions[p];
            actionMatches[c][p] <== actionMatch[c][p].out;
            matchedActions[c][p] <== actionMatches[c][p] * (1 - actionBlank[c].out);
            resourceMatch[c][p] = IsEqual();
            resourceMatch[c][p].in[0] <== childResources[c];
            resourceMatch[c][p].in[1] <== parentResources[p];
            resourceMatches[c][p] <== resourceMatch[c][p].out;
            matchedResources[c][p] <== resourceMatches[c][p] * (1 - resourceBlank[c].out);
            conditionMatch[c][p] = IsEqual();
            conditionMatch[c][p].in[0] <== parentConditions[c];
            conditionMatch[c][p].in[1] <== childConditions[p];
            conditionMatches[c][p] <== conditionMatch[c][p].out;
            matchedConditions[c][p] <== conditionMatches[c][p] * (1 - parentConditionBlank[c].out);
            actionMatchCount += matchedActions[c][p];
            resourceMatchCount += matchedResources[c][p];
            conditionMatchCount += matchedConditions[c][p];
        }
        actionMatchCount + actionBlank[c].out === 1;
        resourceMatchCount + resourceBlank[c].out === 1;
        conditionMatchCount + parentConditionBlank[c].out === 1;
    }
}

component main {public [missionId, parentAgentId, childAgentId]} = DelegationSubset();
