"""Operations of ``satk.batch``, CLI only (``mcp=False``; agents reach them through ``satk_op``):

* ``batch``         -> ``satk batch <op> --over <glob|SQL|@list|dir|items> [--arg k=v ...] [--jobs N] [--resume]``;
* ``batch.report``  -> ``satk batch report <results.jsonl> [--status fail] [--field summary.fatal ...]``;
* ``recipe.run``    -> ``satk recipe run <name|file.yaml> [--var k=v ...] [--dry-run]``;
* ``recipe.list``   -> ``satk recipe list``;
* ``recipe.show``   -> ``satk recipe show <name|file.yaml>``.
"""

from __future__ import annotations

from typing import Literal

from ..core.registry import op


@op("batch", mcp=False, long_running=True,
    summary="Run any operation over many inputs (glob, IMG entries, @list file, folder, SQL over the index, "
            "SIDs): JSONL record per input, --resume skips inputs done, threads for read-only ops; answer = "
            "counts and first failures, CHECK_FAILED if any input failed.",
    summary_ru="Запуск любой операции по многим входам (glob, записи IMG, @список, папка, SQL, SID): JSONL на "
               "каждый вход, --resume, потоки для операций чтения; сводка и первые ошибки, CHECK_FAILED при сбоях",
    examples=('satk batch asset.lint --over "mods/cars/*.dff" --arg fail_on=error --jobs 4',
              'satk batch formats.dump --over "models/gta3.img/infer*.dff" --dry-run',
              'satk batch texture.extract --over @txds.txt --resume',
              'satk batch asset.get --over "SELECT sid FROM v_model WHERE name LIKE \'infer%\'"'))
def batch(op: list[str], over: str | None = None, arg: list[str] | None = None, args: dict | None = None,
          param: str | None = None, jobs: int = 1, resume: bool = False, out: str | None = None,
          keep: Literal["full", "brief", "none"] = "full", max_fail: int = 0, allow_empty: bool = False,
          profile: str | None = None, dry_run: bool = False, yes: bool = False, limit: int = 20) -> dict:
    """Run one operation per input.

    Each input fills the operation's input parameter (``--param``; default: its first required parameter) on
    top of the shared arguments. String arguments may use ``{item} {name} {stem} {ext} {parent} {n}``.

    Args:
        op: the operation: dotted name (asset.lint), CLI words (asset lint) or MCP tool name.
        over: inputs: a glob (mods/*.dff, **/*.txd, models/gta3.img/*.dff), @file (one input or JSON argument
            object per line; @- = stdin), a folder (its children), SQL (SELECT ... or sql:...; columns named like
            parameters fill them) or comma-separated SIDs/names.
        arg: shared arguments key=value (repeat or comma-separate; JSON lists ok: rule=["a","b"]).
        args: shared arguments as a JSON object (or @file.json).
        param: parameter that gets each input (default: the first required one); none = pass it to no
            parameter, only to {item} templates in --arg (batch of recipes: --param none --arg var=mod={item}).
        jobs: threads (0 = one per CPU, at most 8); only operations known to be thread-safe use them.
        resume: skip inputs whose key (operation + arguments) has an ok record in the results file; rerun failures.
        out: results JSONL (default work/out/batch/<op>-<hash>.jsonl; the same batch gets the same file).
        keep: what each record keeps of the answer: full, brief (no table rows) or none.
        max_fail: stop after this many failures (0 = run everything).
        allow_empty: no inputs is ok (default: NOT_FOUND).
        profile: index profile of SQL inputs and game root for relative globs; also passed to the operation
            when it has a profile parameter and --arg does not set it.
        dry_run: show the inputs and their arguments; run and write nothing.
        yes: run operations that need the user's consent or are CLI only (counts only on the command line).
        limit: rows of failures in the answer.
    """
    from ..core.errors import SatkError
    from .runner import run_batch

    if not over:
        raise SatkError("BAD_PARAMS", "give the inputs: --over <glob|@list|folder|SQL|SIDs>",
                        hint='satk batch asset.lint --over "mods/*.dff"')
    name = " ".join(op) if isinstance(op, list) else str(op)
    return run_batch(name, over, arg=arg, args=args, param=param, jobs=jobs, resume=resume, out=out, keep=keep,
                     max_fail=max_fail, allow_empty=allow_empty, profile=profile, dry_run=dry_run, yes=yes,
                     limit=limit)


@op("batch.report", mcp=False,
    summary="Table of a batch results file (JSONL): item/status/code/msg or chosen result fields "
            "(--field summary.fatal), status filter, paging; counts, error codes and summed summary counters.",
    summary_ru="Таблица файла результатов batch: статус, коды ошибок или выбранные поля ответов; фильтр, страницы",
    examples=("satk batch report work/out/batch/asset.lint-1a2b3c4d.jsonl --status fail",
              "satk batch report results.jsonl --field summary.fatal --field files"))
def batch_report(results: str, status: Literal["all", "ok", "fail"] = "all", field: list[str] | None = None,
                 limit: int = 20, cursor: str | None = None) -> dict:
    """Read a results file.

    Args:
        results: the JSONL file of a batch (its 'out' value).
        status: all, ok or fail.
        field: dotted paths into each answer to show as columns (summary.fatal, total, rows.0.1).
        limit: rows per page.
        cursor: 'next' of the previous page.
    """
    from .runner import report

    return report(results, status=status, fields=field, limit=limit, cursor=cursor)


@op("recipe.run", mcp=False, long_running=True,
    summary="Run a recipe: steps = registered operations with arguments, variables (--var k=v) and references "
            "to earlier steps' answers; missing operations are skipped; --dry-run plans without running or "
            "writing. Table step/op/status/info; CHECK_FAILED if a step failed.",
    summary_ru="Запуск рецепта: шаги = операции с аргументами, переменными и ссылками на ответы прошлых шагов; "
               "отсутствующие операции пропускаются; --dry-run только план",
    examples=("satk recipe run check-mod-before-release --var mod=mods/mycar",
              "satk recipe run crash-triage --dry-run",
              "satk recipe run my-recipe.yaml --var file=a.dff"))
def recipe_run(recipe: str, var: list[str] | None = None, vars: dict | None = None,  # noqa: A002
               dry_run: bool = False, yes: bool = False, out: str | None = None) -> dict:
    """Run a recipe.

    Args:
        recipe: a recipe name (satk recipe list) or a .yaml/.json/.toml file.
        var: variables key=value (repeat or comma-separate).
        vars: variables as a JSON object.
        dry_run: validate and show the plan; run and write nothing.
        yes: allow steps that need the user's consent or are CLI only (counts only on the command line).
        out: JSON file with every step's full answer (default work/out/recipes/<name>.json).
    """
    from .recipe import run_recipe

    return run_recipe(recipe, var=var, vars=vars, dry_run=dry_run, yes=yes, out=out)


@op("recipe.list", mcp=False,
    summary="List recipes: shipped (data/recipes) and the user's (work/recipes; same name wins): name, source, "
            "summary, variables (* = required).",
    summary_ru="Список рецептов: поставляемые и пользовательские (work/recipes), переменные",
    examples=("satk recipe list",))
def recipe_list() -> dict:
    """Shipped and user recipes."""
    from .recipe import list_recipes

    return list_recipes()


@op("recipe.show", mcp=False,
    summary="Show a recipe: variables (default, required, help) and steps (operation, available or not, "
            "when/unless/on_error/fail_if, arguments) plus the command that runs it.",
    summary_ru="Рецепт: переменные и шаги (операция, доступна ли, условия, аргументы)",
    examples=("satk recipe show crash-triage",))
def recipe_show(recipe: str) -> dict:
    """Describe a recipe.

    Args:
        recipe: a recipe name or a .yaml/.json/.toml file.
    """
    from .recipe import show_recipe

    return show_recipe(recipe)
