from typing import List, Dict
from collections import defaultdict

from .git import Commit, Patch, Sha, GitFailed, split_author, Git


def port_user_merge(M: Commit, B: list[Sha], Bʹ: list[Sha], *, git: Git) -> Commit | None:
    """
    Attempt to port a user merge from the old baselines B to the
    new ones Bʹ, based on patch-ids.

    Strategy:
        * For each parent A of the merge M,
            * Determine which patches in Bʹ..A are essential.  If P can
            be cleanly reverted both from A and M, it is not essential.
            Otherwise it is.
            * Starting from the merge-base of B and Bʹ, find the first
            commit Aʹ in Bʹ that contains all the essential patches of A.
            * For each patch in Aʹ and not in A, apply it to  M.
            * For each patch not in Aʹ and in A, revert it from M.

    The tree resulting from applying/reverting patches to M is the
    new conflict resolution tree between the parents A₁ʹ and A₂ʹ.
    Create a merge commit with those parents and that tree.
    """

    # Common ancestor of all old and new baselines
    mb = [f"^{s}" for s in git.all_merge_bases(*B, *Bʹ, none_ok=True)]
    if len(mb) > 1:
        return None  # patching code below assumes single point of divergence

    new_parents: List[Sha] = []
    for A in M.parents:
        # Bʹ..A: patches in A not yet in any new baseline (used for essentialness)
        patches = git.patches_and_merges(A, *(f"^{sha}" for sha in Bʹ), reverse=True)
        if not patches:
            new_parents.append(A)  # A is already within the new baselines — keep it as-is
            continue

        # Determine which patches are essential: cannot be reverted from both A and M.
        essential_patch_ids: set[str] = set()
        for p in patches:
            if not isinstance(p, Patch):
                if not git.is_conflicted(p):
                    continue
                return None  # TODO handle stacked user merges
            if not (
                git.check_apply(p, to=A, reverse=True)
                and git.check_apply(p, to=M.sha, reverse=True)
            ):
                essential_patch_ids.add(p.patch_id)
        if not essential_patch_ids:
            return None

        # Find Aʹ, the first commit in Bʹ with all the essential patches in A
        found_by_sha: Dict[Sha, set[str]] = defaultdict(set)
        for commit in git.commits(*Bʹ, *mb, reverse=True):
            found = found_by_sha[commit.sha]
            if not commit.is_merge:
                found.add(git.patch_id(commit.sha))
            for parent in commit.parents:
                found.update(found_by_sha[parent])
            if essential_patch_ids <= found:
                Aʹ = commit.sha
                break
        else:
            return None

        new_parents.append(Aʹ)

    # Make sets of patches to compare.
    left: set[Patch] = set()
    right: set[Patch] = set()
    for parents, S in zip([M.parents, new_parents], [left, right]):
        for c in git.patches_and_merges(*parents, *mb):
            if isinstance(c, Patch):
                S.add(c)
            elif git.is_conflicted(c):
                return None  # can't handle conflicts, yet.

    # Build the new resolution tree by patching M
    with git.temp_index(tree=M.tree) as git2:
        try:
            for p in right - left:
                git2.apply_patch_to_index(p)
            for p in left - right:
                git2.apply_patch_to_index(p, reverse=True)
        except GitFailed:
            return None
        new_tree = Sha(git2.cmd(["git", "write-tree"], quiet=True).strip())

    # Create the ported merge commit, preserving the original author
    author = split_author(M.author)
    parent_args = [arg for p in new_parents for arg in ("-p", p)]
    with git.temp_env(
        GIT_AUTHOR_NAME=author.name,
        GIT_AUTHOR_EMAIL=author.email,
        GIT_AUTHOR_DATE=author.date,
    ) as git2:
        sha = Sha(git2("commit-tree", "-m", M.message, new_tree, *parent_args).strip())
        return git2.commit(sha)
