"""Only isolated counter/compatibility tests; never a real Provider PASS."""
from decimal import Decimal
import pytest
from app.preview_budget import PreviewBudget, PreviewBudgetExceeded, plaintext_history


def test_plain_tool_cycle_preserves_real_call_and_result():
    call={"type":"function_call","call_id":"real-call","name":"knowledge_search","arguments":"{}"}
    result={"type":"function_call_output","call_id":"real-call","output":"真实工具返回"}
    normalized,dropped=plaintext_history({"input":[{"role":"user","content":"问题"},{"type":"reasoning","encrypted_content":"Fixture-only-opaque"},call,result],"include":["reasoning.encrypted_content"]})
    assert dropped==1 and normalized["input"][-2:]==[call,result]
    assert "encrypted_content" not in str(normalized)


@pytest.mark.parametrize("item",[{"type":"item_reference","id":"unknown"},{"type":"computer_call"},{"type":"message","encrypted_content":"Fixture"}])
def test_unknown_or_opaque_inputs_fail_closed(item):
    with pytest.raises(ValueError): plaintext_history({"input":[item]})


def test_budget_count_persists_across_restart(tmp_path):
    meter=PreviewBudget(tmp_path)
    for _ in range(24): meter.reserve("text",Decimal('0.1'))
    with pytest.raises(PreviewBudgetExceeded): PreviewBudget(tmp_path).reserve("text",Decimal('0.1'))
    assert PreviewBudget(tmp_path).summary()['text']['requests']==24


def test_money_limit_reserves_unknown_outcome_and_never_refunds(tmp_path):
    meter=PreviewBudget(tmp_path);meter.reserve('image',Decimal('20'))
    with pytest.raises(PreviewBudgetExceeded): meter.reserve('image',Decimal('1'))
    assert meter.summary()['image']['reserved_ceiling_cny']=='20'


@pytest.mark.parametrize('cost',['0','-1','NaN','Infinity'])
def test_unbounded_or_free_assumptions_are_not_permission(tmp_path,cost):
    with pytest.raises(PreviewBudgetExceeded): PreviewBudget(tmp_path).reserve('text',Decimal(cost))
