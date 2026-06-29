import json

from src.generator.jobs import JobManager


def test_job_manager_creates_progress_file():
    job_manager = JobManager(job_name="test-job")
    job_manager.save_status(status="running")
    assert job_manager.progress_path.exists()
    assert job_manager.get_status()["job_name"] == "test-job"


def test_job_manager_resume_state_uses_saved_progress():
    job_manager = JobManager(job_name="test-job")
    job_manager.progress_path.write_text(
        json.dumps(
            {
                "job_name": "test-job",
                "status": "failed",
                "completed": 3,
                "total": 10,
                "single_turn_count": 3,
                "multi_turn_count": 3,
                "next_sample_index": 3,
            }
        ),
        encoding="utf-8",
    )

    resume_state = job_manager.get_resume_state()

    assert resume_state["completed"] == 3
    assert resume_state["next_sample_index"] == 3
    assert resume_state["single_turn_count"] == 3
    assert resume_state["multi_turn_count"] == 3
