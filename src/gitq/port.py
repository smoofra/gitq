from typing import List, Dict
from collections import defaultdict
from typing import NamedTuple

from .git import Commit, Patch, Sha, GitFailed, split_author, Git, add_trailer
from .output import Output
from .continuations import Heading

Merge = Commit


class State(NamedTuple):
    tree: Sha
    left: frozenset[Patch | Merge]


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
        patches = git.patches_and_merges(A, *(f"^{sha}" for sha in Bʹ))
        if not patches:
            new_parents.append(A)  # A is already within the new baselines — keep it as-is
            continue

        # Determine which patches are essential: cannot be reverted from both A and M.
        essential_patch_ids: set[str] = set()
        with git.temp_index(tree=M.sha) as gitM, git.temp_index(tree=A) as gitA:
            for commit in patches:
                if not isinstance(commit, Patch):
                    if not git.is_conflicted(commit):
                        continue
                    return None  # TODO handle stacked user merges
                if not (
                    gitA.check_apply_patch_to_index(commit, reverse=True)
                    and gitM.check_apply_patch_to_index(commit, reverse=True)
                ):
                    essential_patch_ids.add(commit.patch_id)
                else:
                    gitA.apply_patch_to_index(commit, reverse=True)
                    gitM.apply_patch_to_index(commit, reverse=True)
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

    # Read all the commits on both sides
    commits: dict[Sha, Patch | Merge] = dict()
    left: set[Patch | Merge] = set()
    right: set[Patch | Merge] = set()
    left_topo = git.patches_and_merges(*M.parents, *mb)
    right_topo = git.patches_and_merges(*new_parents, *mb)
    for topo, S in zip([left_topo, right_topo], [left, right]):
        for commit in topo:
            S.add(commit)
            commits[commit.sha] = commit

    def apply_patch(state: State | None, patch: Patch, *, reverse: bool = False) -> State | None:
        if state is None:
            return None
        with git.temp_index(tree=state.tree) as git2:
            verb = "Revert" if reverse else "Apply"
            Output.print(verb, patch.summary)
            try:
                git2.apply_patch_to_index(patch, reverse=reverse)
            except GitFailed:
                Output.print("Failed.")
                return None
            L = state.left - {patch} if reverse else state.left | {patch}
            return State(git2.write_tree(), L)

    def add_history_parents(
        state: State | None, head: Commit, mb: list[Sha], reverse: bool = False
    ):
        if state is None:
            return None
        for sha in head.parents:
            if parent := commits.get(sha):
                if s := add_history(state, parent, reverse=reverse, mb=mb):
                    state = s
                else:
                    return None
        return state

    def add_history(
        state: State | None, head: Patch | Merge, *, mb: list[Sha] = [], reverse: bool = False
    ) -> State | None:
        if state is None:
            return None
        if head.sha in mb:
            return state

        if not git.is_conflicted(head):
            # In order to have the best chance of avoiding commutation failures,
            # apply patches in forward order but revert them in backwards order.
            if reverse:
                if isinstance(head, Patch) and state and head in state.left:
                    state = apply_patch(state, head, reverse=True)
                state = add_history_parents(state, head, mb=mb, reverse=True)
            else:
                state = add_history_parents(state, head, mb=mb)
                if isinstance(head, Patch) and state and head not in state.left:
                    state = apply_patch(state, head)
            return state

        if len(head.parents) != 2:
            Output.print("Can't handle conflicted octopus:", head.summary)
            return None

        old_mb = mb
        mb = git.all_merge_bases(*head.parents, none_ok=True)
        if len(mb) > 1:
            Output.print("Can't handle multi-merge-base:", head.summary)
            return None

        if mb and not reverse and (base := commits.get(mb[0])):
            state = add_history(state, base, mb=old_mb)
            if not state:
                Output.print("Failed to apply history at", base.summary)
                return None

        for order in head.parents, reversed(head.parents):
            X, Y = (commits[sha] for sha in order)
            verb = "Reverting" if reverse else "Applying"
            side = "left" if order is head.parents else "right"
            with Heading(f"{verb} merge ({side} first) {M.summary}"):
                if (s1 := add_history(state, X, mb=mb)) and (
                    s2 := add_history(s1, Y, reverse=True, mb=mb)
                ):
                    with git.temp_index(s2.tree) as git2:
                        try:
                            if reverse:
                                Output.print(f"Revert [{Y.summary}]..[{head.summary}]")
                                git2.apply_diff_to_index(Y.sha, head.sha, reverse=True)
                                state = State(git2.write_tree(), s2.left - {head})
                            else:
                                Output.print(f"Apply [{X.summary}]..[{head.summary}]")
                                git2.apply_diff_to_index(X.sha, head.sha)
                                state = State(git2.write_tree(), s2.left | {head})
                            break
                        except GitFailed:
                            Output.print("Failed.")
                            continue
        else:
            return None

        if mb and reverse and (base := commits.get(mb[0])):
            state = add_history(state, base, mb=old_mb, reverse=True)
            if not state:
                Output.print("Failed to apply history at", base.summary)
                return None

        return state

    state: State | None = State(M.tree, frozenset(left))

    # Remove any patches from the left side that do not occur on the right side
    for commit in left_topo:
        if commit in right:
            continue
        if git.is_conflicted(commit):
            return None  # TODO handle stacked user merges
        if not isinstance(commit, Patch):
            continue
        state = apply_patch(state, commit, reverse=True)

    # Add any history, including merges, on the right side that doesn't occur on the left
    for sha in new_parents:
        state = add_history(state, commits[sha])

    if state is None:
        return None

    # Create the ported merge commit, preserving the original author
    author = split_author(M.author)
    parent_args = [arg for p in new_parents for arg in ("-p", p)]
    message = add_trailer(M.message, "GitQ-Ported-From", M.sha)
    with git.temp_env(
        GIT_AUTHOR_NAME=author.name,
        GIT_AUTHOR_EMAIL=author.email,
        GIT_AUTHOR_DATE=author.date,
    ) as git2:
        sha = Sha(git2("commit-tree", "-m", message, state.tree, *parent_args).strip())
        return git2.commit(sha)
