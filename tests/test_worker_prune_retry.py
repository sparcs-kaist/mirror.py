"""prune_finished retries notification and force-prunes after budget."""

from mirror.worker import process


def test_prune_force_prunes_after_attempt_budget(
    finished_job_factory,
    patch_worker_notification,
):
    job = finished_job_factory("pr_test")
    job._notify_attempts = 0

    raised_count = {"n": 0}

    def _raising(*args, **kwargs):
        raised_count["n"] += 1
        raise ConnectionError("no client")

    try:
        with patch_worker_notification(_raising):
            for attempt in range(1, process.NOTIFY_ATTEMPT_BUDGET):
                process.prune_finished()
                assert process.get("pr_test") is not None, (
                    f"job was pruned early after attempt {attempt}"
                )
            # This call pushes attempts to NOTIFY_ATTEMPT_BUDGET -> force-prune
            process.prune_finished()
            assert process.get("pr_test") is None

        assert raised_count["n"] >= process.NOTIFY_ATTEMPT_BUDGET
    finally:
        with process._jobs_lock:
            process._jobs.pop("pr_test", None)


def test_prune_succeeds_when_notification_works(
    finished_job_factory,
    patch_worker_notification,
):
    sent = []

    def _ok(job_id, success, returncode):
        sent.append((job_id, success, returncode))

    finished_job_factory("pr_ok")

    try:
        with patch_worker_notification(_ok):
            process.prune_finished()
        assert ("pr_ok", True, 0) in sent
        assert process.get("pr_ok") is None
    finally:
        with process._jobs_lock:
            process._jobs.pop("pr_ok", None)
