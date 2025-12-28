<!--
Template: final_review.md
Purpose: Holistic branch review before merge
Variables:
  - feature_title: Title of the feature/workstream
  - commit_log: Git log of commits on the branch
  - diff_stats: Git diff --stat output
  - diff: Full git diff against main
-->
You are reviewing a complete feature branch before merge.

## Feature: {feature_title}

## Commits:
{commit_log}

## Diff Stats:
{diff_stats}

## Full Diff:
{diff}

Provide a final review with:
1. **Summary**: 2-3 sentences describing what this feature does
2. **Changes Overview**: Key areas affected
3. **Assessment**: Holistic review - does this hang together well? Any cross-cutting concerns?
4. **Concerns**: Any issues spotted (or "None")
5. **Verdict**: Either "APPROVE" or "CONCERNS"

Be concise but thorough. This is a senior staff engineer's final check before merge.
