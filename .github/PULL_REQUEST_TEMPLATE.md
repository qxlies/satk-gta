## What and why

<!-- What does this change, and why? Link the issue it closes: "Fixes #123". -->

## How it was tested

<!-- The commands you ran and what they showed: the CI checks, a new test, or a manual check with your game.
     Never paste or attach game files. -->

## Checklist

- [ ] One topic per pull request; the commit messages say what changes and why.
- [ ] Tests added or updated for every change in behavior; the checks that CI runs pass on my machine (the
      commands are in CONTRIBUTING.md, "Before you open a pull request").
- [ ] No game files, mod files or pictures of game assets; the pre-commit hook (`satk dev assetguard`) ran and
      was not bypassed with `--no-verify`.
- [ ] No code copied from gta-reversed, Ariane/euryopa, SA-MP sources, MTA or DragonFF into MIT files.
- [ ] No paths of my machine, personal data, e-mail addresses or tokens in code, tests, docs or logs.
- [ ] Operations, their parameters or an SQL schema changed: I ran `satk dev gen-docs`.
- [ ] User-visible change: `docs/en/` is updated, and the Russian mirror `docs/ru/` too (or I say below that I
      could not write it).
- [ ] Code, messages and model-facing text are in English; every file stays in one language.
- [ ] I agree that my contribution is licensed under the license of the files it changes (MIT; GPL-3.0 for
      `blender/satk_blender`).

<!-- Anything reviewers should know: limitations, follow-ups, the Russian mirror. -->
