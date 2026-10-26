# Security

## Trust model

`dv` is a local verification aid for repositories and commands you trust. It is not a sandbox, an access-control system, a general secret scanner, or a signed attestation service.

- Configured commands run with your user permissions and inherited environment. They can read secrets, modify files, spawn processes, or access the network. Argument arrays avoid an implicit shell; they do not make the invoked program safe.
- `dv prove` materializes raw committed blobs in separate detached worktrees and leaves dirty caller files alone. Checkout hooks and smudge filters are disabled; submodule/LFS hydration and outward symlinks are rejected. It allocates independent caches, never links caller dependency directories, and records tracked/index mutations. Worktrees still share Git administration; a deliberately hostile command can reach outside its cwd. Editable installs and inherited environment paths can defeat source isolation.
- Proof attempts keep logs and `proof.json` in Git's administrative `dv/proofs/` directory. If an attempt leaves `recovery.json`, inspect exactly its listed worktrees/caches and registrations before targeted cleanup. A later attempt warns but does not automatically delete those resources. Cleanup failure cannot publish PASS. SIGKILL, power loss and escaped descendants remain outside cleanup guarantees.
- The legacy `dv redgreen` command temporarily restores changed production paths to the merge base, runs the retained tests, then restores production paths to `HEAD` before green. Use a clean POSIX worktree, avoid concurrent edits, and keep ordinary backups. Timeouts and catchable interruptions trigger cleanup, but power loss or uncatchable termination can interrupt restoration. A descendant that detaches from its process group can outlive timeout cleanup.
- If `.dv/restoration.json` remains, stop editing and preserve that file. Inspect its recorded commit and paths together with `git status` and your local changes. Recover deliberately before retrying; do not use a blanket reset or delete the recovery record to bypass the check.
- Logs, reports, and runtime attachments are local plaintext. They may contain sensitive command output. The default `.dv/` directory is locally Git-ignored; custom item directories must also be ignored. Inspect artifacts before sharing them. Git ignore rules are not access controls or encryption.
- Content hashes detect drift between evidence and current inputs. Anyone with write access to the repository and evidence can forge records. Reviewer identity, model independence, fresh-context isolation, and human approval are self-reported attestations.
- Claude Code hooks catch supported direct Bash operations. Arbitrary scripts, aliases, subprocesses, other tools, races after checking, and package publishing are outside their enforcement scope. Exact-command exemptions skip the gate. The Stop retry escape allows a session to end without approving the change.

Internal Git operations force `GIT_NO_LAZY_FETCH=1` and an empty `GIT_ALLOW_PROTOCOL`, preventing promisor-object reads from contacting a remote. Missing objects fail locally; prepare them yourself. Partial clones whose Git lacks `--no-lazy-fetch` support are rejected. The supported hook minimum is Claude Code 2.1.310; PreToolUse failures and timeouts block via `onFailure: "block"`. Hook installer output excludes existing settings, including environment values.

`dv` itself does not fetch remotes, install dependencies, upload evidence, send telemetry, call a model, or publish changes. User-configured programs may do those things. Review repository configuration before running a check, particularly in an unfamiliar checkout.

## Reporting a vulnerability

Use the repository's private **Report a vulnerability** option if it is enabled. If no private reporting channel is available, open an issue requesting a private contact without including an exploit, a credential, or sensitive logs. Do not send reports to an address guessed from Git history.

A useful report includes the affected version, Python/Git/OS versions, a minimal synthetic reproduction, expected and observed behavior, and the potential impact. Remove credentials, personal data, and private paths from evidence. Do not test against someone else's repository or services.

For the 0.2.x line, fixes target the current development branch. There are no maintenance or response-time guarantees. A successful gate is evidence to inspect, not a security certification.
