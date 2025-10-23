# Git Queues

##  ⚠️ 🪏 Work In Progress 🪏 ⚠️ 

This is currently a work in progress and doesn't do much yet.

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

`git-swap` can re-order commits like `git rebase -i`, but it is, in my
opinion easier to use.   In particular, conflicts must often be resolved
twice when using rebase, but not with `git-swap`.  `git-swap` reverses the
order of two adjacent commits, while holding the final content constant.

## `git-queue`

`git-queue` is manipulates a queue.   A queue is a git branch with a baseline,
which is specified in the `.git-queue` file.


[stgit]: https://stacked-git.github.io/
[quilt]: https://linux.die.net/man/1/quilt
[gitpq]: https://github.com/smoofra/git-pq
[topgit]: https://mackyle.github.io/topgit/topgit.html
[mq]: https://hgbook.red-bean.com/read/managing-change-with-mercurial-queues.html