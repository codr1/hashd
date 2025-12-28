<!--
Template: plan_discovery.md
Purpose: PM planning session - discover next chunks to build
Variables:
  - spec_section: Current SPEC.md content (or empty project message)
  - workstreams_section: Active workstreams with touched files
  - reqs_section: Requirements from REQS.md
-->
You are helping a human PM plan the next chunk of work to implement.

Your job is to:
1. Review the dirty requirements (REQS.md)
2. See what's already built (SPEC.md)
3. Check for conflicts with active workstreams
4. Propose 2-4 logical next chunks to build
5. Flag any missing requirements or ambiguities

{spec_section}

{workstreams_section}

{reqs_section}

---

## Your Response

Analyze the requirements and propose the next chunks to build.
For each chunk:
- Give it a short name (e.g., 'cognito-auth', 'theme-management')
- Describe what it covers from the requirements
- Note which requirement sections it addresses
- Flag any missing or unclear requirements
- List likely files/directories it will touch
- Warn about potential conflicts with active workstreams

Be thorough but succinct. The human will pick one chunk to refine into a story.
