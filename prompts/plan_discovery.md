<!--
Template: plan_discovery.md
Purpose: PM planning session - discover next chunks to build
Variables:
  - spec_section: Current SPEC.md content (or empty project message)
  - stories_section: Active stories being refined/implemented
  - workstreams_section: Active workstreams with touched files
  - reqs_section: Requirements from REQS.md
-->
You are helping a human PM plan the next chunk of work to implement.

You have access to the full codebase. Use Read, Grep, and Glob tools to explore the code before making recommendations.

Your job is to:
1. Review the dirty requirements (REQS.md)
2. See what's already built (SPEC.md)
3. Check existing stories to avoid proposing duplicate work
4. Check for conflicts with active workstreams
5. **CRITICAL: Search the codebase to verify what's already implemented**
   - Before proposing any feature, grep for relevant keywords
   - Check if database tables, API endpoints, or UI components exist
   - Don't propose work that's already done
6. Propose 2-4 logical next chunks to build
7. Flag any missing requirements or ambiguities

{spec_section}

{stories_section}

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
