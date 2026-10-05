# Security policy

satk reads game folders and mod files that come from strangers, writes into a folder of its own, and runs an MCP
server that an AI assistant drives. Bugs in those places can hurt users, so please report them privately.

## Supported versions

satk is a preview. Only the latest release receives security fixes; older previews are not patched.

| Version | Supported |
|---|---|
| latest 0.2.x preview | yes |
| anything older | no |

## Reporting a vulnerability

Report privately through GitHub: open this repository's **Security** tab and choose **Report a vulnerability**
(a private security advisory). Only the maintainers see it. Please do **not** open a public issue, discussion or pull
request for a vulnerability, and do not send reports by e-mail.

A useful report has:

- the satk version (`satk version`) and the Windows version;
- what an attacker controls (a mod archive, a file in the game folder, an MCP tool call, a crash dump, ...) and what
  they gain;
- the smallest reproduction you can make. Build the input synthetically when you can; **never attach game files or
  copyrighted mods**. If the input really must come from a public mod, link to it instead.

What happens next (best effort, this is a small project):

1. we confirm that we received the report within 7 days;
2. we agree on the impact with you and work on a fix inside the private advisory;
3. the fix ships in a release, then the advisory is published with credit to you (unless you prefer to stay
   anonymous).

## Scope

In scope are weaknesses in satk itself, especially:

- **Path guard and write protection.** satk must never write into the game folder, the clean game copy or the
  source clones, and must write only inside its `work` folder (and the output paths the user names). Escapes
  through `..`, absolute paths, junctions, symbolic links, 8.3 short names, alternate data streams, case tricks,
  UNC or `\\?\` prefixes, or race conditions are in scope.
- **Parsers of untrusted files.** IMG, DFF, TXD, COL, IPL (text and binary), IDE, IFP, ZON, DAT, GXT, handling and
  vehicle data, Mod Loader folders, crash dumps and logs, map files of SA-MP and MTA, and mod archives: memory or CPU
  exhaustion, hangs, crashes of the process, or writes outside the intended output folder (for example an entry name
  like `..\..\file` inside an archive).
- **The MCP server** (`satk mcp serve`, stdio). Tool arguments come from a model and are untrusted: they must not
  reach operations that are marked CLI-only, write outside `work`, run non-read-only SQL through `index_query`, or
  make satk open network connections.
- **Privacy of bug reports.** `satk bug-report` must redact paths, user and host names and tokens; a leak is in scope.
- **Downloads** that satk performs on request (dependency setup, engine setup, release builds) must check pinned
  hashes.

Out of scope:

- vulnerabilities in GTA San Andreas, in mods, or in other projects that satk works with (the MTA fork, the Ariane
  viewer, Blender, AI clients); report those to their own projects;
- an AI assistant following instructions that it found in game data or mod files: satk returns such text as data,
  and the assistant is responsible for treating it as data (hardening suggestions are still welcome as issues);
- attacks that need an already compromised machine or administrator rights;
- missing hardening without a concrete attack (open an issue for those).
