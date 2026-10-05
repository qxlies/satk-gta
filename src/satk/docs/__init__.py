"""satk.docs — documentation tooling (owner: docs, M3 A3; SPEC §4.13, §5.3 WP-12).

* :mod:`satk.docs.linkcheck` — ``satk dev linkcheck``: paths and links mentioned in Markdown exist;
* :mod:`satk.docs.smoke` — ``satk dev docs-smoke``: run the quick-example blocks of ``docs/en`` and ``docs/ru``;
* :mod:`satk.docs.parity` — ``satk dev docs-parity``: every English page has its Russian mirror and both
  start with the language switch;
* :mod:`satk.docs.workflows` — ``satk dev workflow-cost``: the agent workflows S1–S8 as executable call
  chains (MCP dispatch path) with measured calls and estimated tokens;
* :mod:`satk.docs.sync` — ``satk dev sync-agent-docs``: publish the agent docs kept in the repo
  (``docs/agent/SKILL.md``, ``docs/agent/workspace-CLAUDE.md``) to the workspace;
* :mod:`satk.docs.cleanup` — removal of temporary work directories that retries and reports leftovers
  (docs-smoke private work dirs, e2e runs) and waits for a process object to be signaled.

Standard library only (like ``satk.core``).
"""
