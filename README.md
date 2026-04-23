# GitQ

##  ⚠️ 🪏 Work In Progress 🪏 ⚠️ 

This is currently a work in progress.  Planned features for `git-queue` include:

*  Unapplied patches.  If rebase fails due to conflicts, give the user the
   choice to save commits as patch files to be applied later.

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

## FAQ

### Why?

None of the existing tools do quite what I want.  In particular, GitQ
has the following distinguishing characteristics

* Queues can have multiple baselines.

* A queue is a git branch.  All queue information is stored in a single
  branch.  There are no auxiliary refs, no information stored anywhere
  else.   A queue can be pushed and pulled to remotes as an ordinary
  branch.   There is no `git queue push` or `git queue pull`.

* Each patch is a git commit.   As much as possible, ordinary git commands
  are used to manipulate patches.

* The commit history of a queue *is* a git commit history.  You can use
  `git log`, or `tig` to inspect it.   You can use `git diff` to diff it.

  Commits are user-curated checkpoints, with user-written commit messages.
  The history is not a detailed log of every micro-operation that was used
  to create a queue.   The data model is as simple as possible.   Git is a
  "stupid" content tracker, because it tracks versions of the content of a
  source directory.   There is no specific representation in git to, for
  example, move a file.  The contents of the file are simply moved to a new
  location.  GitQ aims to track the content of a patch series in the same
  spirit, and *with the same mechanism* that Git tracks the content of a
  directory.

### Is this a new version control system based on git?

No!  GitQ adds new functionality to Git.  It automates workflows that are
awkward and manual in Git.

### Is this a alternate user interface to git?

No! GitQ does not wrap Git's existing functionality in a new interface. You
do not need learn a new set of commands overlapping with the Git commands
you already know to use GitQ.   Use normal git commands, and mix them
freely with GitQ commands.   GitQ will print out a log of Git commands it
executes, so you can understand what it's doing.

## `git-queue`

`git-queue` manages a queue.  A queue is a bunch of patches.

Patches are ordinary, non-merge git commits.

The patches in a queue sit on one or more *baselines*.  Baselines are
commits which the queue is based on in terms of git history, but are not
part of the queue.   Baselines may be added, removed, or refreshed. Commits
in the old baselines may be left behind, but the patches in the queue are
carried forward.

If there are more than one baselines, then the queue's patches will sit on
merge commits brining them together.

If the baselines conflict, the queue will also need to contain *user merges*.

User merges are merge commits prepared by the user to reconcile conflicting
baselines.   When the queue is rebased, user merges will be carried forward
if they can still resolve conflicts in the baselines.   If not, the user
will need to prepare new ones.   If the baselines no longer conflict, user
merges may be left behind during a rebase.

### Queue Metadata

A queue is a branch with a `.git-queue` file at the root, or a *bare branch*
where the metadata is stored in git config (`branch.<name>.git-queue`) instead.
Baselines are recorded in the queue metadata.  They specify the shas of the
current baseline as well as the branch names they came from.  This information
is used to rebase the queue.

Storing metadata in a `.git-queue` file means it travels with the branch —
push and pull work normally, and anyone who clones or fetches the branch has
everything they need to rebase it.

Bare branches keep the commit history clean — no baseline commits and no
`.git-queue` file appear in the branch, so the history looks exactly like an
ordinary topic branch.  The tradeoff is that the metadata lives only in local
git config and is not pushed to remotes.

Create a bare queue with `git queue init --bare`, or convert an existing
queue with `git queue rebase --bare` / `git queue rebase --no-bare`.


```
git queue SUBCOMMAND [OPTIONS]
```

### `git queue init [--bare] BASELINE...`

Initialize a queue.  Each BASELINE is a branch, tag, or commit.  `--bare`
stores metadata in git config instead of a `.git-queue` file.

### `git queue rebase [--bare | --no-bare]`

Rebase the queue onto its baselines, incorporating any upstream changes.
`--bare` converts to a bare branch; `--no-bare` converts back to a `.git-queue` file.

### `git queue add [--bare | --no-bare] BASELINE...`

Add one or more baselines and rebase.

### `git queue remove [--bare | --no-bare] BASELINE...`

Remove one or more baselines and rebase.

### `git queue continue`

Resume a suspended operation.

### `git queue abort`

Abort a suspended operation and restore git to its previous state.

### `git queue status`

Print the current operation status.

### `git queue tidy`

Normalize and rewrite the `.git-queue` file in the current branch.

### `git queue commit`

Commit changes to a queue to a *historiography*.

Git tracks changes to files in a commit history.  `git-queue` can track
changes to a queue to a history of histories, aka a historiography.

A historiography is itself represented as a git history, but the files
being tracked represent a queue, that is a set of changes from a specified
baseline.

This is done by serializing the commits in the queue (but not the
baselines), into patch files, in almost the same format that
`git format-patch` uses.   Unlike `git-format-patch`, git `git-queue`
records the committer, committer date, and parents of each commit in the
patch file.   This means that the patch files recorded in a historiography
have enough information to reconstruct the exact original commit shas that
they were created from.


## `git-swap`

`git-swap` reverses the order of two adjacent commits, while holding the
final content constant.


## `git-edit`

`git-edit` checks out a specific commit for editing, then replays everything
above it when you continue — similar to `git rebase -i` with `edit`.


## `git-split`

`git-split` splits a single commit into two or more commits, while holding
the final content constant.


## `git-squash`

`git-squash` squashes a commit into its parent.


## `git-drop`

`git-drop` deletes a commit from history, replaying all commits above it.



[stgit]: https://stacked-git.github.io/
[quilt]: https://linux.die.net/man/1/quilt
[gitpq]: https://github.com/smoofra/git-pq
[topgit]: https://mackyle.github.io/topgit/topgit.html
[mq]: https://hgbook.red-bean.com/read/managing-change-with-mercurial-queues.html