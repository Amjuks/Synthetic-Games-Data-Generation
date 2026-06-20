from src.generator.jobs import JobManager


def test_job_manager_creates_progress_file():
    job_manager = JobManager(job_name="test-job")
    job_manager.save_status(status="running")
    assert job_manager.progress_path.exists()
    assert job_manager.get_status()["job_name"] == "test-job"
