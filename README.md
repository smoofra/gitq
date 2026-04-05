# Git Queues

##  ⚠️ 🪏 Work In Progress 🪏 ⚠️ 

This is currently a work in progress.  Planned features for `git-queue` include:

* Baseline conflict resolution.   If baselines conflict a queue can contain
  a merge commit to resolve the conflict.  Rebase should somehow be able to
  deal with this in reasonable cases.

*  Unapplied patches.  If rebase fails due to conflicts, give the user the
   choice to save commits as patch files to be applied later.

* Queue history.   Allow queues to be committed to git history so previous
  versions of the queue are not lost when history is edited.   This is
  intended to be used for long-standing patch sets with no prospect of
  being merged upstream.

## Description

This is my second attempt to make a git-based patch queue tool.

It's similar to [`git-pq`][gitpq], [Quilt][quilt], [Stacked Git][stgit],
[TopGit][topgit], [Mercurial Queues][mq], and others.

The goal is to assist the user in composing, and maintaining a
well-organized queue of patches against a baseline codebase.

## Use cases

### Personal Integration Branch

You have multiple topic branches in progress, at various levels of
maturity.   One branch may be a very early work in progress, another may be
in the initial stages of build-and-test in the CI environment, another may
be almost done code review and ready to merge.    In order to anticipate
merge conflicts, functional interactions, and for general convenience, you
want to do all your local development builds on an integration branch with
all your open work merged together.   After changes are made to the topic
branches, you want to easily update the integration branch and keep going.

### Submission Queue

You have a long series of changes you want to push to an upstream project.
The upstream, however does not accept long series of changes.   Their code
review process looks at one small patch at a time.   As each patch goes
through the review process, changes are made and the reset of the queue
must be updated accordingly.

### Long Term Patch Set

You're maintaining a set of patches against an upstream project.   For
whatever reason, most of these patches are never going to be merged
upstream.   You need to repeatedly rebase this patch set onto new versions
of the project.   Essentially, the patches are your source code.   You
would like to use a version control system to track changes to the patches.

## But Why?

None of the existing tools do quite what I want.   In particular, Git Queues has
the following distinguishing characteristics

* Queues can have multiple baselines.

* A queue is a git branch.  All queue information is stored in a single
  branch.  There are no auxiliary refs, no information stored anywhere
  else.   A queue can be pushed and pulled to remotes as an ordinary
  branch.   There is no `git queue push` or `git queue pull`.

* Each patch is a git commit.   As much as possible, ordinary git commands
  are used to manipulate patches.

* The commit history of a queue feels like a git commit history.  That is,
  commits are user-curated checkpoints, with user-written commit messages.
  The history is not a detailed log of every micro-operation that was used
  to create a queue.   The data model is as simple as possible.   Git is a
  "stupid" content tracker, because it tracks versions of the content of a
  source directory.   There is no specific representation in git to, for
  example, move a file.  The contents of the file are simply moved to a new
  location.  Git Queues aims to track the content of a patch series in the
  same spirit that Git tracks the content of a directory.


## `git-swap`

`git-swap` re-orders commits like `git rebase -i`, but is easier to use.
In particular, conflicts must often be resolved twice when using rebase, but
not with `git-swap`.  `git-swap` reverses the order of two adjacent commits,
while holding the final content constant.

```
git swap [OPTIONS] [COMMIT]
```

Swaps COMMIT with COMMIT^ (i.e. moves COMMIT one step earlier in history).
Defaults to HEAD.

Options:

- `--keep-going`, `-k` — push COMMIT as far down the stack as it will go,
  stopping only when a swap would fail due to conflicts or a baseline boundary.
- `--up` — swap COMMIT with the commit above it (i.e. move COMMIT one step
  later in history) rather than the default downward direction.
- `--edit`, `-e` — if conflicts arise, suspend so the user can resolve them
  manually, then resume with `git swap --continue`.
- `--continue`, `-c` — resume after conflicts have been resolved.
- `--abort` — give up and restore git to its original state.
- `--stop` — abandon the current swap operation and push remaining commits
  back onto the branch.
- `--squash` — instead of completing the swap, squash the two commits together.
- `--fixup` — like `--squash`, but discard the lower commit's message.
- `--status` — print the current operation status.

When `git-swap` is used on a queue branch, it will not swap past the queue's
baseline boundary.


## `git-queue`

`git-queue` manages a queue.  A queue is a git branch with one or more
baselines, recorded in a `.git-queue` file committed at the root of the branch.

```
git queue SUBCOMMAND [OPTIONS]
```

### `git queue init [--branch BRANCH] [--title TITLE] BASELINE...`

Initialize a queue on the current branch (or create a new branch with
`--branch`/`-b`) with one or more baselines.  Each BASELINE is a branch,
tag, or commit.

### `git queue rebase [--add BASELINE] [--remove BASELINE]`

Rebase the queue onto its baselines, incorporating any upstream changes.
If a baseline branch is itself a queue managed by this tool, it will be
recursively rebased first.

Use `--add` to incorporate an additional baseline, or `--remove` to drop one,
at the same time as rebasing.

If conflicts arise during cherry-picking, the operation suspends so the user
can resolve them, then resume with `git queue continue`.

### `git queue add BASELINE...`

Add one or more baselines and rebase.

### `git queue remove BASELINE...`

Remove one or more baselines and rebase.

### `git queue continue`

Resume a suspended operation.

### `git queue abort`

Abort a suspended operation and restore git to its previous state.

### `git queue status`

Print the current operation status.

### `git queue tidy`

Normalize and rewrite the `.git-queue` file in the current branch.


## `git-edit`

`git-edit` checks out a specific commit for editing, then replays everything
above it when you continue — similar to `git rebase -i` with `edit`.

```
git edit [COMMIT]
```

Detaches HEAD at COMMIT, suspending so the user can amend it.  When done,
resume with `git edit --continue` to replay all commits that were above
COMMIT back on top.

Options:

- `--continue`, `-c` — resume after edits are complete.
- `--abort` — give up and restore git to its original state.
- `--status` — print the current operation status.


## `git-split`

`git-split` splits a single commit into two or more commits.   It allows
the user to add a additional commits, while holding the final content
constant.

```
git split [COMMIT]
```

Checks out COMMIT with its changes staged (via `git reset --soft HEAD^`),
suspending so the user can make one or more new commits.  After the user
continues, COMMIT will be restored with it's original content, and
`git-split` will suspend again so the user can amend the commit message
with `git commit --amend`.   After continuing again, it  replays the
remaining commits from above COMMIT on top.

Options:

- `--continue`, `-c` — resume at each suspension point.
- `--abort` — give up and restore git to its original state.
- `--status` — print the current operation status.


## `git-squash`

`git-squash` squashes a commit into its parent.

```
git squash [--fixup] COMMIT
```

Combines COMMIT with COMMIT^.  Opens an editor to compose the combined commit
message (like `git commit --squash`).  With `--fixup`/`-f`, discards COMMIT's
message and keeps only the parent's (like `git commit --fixup`).


[stgit]: https://stacked-git.github.io/
[quilt]: https://linux.die.net/man/1/quilt
[gitpq]: https://github.com/smoofra/git-pq
[topgit]: https://mackyle.github.io/topgit/topgit.html
[mq]: https://hgbook.red-bean.com/read/managing-change-with-mercurial-queues.html