# Pipeline checkpoints and recovery (#31)

Each fresh run creates `outputs/.runs/<session>/state.json` (under `OUTPUTS_DIR`
when configured). The CLI logs this path at startup and prints a resume command
on failure. Resume from the same working directory:

```sh
caerus run --resume "outputs/.runs/<session>/state.json"
```

Library callers use `await orchestrate(resume_from=Path(...))`; the library never
starts an event loop. Input and resume are mutually exclusive. Ordinary runs
always start a new session, even for identical job text in the same second.

## State ownership and commit boundaries

The orchestrator owns stage advancement. A versioned, typed JSON envelope owns
the original input, session identity, working directory, Notion policy and
progress, and one `ApplicationPackage` containing stage results. There is no
second set of agent results to synchronize. Successful transitions are:

`created -> parse_jd -> company_research -> resume_selection -> cover_letter -> write_outputs -> completed`

Successful stage metrics are included in that package. A checkpoint is committed
after the stage's metrics context finishes, so recovered metrics show completed
stages and keep their original request/token counts. Failed or uncommitted
attempts are not a durable cost ledger. The final `metrics.json` is written before
marking the run completed; a failure writing it can be retried without repeating
agent work, publication, or an already checkpointed Notion attempt.

The synthetic `caerus demo` replay stays offline and does not create resumable
checkpoints or read production settings. Its existing metrics and artifacts use
the same stage execution path with the supplied replay backend.

The store validates required predecessor results before saving or resuming. It
writes a temporary file in the same directory, flushes and fsyncs the file, then
atomically replaces the checkpoint. A failed write leaves the previous snapshot
usable. An OS lock on a persistent sidecar prevents concurrent writers to one
run; the operating system releases ownership if the process dies. Do not remove
or replace the lock file while a run is active. Filesystem operations remain
synchronous, as does the existing output writer.

Failures and cancellation propagate with their original exception types. The
last successfully committed stage remains authoritative; unfinished work runs
again. Checkpoint failures stop the pipeline rather than continuing paid work
without recovery. Unknown versions, malformed JSON, and inconsistent progress
fail before agent calls. Version 1 has no migration from older diagnostic-only
staging directories, which never contained complete agent state.

## Recovery behavior and limits

- A PDF conversion failure retains the parsed JD, research, resume selection,
  approved letter, and optimization diagnostics. Resume retries output writing
  from that saved package. Existing output staging diagnostics remain available.
- The output writer regenerates and validates the complete document bundle in a
  new staging directory. It does not reuse a partially generated PDF or DOCX.
- Once publication is checkpointed, resume preserves those artifacts and skips
  rendering. Missing published files cause an explicit error; restore them or
  start a fresh run. A completed resume returns the saved package without work.
- Notion is marked `in_flight` durably before submission and `done` after return.
  A crash between those commits has an uncertain remote outcome. Resume refuses
  to submit again automatically. Inspect Notion, then use `--no-notion` to finish
  locally. The existing Notion writer treats service failures as nonfatal, so
  `done` means the attempt returned, not that a page necessarily exists.
- A saved `--no-notion` policy persists. Adding it on resume is also permanent
  for that run; completed runs cannot be used to submit a new Notion page.
- Completed agent results are snapshots, not a cache keyed by current settings.
  Resume does not invalidate them after profile, prompt, resume, model, or search
  changes. Pending stages use current configuration and local files. Start a new
  run when those changes should affect generated content.
- A hard crash after an external effect but before its checkpoint can repeat
  unfinished work. In particular, output publication can leave an additional
  complete bundle in that narrow window. Exactly-once remote writes and atomic
  commits across both checkpoints and output directories are outside this design.
- Atomic replacement protects against partial JSON on process interruption. It
  is not a filesystem-independent guarantee against power loss, network-share
  failures, or external edits. Use a local filesystem supporting file locks and
  atomic replacement.

Checkpoint files contain job text, research, and personal application content.
They contain no configuration dump or API keys. They remain local under the
already ignored output root; secure and delete them like the generated documents.
If you configure a different output root, keep it outside version control.

## Alternatives and validation

In-memory retries cannot survive process exit. Reusing diagnostic staging alone
cannot restore the research or agent outputs. Independent stage files require a
manifest and multi-file consistency; a database would add an unnecessary service
or migration layer for a local, single-run CLI. One bounded JSON snapshot per run
keeps recovery inspectable at the cost of rewriting the accumulated package.

Tests exercise the public orchestration boundary with private temporary output
roots. They fail individual stages, resume, and assert both the resulting content
and absence of repeated completed work. A fresh-process regression uses the real
output publisher around controlled PDF conversion/validation boundaries; existing
output-writer tests cover document validation itself. Other tests cover cancelled
research, malformed state, atomic replacement failure, process lock contention,
missing artifacts, completed replay, Notion ambiguity, CLI errors, and unique run
identities. No live providers or shared processes are needed.
