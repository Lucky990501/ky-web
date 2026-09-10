from scripts.run_rag_v1_3_eval import apply_section_aliases, evaluate_case, metrics_for


def test_positive_case_requires_the_expected_section_not_just_any_result():
    case = {"id": "p1", "query": "活动如何安排？", "expected_answerable": True, "expected_section": "活动场次"}
    wrong = evaluate_case(case, [{"chunk_id": "chunk-1", "file_id": "file-1", "section": "嘉宾档案", "score": 0.8}], 12.5)
    right = evaluate_case(case, [{"chunk_id": "chunk-2", "file_id": "file-1", "section": "活动场次", "score": 0.7}], 10.0)

    assert wrong["accepted"] is True
    assert "query" not in wrong
    assert wrong["section_match_at_k"] is False
    assert wrong["passed"] is False
    assert right["section_match_at_k"] is True
    assert right["top_1_section_match"] is True
    assert right["passed"] is True


def test_negative_case_passes_only_when_no_result_is_returned():
    case = {"id": "n1", "query": "不存在的问题", "expected_answerable": False, "expected_section": None}
    assert evaluate_case(case, [], 8.0)["passed"] is True
    assert evaluate_case(case, [{"chunk_id": "chunk-1", "section": "正文", "score": 0.2}], 8.0)["passed"] is False


def test_metrics_distinguish_acceptance_from_grounded_section_matches():
    positive = evaluate_case({"id": "p1", "query": "q", "expected_answerable": True, "expected_section": "目标"}, [{"section": "错误"}], 1.0)
    negative = evaluate_case({"id": "n1", "query": "q", "expected_answerable": False, "expected_section": None}, [], 1.0)
    metrics = metrics_for([positive, negative])

    assert metrics["answerable_acceptance_recall"] == 1.0
    assert metrics["section_recall_at_k"] == 0.0
    assert metrics["no_answer_rejection_rate"] == 1.0


def test_reviewer_approved_section_aliases_allow_concrete_index_sections():
    cases = [{"id": "p1", "query": "活动如何安排？", "expected_answerable": True, "expected_section": "活动场次"}]
    resolved = apply_section_aliases(cases, {"活动场次": ["2023-2024秋季｜活动时间"]})
    result = evaluate_case(resolved[0], [{"chunk_id": "chunk-1", "section": "2023-2024秋季｜活动时间", "score": 0.8}], 2.0)

    assert result["accepted_sections"] == ["活动场次", "2023-2024秋季｜活动时间"]
    assert result["section_match_at_k"] is True
    assert result["passed"] is True
