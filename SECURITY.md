# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's security-advisory feature for this
repository. Do not open a public issue containing an exploit, credential, private source code, or
task logs.

## Security model

Agent Orchestrator runs third-party coding CLIs on the user's machine. Those processes inherit the
permissions and credentials available to their own executables and configuration. The runner adds
coordination, observability, timeouts, scope-drift detection, and review guidance; it is not an OS
sandbox and cannot undo a destructive command.

Use a dedicated worktree, keep repository credentials out of prompts, choose the least-permissive
provider mode, deny sensitive paths, and review all diffs before committing or pushing. A prompt
transported as an argv value may be visible to other local processes. Runtime task packets and logs
can contain private repository content and must not be published with bug reports.

The plugin never needs provider API keys. Authenticate each CLI using its documented credential
store and never place tokens in adapter JSON, task packets, fixtures, or command output.
