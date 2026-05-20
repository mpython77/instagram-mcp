import asyncio
import json
import os
import signal
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from instagram_mcp.batch_runner import BatchConfig, BatchRunner, BatchStats, _parse_date
from instagram_mcp.models import InstagramProfile, FeedTagResult

def test_parse_date():
    assert _parse_date("") is None
    assert _parse_date(None) is None
    
    with patch("instagram_mcp.batch_runner.datetime") as mock_dt:
        mock_instance = MagicMock()
        mock_instance.replace.return_value.timestamp.return_value = 123456789.0
        mock_dt.strptime.return_value = mock_instance
        assert _parse_date("01.01.2023") == 123456789
        
    assert _parse_date("invalid") is None

def test_batch_config():
    cfg = BatchConfig(targets_file="targets.txt", output_file="out.json")
    assert cfg.progress_file == "out.progress.json"
    
    with patch("instagram_mcp.batch_runner._parse_date", side_effect=lambda x: 100 if x == "1.1.1" else 200):
        cfg = BatchConfig(
            targets_file="targets.txt", 
            output_file="out.json",
            since_date="1.1.1",
            until_date="2.2.2"
        )
        assert cfg.since_timestamp == 100
        assert cfg.until_timestamp == 200

def test_batch_stats():
    stats = BatchStats(completed=10, elapsed_seconds=2.0)
    assert stats.rate == 5.0
    
    stats2 = BatchStats(completed=10, elapsed_seconds=0.0)
    assert stats2.rate == 0.0

@pytest.fixture
def temp_targets():
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write("user1\n@user2\ntarget\n\nuser3\n")
        path = f.name
    yield path
    os.remove(path)

@pytest.fixture
def temp_progress():
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        json.dump({"completed": ["user1"]}, f)
        path = f.name
    yield path
    os.remove(path)

@pytest.fixture
def temp_existing_results():
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        json.dump({"profiles": {"user1": {"username": "user1", "status": "active"}}}, f)
        path = f.name
    yield path
    os.remove(path)

@pytest.mark.asyncio
async def test_batch_runner_load_targets(temp_targets):
    cfg = BatchConfig(targets_file=temp_targets, output_file="out.json")
    runner = BatchRunner(cfg, AsyncMock())
    assert runner._load_targets() == ["user1", "user2", "user3"]

    cfg_miss = BatchConfig(targets_file="missing_asdf_123.txt", output_file="out.json")
    runner_miss = BatchRunner(cfg_miss, AsyncMock())
    assert runner_miss._load_targets() == []

@pytest.mark.asyncio
async def test_batch_runner_load_progress(temp_progress):
    cfg = BatchConfig(targets_file="targets.txt", output_file="out.json", progress_file=temp_progress)
    runner = BatchRunner(cfg, AsyncMock())
    assert runner._load_progress() == {"user1"}

    cfg_miss = BatchConfig(targets_file="targets.txt", output_file="out.json", progress_file="missing.json")
    runner_miss = BatchRunner(cfg_miss, AsyncMock())
    assert runner_miss._load_progress() == set()

    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write("invalid json")
        p = f.name
    cfg_inv = BatchConfig(targets_file="targets.txt", output_file="out.json", progress_file=p)
    runner_inv = BatchRunner(cfg_inv, AsyncMock())
    assert runner_inv._load_progress() == set()
    os.remove(p)

@pytest.mark.asyncio
async def test_batch_runner_load_existing_results(temp_existing_results):
    cfg = BatchConfig(targets_file="t.txt", output_file=temp_existing_results)
    runner = BatchRunner(cfg, AsyncMock())
    assert runner._load_existing_results() == {"user1": {"username": "user1", "status": "active"}}

    cfg_miss = BatchConfig(targets_file="t.txt", output_file="miss.json")
    runner_miss = BatchRunner(cfg_miss, AsyncMock())
    assert runner_miss._load_existing_results() == {}

    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write("inv")
        p = f.name
    cfg_inv = BatchConfig(targets_file="t.txt", output_file=p)
    runner_inv = BatchRunner(cfg_inv, AsyncMock())
    assert runner_inv._load_existing_results() == {}
    os.remove(p)

@pytest.mark.asyncio
async def test_save_progress(tmp_path):
    out_file = str(tmp_path / "out.json")
    prog_file = str(tmp_path / "prog.json")
    
    cfg = BatchConfig(targets_file="t.txt", output_file=out_file, progress_file=prog_file, since_date="01.01.2023", use_cookies=True)
    runner = BatchRunner(cfg, AsyncMock())
    runner._stats = BatchStats(total=10, completed=2, active=1, not_found=1)
    runner._started_at = "2023-01-01 00:00:00"
    runner._results = {"user1": {"username": "user1"}}
    runner._completed = {"user1"}
    
    runner._save_progress()
    
    with open(out_file) as f:
        out_data = json.load(f)
    assert out_data["metadata"]["total_targets"] == 10
    assert out_data["profiles"] == {"user1": {"username": "user1"}}
    
    with open(prog_file) as f:
        prog_data = json.load(f)
    assert prog_data["completed"] == ["user1"]

@pytest.mark.asyncio
async def test_save_progress_errors():
    cfg = BatchConfig(targets_file="t.txt", output_file="/invalid/dir/out.json", progress_file="/invalid/dir/prog.json")
    runner = BatchRunner(cfg, AsyncMock())
    runner._save_progress()

@pytest.mark.asyncio
async def test_scrape_one_retries_and_failure():
    cfg = BatchConfig(targets_file="t.txt", output_file="o.json", max_retries=2, retry_base_delay=0.01)
    client = AsyncMock()
    client.fetch_user.side_effect = Exception("network error")
    
    runner = BatchRunner(cfg, client)
    sem = asyncio.Semaphore(1)
    res = await runner._scrape_one("user1", sem)
    
    assert res["status"] == "error"

@pytest.mark.asyncio
async def test_scrape_one_not_found():
    cfg = BatchConfig(targets_file="t.txt", output_file="o.json")
    client = AsyncMock()
    client.fetch_user.return_value = None
    
    runner = BatchRunner(cfg, client)
    sem = asyncio.Semaphore(1)
    res = await runner._scrape_one("user1", sem)
    assert res["status"] == "not_found"

@pytest.mark.asyncio
@patch("instagram_mcp.batch_runner.parse_profile")
@patch("instagram_mcp.batch_runner.format_profile_json")
@patch("instagram_mcp.batch_runner.format_feed_tags_json")
async def test_scrape_one_success_private(mock_fmt_feed, mock_fmt_prof, mock_parse_prof):
    cfg = BatchConfig(targets_file="t.txt", output_file="o.json")
    client = AsyncMock()
    client.fetch_user.return_value = {"data": "some"}

    runner = BatchRunner(cfg, client)
    prof_mock = MagicMock()
    prof_mock.is_private = True
    mock_parse_prof.return_value = prof_mock

    res = await runner._scrape_one("user1", asyncio.Semaphore(1))
    assert res["status"] == "private"

@pytest.mark.asyncio
@patch("instagram_mcp.batch_runner.parse_profile")
@patch("instagram_mcp.batch_runner.parse_feed_items")
@patch("instagram_mcp.batch_runner.check_dead_account_from_items")
@patch("instagram_mcp.batch_runner.format_profile_json")
@patch("instagram_mcp.batch_runner.format_feed_tags_json")
async def test_scrape_one_success_active(mock_fmt_feed, mock_fmt_prof, mock_check_dead, mock_parse_feed, mock_parse_prof):
    cfg = BatchConfig(targets_file="t.txt", output_file="o.json", max_posts=10)
    client = AsyncMock()
    client.fetch_user.return_value = {"data": "some"}
    client.fetch_feed_items.return_value = []

    runner = BatchRunner(cfg, client)
    prof_mock = MagicMock()
    prof_mock.is_private = False
    mock_parse_prof.return_value = prof_mock
    mock_check_dead.return_value = (False, 1)
    mock_parse_feed.return_value = FeedTagResult()

    res = await runner._scrape_one("user1", asyncio.Semaphore(1))
    assert res["status"] == "active"

@pytest.mark.asyncio
@patch("instagram_mcp.batch_runner.parse_profile")
@patch("instagram_mcp.batch_runner.parse_feed_items")
@patch("instagram_mcp.batch_runner.check_dead_account_from_items")
@patch("instagram_mcp.batch_runner.format_profile_json")
@patch("instagram_mcp.batch_runner.format_feed_tags_json")
async def test_scrape_one_success_dead_paginated(mock_fmt_feed, mock_fmt_prof, mock_check_dead, mock_parse_feed, mock_parse_prof):
    cfg = BatchConfig(targets_file="t.txt", output_file="o.json", max_posts=20, since_date="01.01.2023", until_date="31.12.2023")
    client = AsyncMock()
    client.fetch_user.return_value = {"data": "some"}
    client.fetch_feed_items.return_value = [{"taken_at": 1672531200}]

    runner = BatchRunner(cfg, client)
    prof_mock = MagicMock()
    prof_mock.is_private = False
    mock_parse_prof.return_value = prof_mock
    mock_check_dead.return_value = (True, 100)
    mock_parse_feed.return_value = FeedTagResult()

    res = await runner._scrape_one("user1", asyncio.Semaphore(1))
    assert res["status"] == "dead"

@pytest.mark.asyncio
@patch("instagram_mcp.batch_runner.parse_profile")
async def test_scrape_one_parse_error(mock_parse_prof):
    cfg = BatchConfig(targets_file="t.txt", output_file="o.json")
    client = AsyncMock()
    client.fetch_user.return_value = {"data": "some"}
    
    runner = BatchRunner(cfg, client)
    mock_parse_prof.side_effect = Exception("parse issue")
    
    res = await runner._scrape_one("user1", asyncio.Semaphore(1))
    assert res["status"] == "error"

@pytest.mark.asyncio
async def test_scrape_one_stop_flag():
    cfg = BatchConfig(targets_file="t.txt", output_file="o.json")
    runner = BatchRunner(cfg, AsyncMock())
    runner._stop_flag = True
    
    res = await runner._scrape_one("user1", asyncio.Semaphore(1))
    assert res["status"] == "error"

@pytest.mark.asyncio
async def test_handle_shutdown():
    cfg = BatchConfig(targets_file="t.txt", output_file="o.json")
    runner = BatchRunner(cfg, AsyncMock())
    runner._save_progress = MagicMock()
    
    runner._handle_shutdown()
    assert runner._stop_flag is True
    runner._save_progress.assert_called_once()
    
    runner._save_progress.reset_mock()
    runner._handle_shutdown()
    assert runner._save_progress.call_count == 0

@pytest.mark.asyncio
@patch("instagram_mcp.batch_runner.BatchRunner._load_targets")
@patch("instagram_mcp.batch_runner.BatchRunner._load_progress")
@patch("instagram_mcp.batch_runner.BatchRunner._load_existing_results")
@patch("instagram_mcp.batch_runner.BatchRunner._save_progress")
async def test_run_main(mock_save, mock_load_res, mock_load_prog, mock_load_targets):
    cfg = BatchConfig(targets_file="t.txt", output_file="o.json", save_every=2)
    client = AsyncMock()
    runner = BatchRunner(cfg, client)

    mock_load_targets.return_value = ["user1", "user2", "user3", "user4", "user5", "user6"]
    mock_load_prog.return_value = {"user1"}
    mock_load_res.return_value = {"user1": {}}

    async def mock_scrape(u):
        if u == "user2":
            return {"username": u, "status": "active"}
        if u == "user3":
            return {"username": u, "status": "not_found"}
        if u == "user4":
            return {"username": u, "status": "error"}
        if u == "user5":
            return {"username": u, "status": "private"}
        if u == "user6":
            return {"username": u, "status": "dead"}
        return {}

    runner._scrape_one_no_semaphore = mock_scrape

    async def noop_open(force=False): pass
    async def noop_close(): pass
    async def noop_write(r): pass
    runner._open_jsonl_stream = noop_open
    runner._close_jsonl_stream = noop_close
    runner._jsonl_write = noop_write

    stats = await runner.run()
    assert stats.completed == 6

@pytest.mark.asyncio
@patch("instagram_mcp.batch_runner.BatchRunner._load_targets")
async def test_run_stop_flag_during_execution(mock_load_targets):
    cfg = BatchConfig(targets_file="t.txt", output_file="o.json")
    client = AsyncMock()
    runner = BatchRunner(cfg, client)
    
    mock_load_targets.return_value = ["u1", "u2", "u3"]
    runner._load_progress = MagicMock(return_value=set())
    runner._load_existing_results = MagicMock(return_value={})
    runner._save_progress = MagicMock()
    
    async def slow_scrape(u):
        if u == "u1":
            runner._stop_flag = True
            return {"username": u, "status": "active"}
        await asyncio.sleep(0.5)
        return {"username": u, "status": "active"}

    runner._scrape_one_no_semaphore = AsyncMock(side_effect=slow_scrape)

    await runner.run()
    assert runner._stats.completed <= 1

@pytest.mark.asyncio
@patch("instagram_mcp.batch_runner.BatchRunner._load_targets")
async def test_run_exceptions(mock_load_targets):
    cfg = BatchConfig(targets_file="t.txt", output_file="o.json")
    client = AsyncMock()
    runner = BatchRunner(cfg, client)
    
    mock_load_targets.return_value = ["u1"]
    runner._load_progress = MagicMock(return_value=set())
    runner._load_existing_results = MagicMock(return_value={})
    runner._save_progress = MagicMock()
    
    # 1. CancelledError — re-raised by worker, never put in output_q → error stays 0
    runner._scrape_one_no_semaphore = AsyncMock(side_effect=asyncio.CancelledError())
    await runner.run()
    assert runner._stats.error == 0

    # 2. Generic Exception — worker catches it, creates error result → error == 1
    runner._scrape_one_no_semaphore = AsyncMock(side_effect=Exception("generic error"))
    await runner.run()
    assert runner._stats.error == 1

@pytest.mark.asyncio
async def test_run_loop_exceptions():
    cfg = BatchConfig(targets_file="t.txt", output_file="o.json")
    client = AsyncMock()
    runner = BatchRunner(cfg, client)

    runner._load_targets = MagicMock(return_value=["u1", "u2"])
    runner._load_progress = MagicMock(return_value=set())
    runner._load_existing_results = MagicMock(return_value={})
    runner._save_progress = MagicMock()

    call_count = 0

    async def mock_scrape(username):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise asyncio.CancelledError()
        raise Exception("generic error")

    runner._scrape_one_no_semaphore = mock_scrape
    await runner.run()

@pytest.mark.asyncio
async def test_run_signal_setup():
    cfg = BatchConfig(targets_file="t.txt", output_file="o.json")
    runner = BatchRunner(cfg, AsyncMock())
    
    with patch("asyncio.get_running_loop") as mock_loop, \
         patch("signal.getsignal") as mock_getsignal:
        mock_loop_instance = MagicMock()
        mock_loop_instance.add_signal_handler.side_effect = NotImplementedError()
        mock_loop.return_value = mock_loop_instance
        mock_getsignal.return_value = signal.SIG_DFL
        
        runner._load_targets = MagicMock(return_value=[])
        runner._load_progress = MagicMock(return_value=set())
        runner._load_existing_results = MagicMock(return_value={})
        await runner.run()
