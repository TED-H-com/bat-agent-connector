from bat_agent_connector.task_core import (
    classify_read,
    final_agent_text,
    initial_prompt,
    review_verdict,
    reviewer_prompt,
)


def test_initial_prompt():
    task = {"original_words": "foo bar"}
    prompt = initial_prompt(task)
    assert "foo bar" in prompt
    assert "BAT-STATUS:" in prompt


def test_reviewer_prompt():
    task = {"original_words": "foo bar", "acceptance": "accept this"}
    prompt = reviewer_prompt(task, "c1", "t1")
    assert "foo bar" in prompt
    assert "accept this" in prompt
    assert "c1" in prompt
    assert "t1" in prompt
    assert "REVIEW-CANDIDATE" in prompt


def test_final_agent_text():
    read1 = {"messages": [{"role": "user", "text": "u"}, {"role": "agent", "text": "a"}]}
    assert final_agent_text(read1) == "a"
    read2 = {"messages": [{"role": "agent", "text": "a1"}, {"role": "agent", "text": "a2"}]}
    assert final_agent_text(read2) == "a2"
    read3 = {"messages": []}
    assert final_agent_text(read3) is None
    read4 = {"messages": [{"role": "tool", "text": "t"}, {"role": "agent", "text": "a"}]}
    assert final_agent_text(read4) == "a"


def test_review_verdict_missing():
    read = {"messages": [{"role": "agent", "text": "just text"}]}
    res = review_verdict(read, "c1", "t1")
    assert res["verdict"] == "reject"
    assert res["reason"] == "verdict_missing"


def test_review_verdict_clipped():
    read = {"messages": [{"role": "agent", "text": '{"verdict": "pass"} …[+10 chars]'}]}
    res = review_verdict(read, "c1", "t1")
    assert res["verdict"] == "reject"
    assert res["reason"] == "final_message_truncated"


def test_review_verdict_pass():
    read = {
        "messages": [
            {
                "role": "agent",
                "text": '{"verdict": "pass", "candidate_commit": "c1", "tree_hash": "t1", "findings": []}',
            }
        ]
    }
    res = review_verdict(read, "c1", "t1")
    assert res["verdict"] == "pass"


def test_review_verdict_invalid():
    read = {
        "messages": [
            {
                "role": "agent",
                "text": '{"verdict": "unknown", "candidate_commit": "c1", "tree_hash": "t1", "findings": []}',
            }
        ]
    }
    res = review_verdict(read, "c1", "t1")
    assert res["verdict"] == "reject"
    assert res["reason"] == "verdict_invalid"


def test_review_verdict_candidate_mismatch():
    read = {
        "messages": [
            {
                "role": "agent",
                "text": '{"verdict": "pass", "candidate_commit": "c2", "tree_hash": "t2", "findings": []}',
            }
        ]
    }
    res = review_verdict(read, "c1", "t1")
    assert res["verdict"] == "reject"
    assert res["reason"] == "verdict_candidate_mismatch"


def test_review_verdict_conflicting():
    read = {
        "messages": [
            {
                "role": "agent",
                "text": '{"verdict": "pass", "candidate_commit": "c1", "tree_hash": "t1", "findings": []} {"verdict": "reject", "candidate_commit": "c1", "tree_hash": "t1", "findings": []}',
            }
        ]
    }
    res = review_verdict(read, "c1", "t1")
    assert res["verdict"] == "reject"
    assert res["reason"] == "verdict_conflicting"


def test_review_verdict_reviewer_reject():
    read = {
        "messages": [
            {
                "role": "agent",
                "text": '{"verdict": "reject", "candidate_commit": "c1", "tree_hash": "t1", "findings": []}',
            }
        ]
    }
    res = review_verdict(read, "c1", "t1")
    assert res["verdict"] == "reject"
    assert res["reason"] == "reviewer_reject"


def test_review_verdict_high_findings():
    read = {
        "messages": [
            {
                "role": "agent",
                "text": '{"verdict": "pass", "candidate_commit": "c1", "tree_hash": "t1", "findings": [{"severity": "high", "summary": "issue"}]}',
            }
        ]
    }
    res = review_verdict(read, "c1", "t1")
    assert res["verdict"] == "reject"
    assert res["reason"] == "pass_with_high_findings"


def test_classify_read():
    assert classify_read({"pending": {"kind": "permission"}}) == "waiting_permission"
    assert classify_read({"pending": {"kind": "unknown"}}) == "needs_ted"
    assert classify_read({"messages": [{"role": "agent", "text": "quota exhausted"}]}) == "quota_limited"
    assert classify_read({"turn_done": False}) == "running"

    assert (
        classify_read({"turn_done": True, "messages": [{"role": "agent", "text": "BAT-STATUS: CONTINUE"}]})
        == "continue"
    )
    assert (
        classify_read({"turn_done": True, "messages": [{"role": "agent", "text": "BAT-STATUS: MILESTONE"}]})
        == "verifying"
    )
    assert (
        classify_read({"turn_done": True, "messages": [{"role": "agent", "text": "BAT-STATUS: NEED-HUMAN"}]})
        == "needs_ted"
    )
