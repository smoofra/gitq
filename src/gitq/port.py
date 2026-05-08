from typing import List, Dict
from collections import defaultdict
from typing import NamedTuple

from .git import Commit, Patch, Sha, GitFailed, split_author, Git, add_trailer
from .output import Output
from .continuations import Heading

Merge = Commit


class State(NamedTuple):
    tree: Sha
    applied: frozenset[Patch | Merge]

    def add(self, c: Patch | Merge, *, reverse: bool = True, tree: Sha | None = None) -> "State":
        if reverse:
            return State(tree or self.tree, self.applied - {c})
        else:
            return State(tree or self.tree, self.applied | {c})


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
    mb = [f"^{s}^@" for s in git.all_merge_bases(*B, *Bʹ, none_ok=True)]
    if len(mb) > 1:
        Output.print("Can't handle multiple merge bases")
        return None  # patching code below assumes single point of divergence

    new_parents: List[Sha] = []
    for A in M.parents:
        # Bʹ..A: patches in A not yet in any new baseline (used for essentialness)
        A_commits = git.patches_and_merges(A, *(f"^{sha}" for sha in Bʹ))
        commits: dict[Sha, Patch | Merge] = {c.sha: c for c in A_commits}
        if not A_commits:
            new_parents.append(A)  # A is already within the new baselines — keep it as-is
            continue

        # Determine which patches are essential: cannot be reverted from both A and M.
        essential_patch_ids: set[str] = set()
        todo: List[Patch | Merge] = [A_commits[0]]
        seen: set[Sha] = set()
        with git.temp_index(tree=M.sha) as gitM, git.temp_index(tree=A) as gitA:
            while todo:
                commit = todo.pop()
                if commit.sha in seen:
                    continue
                seen.add(commit.sha)
                if not isinstance(commit, Patch):
                    if not git.is_conflicted(commit):
                        todo.extend(commits[sha] for sha in commit.parents if sha in commits)
                        continue
                    if base := git.merge_base(*commit.parents):
                        # try to back out both sides together
                        if gitA.check_apply_diff_to_index(
                            base, commit.sha, reverse=True
                        ) and gitM.check_apply_diff_to_index(base, commit.sha, reverse=True):
                            gitA.apply_diff_to_index(base, commit.sha, reverse=True)
                            gitM.apply_diff_to_index(base, commit.sha, reverse=True)
                            if base in commits:
                                todo.append(commits[base])
                            continue
                    Output.print("Can't handle conflict", commit.summary)
                    return None  # TODO handle stacked user merges
                if not (
                    gitA.check_apply_patch_to_index(commit, reverse=True)
                    and gitM.check_apply_patch_to_index(commit, reverse=True)
                ):
                    essential_patch_ids.add(commit.patch_id)
                else:
                    gitA.apply_patch_to_index(commit, reverse=True)
                    gitM.apply_patch_to_index(commit, reverse=True)
                todo.extend(commits[sha] for sha in commit.parents if sha in commits)
            if not essential_patch_ids:
                Output.print("Found no essential patches for", A_commits[0].summary)
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
            Output.print("Failed to find Aʹ for", A_commits[0].summary)
            return None

        new_parents.append(Aʹ)

    # Read all the commits on both sides
    commits = dict()
    left: set[Patch | Merge] = set()
    right: set[Patch | Merge] = set()
    left_topo = git.patches_and_merges(*M.parents, *mb)
    right_topo = git.patches_and_merges(*new_parents, *mb)
    for topo, S in zip([left_topo, right_topo], [left, right]):
        for commit in topo:
            S.add(commit)
            commits[commit.sha] = commit

    def apply(state: State | None, head: Patch | Merge, *, reverse: bool = False) -> State | None:
        if state is None:
            return None
        if (not reverse) == (head in state.applied):
            return state  # already applied

        if not git.is_conflicted(head):
            if isinstance(head, Patch):
                with git.temp_index(tree=state.tree) as git2:
                    verb = "Revert" if reverse else "Apply"
                    Output.print(verb, head.summary)
                    try:
                        git2.apply_patch_to_index(head, reverse=reverse)
                    except GitFailed:
                        Output.print("Failed.")
                        return None
                    state = state.add(head, tree=git2.write_tree(), reverse=reverse)
            else:
                state = state.add(head, reverse=reverse)
            return state

        mb = git.all_merge_bases(*head.parents, none_ok=True)
        if len(mb) > 1:
            Output.print("Can't handle multi-merge-base:", head.summary)
            return None

        for order in head.parents, reversed(head.parents):
            X, Y = (commits[sha] for sha in order)
            side = "left" if order is head.parents else "right"

            if reverse:
                with (
                    Heading(f"Reverting merge ({side} first) {head.summary}"),
                    git.temp_index(state.tree) as git2,
                ):
                    Output.print(f"Revert [{Y.summary}]..[{head.summary}]")
                    try:
                        git2.apply_diff_to_index(Y.sha, head.sha, reverse=True)
                        s = update_history(state, git2.write_tree(), head, X, mb=mb, reverse=True)
                    except GitFailed:
                        Output.print("Failed.")
                        continue
                    return s  # this could return multiple correct answers.. switch to List monad?

            else:
                with Heading(f"Applying merge ({side} first) {head.summary}"):
                    if (s1 := apply_history(state, X, mb=mb)) and (
                        s2 := apply_history(s1, Y, reverse=True, mb=mb)
                    ):
                        with git.temp_index(s2.tree) as git2:
                            try:
                                Output.print(f"Apply [{X.summary}]..[{head.summary}]")
                                git2.apply_diff_to_index(X.sha, head.sha)
                                return update_history(s2, git2.write_tree(), head, Y, mb=mb)
                            except GitFailed:
                                Output.print("Failed.")
                                continue

        return None

    def apply_parents(state: State | None, head: Commit, mb: list[Sha], reverse: bool = False):
        if state is None:
            return None
        for sha in head.parents:
            if parent := commits.get(sha):
                state = apply_history(state, parent, reverse=reverse, mb=mb)
        return state

    def update_history(
        state: State | None,
        tree: Sha,
        merge: Merge,
        parent: Patch | Merge,
        *,
        mb: list[Sha],
        reverse: bool = False,
    ) -> State | None:
        "Update state to reflect that mb..parent and merge have been applied, or reverted."
        if state is None:
            return None
        state = state.add(merge, tree=tree, reverse=reverse)
        todo: list[Sha] = [parent.sha]
        while todo:
            sha = todo.pop()
            if sha not in mb and (commit := commits.get(sha)):
                state = state.add(commit, reverse=reverse)
                todo.extend(commit.parents)
        return state

    def apply_history(
        state: State | None, head: Patch | Merge, *, mb: list[Sha] = [], reverse: bool = False
    ) -> State | None:
        if state is None:
            return None
        if head.sha in mb:
            return state

        # In order to have the best chance of avoiding commutation failures,
        # apply patches in forward order but revert them in backwards order.

        if not git.is_conflicted(head):
            if reverse:
                state = apply(state, head, reverse=True)
                state = apply_parents(state, head, mb=mb, reverse=True)
            else:
                state = apply_parents(state, head, mb=mb)
                state = apply(state, head)
            return state

        if len(head.parents) != 2:
            Output.print("Can't handle conflicted octopus:", head.summary)
            return None

        old_mb = mb
        mb = git.all_merge_bases(*head.parents, none_ok=True)
        if len(mb) > 1:
            Output.print("Can't handle multi-merge-base:", head.summary)
            return None

        if reverse:
            state = apply(state, head, reverse=True)
            if mb and (base := commits.get(mb[0])):
                state = apply_history(state, base, mb=old_mb, reverse=True)
        else:
            if mb and (base := commits.get(mb[0])):
                state = apply_history(state, base, mb=old_mb)
            state = apply(state, head)

        return state

    state: State | None = State(M.tree, frozenset(left))

    # Revert any commits from the left side that do not occur in the right
    for commit in left_topo[1:]:
        if commit not in right:
            state = apply(state, commit, reverse=True)
            if state is None:
                Output.print("Failed to revert", commit.summary)
                return None

    # Apply all commits on the right that do not occur on the left
    for sha in new_parents:
        state = apply_history(state, commits[sha])
        if state is None:
            Output.print("Failed to apply", commits[sha].summary)
            return None

    assert state is not None and state.applied == right

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
