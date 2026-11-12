# Security policy

## Guarantees

declutter is designed to be safe to run on any project, including ones with sensitive content.

- **Read-only engine.** `declutter measure`, `declutter radar` and the hooks (`declutter hook session-start`,
  `declutter statusline`) never write inside the projects they inspect. Their only writes are the local
  cache and the hook error log under `~/.cache/declutter/`, and `declutter radar --save` / `--md`,
  which write the registry file or the report you asked for.
- **`declutter init` only creates.** It writes new files inside the target project, never overwrites an
  existing file, skips targets that already exist as symlinks, and skips targets whose parent
  directory resolves outside the project.
- **No network.** The engine uses only the Python standard library and opens no sockets. It
  starts `git` as a local subprocess to read repository state and nothing else.
- **No AI.** The engine does not call any model.
- **Metrics, not content.** Output consists of numbers, file names and the items of the state file
  (`ESTADO.md` / `STATE.md`). It does not print the contents of other files, and the placeholder
  that `declutter measure` shows when a signal fails omits the exception text.
- **Paths are confined.** Reads go through one helper that resolves symlinks and refuses paths
  outside the allowed roots each caller declares. Files that live inside a project (its
  `CLAUDE.md`, `AGENTS.md`, rules and relative imports) must resolve inside that project, so a
  malicious symlink cannot make the tool read elsewhere in your home folder.
- **Only regular files, bounded size.** FIFOs, sockets and devices are never opened for reading
  (they could hang a hook), at most 8 MiB of a file is decoded, and larger files are measured by
  streaming. Directory walks never follow symlinked folders, so symlink loops cannot stall them.
- **Fail-safe hooks.** The hooks never raise, always exit with code 0 and log errors to
  `~/.cache/declutter/hook.log` (size-capped, with rotation).

## Files that are never read

Any file whose name, or the name of the file a symlink points to, matches these patterns is
skipped by every read in the engine:

```text
.env   .env.*   *.pem   *.key   id_*   secrets*
```

Contributors: do not add code that reads files outside `fsutil.safe_read_text` / `fsutil.iter_files`.

## Supported versions

Only the latest released version receives security fixes.

## Reporting a vulnerability

Please report privately through GitHub: open the repository's **Security** tab and choose
**Report a vulnerability** (a private security advisory). Do not open a public issue for security
problems.

Include the version (`declutter --version`), your platform, and a minimal reproduction using synthetic
files. Never attach real project data or secrets. You can expect an acknowledgement within a few
days; the maintainer will coordinate a fix and disclosure with you.
