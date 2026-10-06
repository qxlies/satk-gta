"""satk.batch: run any registered operation over many inputs, and recipes of operations.

* ``satk batch <op> --over <glob|SQL|@list|dir|items> [--arg k=v ...] [--jobs N] [--resume] [--out f.jsonl]``
  runs one operation once per input, in-process (threads for operations known to be thread-safe),
  writes one JSONL record per input and answers with a compact summary (counts, first failures);
* ``satk batch report <results.jsonl>`` tabulates a results file (status filter, fields of the results);
* ``satk recipe run <name|file.yaml> [--var k=v ...] [--dry-run]`` runs the steps of a recipe: registered
  operations with arguments, variables and references to the results of earlier steps;
  ``satk recipe list`` / ``satk recipe show <name>`` describe the shipped and the user's recipes.

Operations an agent must not run (``satk.mcp.generic.denial``: CLI only or consent) are refused inside a
batch or a recipe unless ``--yes`` is given on the command line; an AI client can never give it.
Python API: :func:`satk.batch.runner.run_batch`, :func:`satk.batch.recipe.run_recipe`,
:func:`satk.batch.yamlite.loads`.
"""
