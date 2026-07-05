# Karsa Architecture Validation Report

**Purpose:** Validate whether the proposed architectural changes are
justified, identify risks, and determine the safest implementation
strategy.

------------------------------------------------------------------------

# Executive Verdict

## Recommendation

**Proceed, but not as a rewrite.**

The proposed direction is technically sound, however the migration
strategy must prioritize production stability over architectural purity.

Overall assessment:

  Area               Verdict
  ------------------ ------------------------------------------
  Event Bus          ✅ Strongly Recommended
  Position Manager   ✅ Strongly Recommended
  Exit Engine        ✅ Strongly Recommended
  Decision Engine    ✅ Recommended
  Agent Runtime      ⚠️ Recommended for AI orchestration only
  Workflow Engine    ⚠️ Introduce later
  Memory             ⚠️ Introduce after stable runtime
  Knowledge Graph    ❌ Too early

------------------------------------------------------------------------

# Current Architecture Assessment

Current strengths:

-   Modular project structure
-   Clear execution pipeline
-   Mature OMS
-   Mature risk services
-   Good observability
-   Good separation between execution and advisory

Current weaknesses:

-   Service-to-service coupling
-   Multiple owners of position state
-   Distributed exit logic
-   No unified event model
-   No replay capability
-   No workflow orchestration

------------------------------------------------------------------------

# Validation of Each Proposal

## Event Bus

### Value

High.

Expected benefits:

-   Decoupling
-   Easier extensions
-   Better observability
-   Cleaner notifications
-   Cleaner journaling

Risk:

Low.

Migration strategy:

Publish events while preserving existing function calls.

Verdict:

✅ Implement first.

------------------------------------------------------------------------

## Position Manager

### Value

Very High.

Problem solved:

Today multiple modules can modify position state.

Recommended ownership:

Position Manager becomes the single writer.

Benefits:

-   Eliminate race conditions
-   Easier reconciliation
-   Better recovery
-   Deterministic state

Verdict:

✅ Highest priority.

------------------------------------------------------------------------

## Exit Engine

Current situation:

Exit logic is distributed.

Recommended:

One engine responsible for:

-   Initial SL
-   Break Even
-   Partial Exit
-   Trailing
-   Time Exit
-   Emergency Exit

Benefits:

-   Easier reasoning
-   Easier testing
-   Easier backtesting

Verdict:

✅ High priority.

------------------------------------------------------------------------

## Decision Engine

Purpose:

Fuse multiple recommendations before execution.

Inputs:

-   Analyzer
-   Portfolio
-   News
-   Funding
-   Policy

Output:

Single execution decision.

Benefits:

-   Avoid conflicting recommendations
-   Centralized governance

Verdict:

✅ Recommended.

------------------------------------------------------------------------

## Agent Runtime

Challenge:

Do not convert deterministic services into agents.

Suitable candidates:

-   Analyzer
-   Advisor
-   Research
-   Planner
-   Journal

Do NOT convert:

-   OMS
-   Risk
-   Exchange
-   Position Manager
-   Exit Engine

Verdict:

⚠️ Introduce only after infrastructure exists.

------------------------------------------------------------------------

## Workflow Engine

Useful for:

Long-running trading processes.

Not required for basic trading.

Verdict:

⚠️ Medium priority.

------------------------------------------------------------------------

## Memory

Useful for:

-   AI learning
-   Journaling
-   Historical reasoning

Not required for execution.

Verdict:

Later phase.

------------------------------------------------------------------------

## Knowledge Graph

Interesting idea.

Current maturity does not justify implementation.

Verdict:

Postpone.

------------------------------------------------------------------------

# AI Cost Validation

Current proposal:

AI participates only in reasoning.

Deterministic services remain AI-free.

Expected cost:

Low.

Alternative:

LLM on every market event.

Expected cost:

Very high and operationally risky.

Conclusion:

Keep AI outside execution path.

------------------------------------------------------------------------

# Production Risk Assessment

  Change             Risk
  ------------------ --------
  Event Bus          Low
  Position Manager   Medium
  Exit Engine        Medium
  Decision Engine    Medium
  Agent Runtime      High
  Workflow Engine    High

------------------------------------------------------------------------

# Recommended Migration Order

1.  Event Bus
2.  Position Manager
3.  Exit Engine
4.  Decision Engine
5.  Replay Engine
6.  Policy Engine
7.  Agent Runtime
8.  Workflow Engine
9.  Memory
10. Knowledge Graph

------------------------------------------------------------------------

# Success Criteria

Architecture migration is considered successful when:

-   No behavioral changes during Event Bus introduction.
-   Position state has a single owner.
-   Exit logic is centralized.
-   AI is never required for order execution.
-   Every trade is replayable.
-   New features are implemented via events instead of service coupling.

------------------------------------------------------------------------

# Final Conclusion

The proposed architecture is an evolution rather than a replacement of
the current Karsa platform.

Approximately 70% of the required architecture already exists. The
remaining work should focus on introducing missing
infrastructure---Event Bus, Position Manager, Exit Engine, and Decision
Engine---before introducing Agent Runtime.

The recommended strategy is incremental migration with backward
compatibility at every phase. This minimizes production risk while
establishing a scalable foundation for future AI-assisted capabilities.
