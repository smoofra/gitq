"""Tests for port_user_merge: porting a user-created conflict-resolution merge
to updated baselines when the old baseline commits are no longer ancestors of
the new ones (e.g. because the baseline was rebased / force-pushed)."""

from .fixtures import Git, repo

_ = repo


def _setup_conflict(repo: Git):
    """
    Build a queue with two baselines that are later updated to conflict,
    with a user-resolved merge on top.

    After setup the queue history looks like:
        0 ─ a ─ a2 \
                     resolved_conflicts ─ merged_baselines ─ patch
        0 ─ b ─ b2 /

    base_a tip = a2 (adds shared="version a")
    base_b tip = b2 (adds shared="version b")
    shared file in working tree = "merged"
    """
    repo.c("0")

    # base_a: initial commit, no conflict yet
    repo.s("git checkout -qb base_a master")
    repo.c("a")

    # base_b: initial commit, no conflict yet
    repo.s("git checkout -qb base_b master")
    repo.c("b")

    # Init queue on both baselines — they merge cleanly at this point
    repo.s("git checkout -q master")
    repo.s("git queue init -b q base_a base_b")
    repo.c("patch")

    # Now update each baseline to add a conflicting "shared" file
    repo.s("git checkout -q base_a")
    repo.w("shared", "version a")
    repo.s("git add shared && git commit -qm a2")

    repo.s("git checkout -q base_b")
    repo.w("shared", "version b")
    repo.s("git add shared && git commit -qm b2")

    # Rebase suspends on the shared-file conflict
    repo.s("git checkout -q q")
    repo.s("git queue rebase; [[ $? = 2 ]]")
    assert repo.unmerged() == {"shared"}

    # User resolves the conflict
    repo.w("shared", "merged")
    repo.s("git add shared")
    repo.s("git queue continue")

    assert "resolved conflicts" in repo.log()


def test_port_user_merge_rebased_baseline(repo: Git):
    """
    When a baseline is rebased onto a new upstream commit (same patch content,
    new parent SHA), port_user_merge should carry the conflict resolution
    forward automatically without user interaction.
    """
    _setup_conflict(repo)

    # Advance master and rebase base_a onto it (a2's diff is identical — only parent changes)
    repo.s("git checkout -q master")
    repo.c("1")
    repo.s("git checkout -q base_a")
    repo.s("git rebase master -q")

    # Rebase should succeed automatically — port_user_merge handles M'
    repo.s("git checkout -q q")
    repo.s("git queue rebase")

    log = repo.log()
    assert "resolved conflicts" in log
    assert "merged baselines" in log
    assert "patch" in log

    # Conflict resolution is preserved
    with open(repo.directory / "shared") as f:
        assert f.read().strip() == "merged"

    # The new upstream commit is present in the log
    assert "1" in log


def test_port_user_merge_both_baselines_rebased(repo: Git):
    """
    When both conflicting baselines are rebased, the user merge is still
    ported automatically.
    """
    _setup_conflict(repo)

    repo.s("git checkout -q master")
    repo.c("1")
    repo.s("git checkout -q base_a && git rebase master -q")
    repo.s("git checkout -q base_b && git rebase master -q")

    repo.s("git checkout -q q")
    repo.s("git queue rebase")

    log = repo.log()
    assert "resolved conflicts" in log
    assert "merged baselines" in log
    assert "patch" in log

    with open(repo.directory / "shared") as f:
        assert f.read().strip() == "merged"

    assert "1" in log


def test_port_user_merge_new_file_in_rebased_baseline(repo: Git):
    """
    When the rebased baseline includes a new file from the upstream commit,
    the ported merge incorporates that file.
    """
    _setup_conflict(repo)

    # Advance master with a new distinct file, then rebase base_a
    repo.s("git checkout -q master")
    repo.c("extra")
    repo.s("git checkout -q base_a")
    repo.s("git rebase master -q")

    repo.s("git checkout -q q")
    repo.s("git queue rebase")

    log = repo.log()
    assert "resolved conflicts" in log
    assert "patch" in log

    with open(repo.directory / "shared") as f:
        assert f.read().strip() == "merged"

    assert (repo.directory / "extra").exists()


def test_port_user_merge_multiple_rebases(repo: Git):
    """
    Repeated rebases of the baseline should each be ported automatically,
    accumulating upstream changes each time.
    """
    _setup_conflict(repo)

    for i in range(1, 4):
        repo.s("git checkout -q master")
        repo.c(str(i))
        repo.s("git checkout -q base_a && git rebase master -q")

        repo.s("git checkout -q q")
        repo.s("git queue rebase")

        log = repo.log()
        assert "resolved conflicts" in log, f"iteration {i}: {log}"
        assert "patch" in log
        assert str(i) in log

        with open(repo.directory / "shared") as f:
            assert f.read().strip() == "merged"


def test_port_user_merge_patch_preserved(repo: Git):
    """
    The queue patch on top of the ported merge should be the last commit.
    """
    _setup_conflict(repo)

    repo.s("git checkout -q master")
    repo.c("1")
    repo.s("git checkout -q base_a && git rebase master -q")

    repo.s("git checkout -q q")
    repo.s("git queue rebase")

    assert repo.log()[-1] == "patch"

    with open(repo.directory / "shared") as f:
        assert f.read().strip() == "merged"


def test_port_user_merge_insufficient(repo: Git):
    """
    Port_user_merges succeeds, but the resulting commit is not sufficient
    to merge the new baselines because new conflicts have been introduced.

    """
    _setup_conflict(repo)

    # Rebase base_a (makes old a2 SHA unreachable), then add a new commit that
    # modifies "shared" in a way incompatible with M's resolved content.
    repo.s("git checkout -q master")
    repo.c("1")
    repo.s("git checkout -q base_a && git rebase master -q")
    repo.w("shared", "version a revised")
    repo.s("git add shared && git commit -qm 'base_a: revise shared'")

    repo.s("git checkout -q q")
    # Porting fails → user merge is dropped → rebase suspends for manual resolution
    repo.s("git queue rebase; [[ $? = 2 ]]")
    assert repo.unmerged() == {"shared"}


def test_port_user_merge_fails_gracefully(repo: Git):
    """
    When base_a is force-reset to a completely new history whose conflicting
    patch has a different patch-id, port_user_merge cannot match the old
    essential patch in the new base. It falls back to merge-base, putting Pa2
    in revert_by_pid, then fails when the reverse patch ("remove version a")
    is applied to M's tree which has "merged" instead.

    check_user_merges catches the GitFailed, drops the user merge, and the
    rebase suspends for manual conflict resolution.
    """
    _setup_conflict(repo)

    # Force-reset base_a to a completely new history: old a/a2 SHAs are gone.
    # The new commit adds shared="version a v2" — a different patch-id from the
    # original "version a", so port_user_merge can never find Pa2 in the new base.
    repo.s("git checkout -q master")
    repo.c("1")
    repo.s("git checkout -qB base_a master")
    repo.w("shared", "version a v2")
    repo.s("git add shared && git commit -qm 'a2_new'")

    repo.s("git checkout -q q")
    # port_user_merge raises GitFailed (reverse patch of Pa2 won't apply to "merged")
    # → user merge is dropped → rebase suspends with a fresh conflict
    repo.s("git queue rebase; [[ $? = 2 ]]")
    assert repo.unmerged() == {"shared"}


def test_port_user_merge_merge_in_baseline_ancestry(repo: Git):
    """
    Tolerate non-conflicted merges in old baseline
    """
    repo.c("0")

    # base_a is constructed by merging two sub-branches (creating a merge commit
    # in its history), then adding the conflicting file on top as a regular commit.
    repo.s("git checkout -qb sub1 master")
    repo.c("sub1_file")
    repo.s("git checkout -qb sub2 master")
    repo.c("sub2_file")
    repo.s("git checkout -qb base_a sub1")
    repo.s("git merge --no-ff -q sub2 -m 'base_a: merge subs'")
    # base_a tip is now a merge commit

    # base_b: a plain branch from master
    repo.s("git checkout -qb base_b master")
    repo.c("base_b_file")

    # Init queue (no shared file yet — clean merge)
    repo.s("git checkout -q master")
    repo.s("git queue init -b q base_a base_b")
    repo.c("patch")

    # Both baselines now add a conflicting "shared" file
    repo.s("git checkout -q base_a")
    repo.w("shared", "version a")
    repo.s("git add shared && git commit -qm 'base_a: add shared'")

    repo.s("git checkout -q base_b")
    repo.w("shared", "version b")
    repo.s("git add shared && git commit -qm 'base_b: add shared'")

    # Rebase suspends on conflict
    repo.s("git checkout -q q")
    repo.s("git queue rebase; [[ $? = 2 ]]")
    assert repo.unmerged() == {"shared"}

    # User resolves
    repo.w("shared", "merged")
    repo.s("git add shared")
    repo.s("git queue continue")

    assert "resolved conflicts" in repo.log()

    # remove the merge from base_a, leaving just the conflicting commit
    repo.s("git checkout -q base_a")
    repo.s("git rebase HEAD^ --onto HEAD^^^")

    repo.s("git checkout -q q")
    repo.s("git queue rebase")

    with open(repo.directory / "shared") as f:
        assert f.read().strip() == "merged"


def test_port_user_merge_stacked_queues(repo: Git):
    """
    Stacked queue scenario: queue_c is based on queue_d (itself a queue with
    patches built on master) and a sidecar branch. When master advances and
    queue_d is rebased onto it, port_user_merge automatically carries the
    conflict-resolution merge in queue_c forward — no manual intervention needed.

    This is the primary motivation for the feature: rebasing a lower queue in
    a stack should not force the user to re-resolve conflicts in every queue
    above it.

    History before the final rebase:
        master: 0
        queue_d: 0 ─ d_patch ─ queue_d:shared
        sidecar: 0 ─ sidecar_patch ─ sidecar:shared
        queue_c: (merge of queue_d + sidecar, resolved) ─ c_patch

    After master advances to "1" and queue_d is rebased:
        queue_d: 0 ─ 1 ─ d_patch ─ queue_d:shared
        queue_c rebase should succeed automatically.
    """
    repo.c("0")

    # queue_d: a lower queue with its own patch on top of master
    repo.s("git checkout -qb queue_d master")
    repo.c("d_patch")

    # sidecar: another branch from master, no conflict yet
    repo.s("git checkout -qb sidecar master")
    repo.c("sidecar_patch")

    # Initialize queue_c based on both — clean merge at this point
    repo.s("git checkout -q master")
    repo.s("git queue init -b queue_c queue_d sidecar")
    repo.c("c_patch")

    # Both baselines now add a conflicting "shared" file
    repo.s("git checkout -q queue_d")
    repo.w("shared", "version d")
    repo.s("git add shared && git commit -qm 'queue_d: add shared'")

    repo.s("git checkout -q sidecar")
    repo.w("shared", "version sidecar")
    repo.s("git add shared && git commit -qm 'sidecar: add shared'")

    # Rebase queue_c — suspends on the shared-file conflict
    repo.s("git checkout -q queue_c")
    repo.s("git queue rebase; [[ $? = 2 ]]")
    assert repo.unmerged() == {"shared"}

    # User resolves the conflict
    repo.w("shared", "merged")
    repo.s("git add shared")
    repo.s("git queue continue")

    assert "resolved conflicts" in repo.log()

    # Advance master and rebase queue_d onto it (simulating `git queue rebase` on queue_d)
    repo.s("git checkout -q master")
    repo.c("1")
    repo.s("git checkout -q queue_d && git rebase master -q")

    # Rebase queue_c — port_user_merge should carry the resolution forward
    repo.s("git checkout -q queue_c")
    repo.s("git queue rebase")

    log = repo.log()
    assert "resolved conflicts" in log
    assert "c_patch" in log
    assert "d_patch" in log
    assert "1" in log

    with open(repo.directory / "shared") as f:
        assert f.read().strip() == "merged"

    assert repo.log()[-1] == "c_patch"


def test_port_user_merge_2_essential(repo: Git):
    "Test a case where there is more than one essential patch."
    repo.c("0")

    # base_a: non-conflicting seed commit so the initial queue merge is clean
    repo.s("git checkout -qb base_a master")
    repo.c("a_init")

    repo.s("git checkout -qb base_b master")
    repo.c("b_init")

    repo.s("git checkout -q master")
    repo.s("git queue init -b q base_a base_b")
    repo.c("patch")

    # Both baselines now grow conflicting files
    repo.s("git checkout -q base_a")
    repo.w("file1", "version_a")
    repo.s("git add file1 && git commit -qm 'a_conflict1'")
    repo.w("file2", "another_version_a")
    repo.s("git add file2 && git commit -qm 'a_conflict2'")

    repo.s("git checkout -q base_b")
    repo.w("file1", "version_b")
    repo.w("file2", "another_version_b")
    repo.s("git add file1 file2 && git commit -qm 'b_conflicts'")

    repo.s("git checkout -q q")
    repo.s("git queue rebase; [[ $? = 2 ]]")
    assert repo.unmerged() == {"file1", "file2"}

    repo.w("file1", "merged1")
    repo.w("file2", "merged2")
    repo.s("git add file1 file2")
    repo.s("git queue continue")

    assert "resolved conflicts" in repo.log()

    # re-order patches in base_a
    repo.s("git checkout -q base_a")
    repo.s("git swap HEAD")

    # rebase the queue
    repo.s("git checkout -q q")
    repo.s("git queue rebase")

    log = repo.log()
    assert "resolved conflicts" in log
    assert "patch" in log

    with open(repo.directory / "file1") as f:
        assert f.read().strip() == "merged1"
    with open(repo.directory / "file2") as f:
        assert f.read().strip() == "merged2"


def test_port_user_merge_no_common_ancestor(repo: Git):
    _setup_conflict(repo)

    # Replace base_a with an orphan commit — no shared ancestry with anything
    # that descends from the initial root ("0").
    repo.s("git checkout --orphan orphan_base")
    repo.s("git rm -qrf .")
    repo.w("shared", "version a")
    repo.s("git add shared && git commit -qm 'orphan_a'")
    repo.s("git branch -f base_a HEAD")

    repo.s("git checkout -q q")
    repo.s("git queue rebase")

    with open(repo.directory / "shared") as f:
        assert f.read().strip() == "merged"
