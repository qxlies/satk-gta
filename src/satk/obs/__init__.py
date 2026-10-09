"""satk.obs: the observability record family of sa-engine (one timeline key, JSONL records, bench JSON, containers).

Four artefacts share one vocabulary (specification: ``docs/en/obs-spec.md``):

* the **timeline key** ``(t_us, tick, sub, frame)``: session time, server tick ``T``, client sub-tick ``k`` and the frame
  counter; every record of every producer carries it, so client, server and bot timelines join offline;
* the **JSONL stream** ``sae-obs/1``: a header, then ``frame`` / ``tick`` / ``stats`` / ``event`` / ``net`` / ``input`` /
  ``seed`` / ``snap`` records and an ``end`` footer;
* the **bench JSON** ``sae-bench/1``: one schema for ``satk ingame bench`` (converted from ``satk-bench/1``) and the later
  trace and bench packages;
* the **container** ``.saenet`` / ``.saerec``: header, append-only chunks, chunk table, optional zlib compression.

Modules: :mod:`.jschema` (JSON Schema subset validator), :mod:`.schemas` (the schema files under ``data/obs``),
:mod:`.key` (key helpers), :mod:`.stats` (frame statistics), :mod:`.stream` (JSONL reader, validator, summary),
:mod:`.bench` (bench validator, converters), :mod:`.container` (reader, writer, validator), :mod:`.sample`
(deterministic example data). Stdlib only; no game data anywhere.
"""
