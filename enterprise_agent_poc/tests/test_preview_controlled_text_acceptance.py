"""FIXTURE_ONLY: one scoped article, max4 text requests, no Provider calls."""
import json
from decimal import Decimal
import sqlite3
import pytest
from app.preview_budget import PreviewBudget,PreviewBudgetExceeded

def control(task='fixture-task'):
    return {'authorization_id':'FIXTURE_ONLY_ONE_ARTICLE','task_id':task,'max_requests':4}

def test_four_reservations_atomic_fifth_refused_without_reset(tmp_path):
    budget=PreviewBudget(tmp_path)
    for _ in range(4):budget.reserve('text',Decimal('.1'),controlled=control())
    with pytest.raises(PreviewBudgetExceeded):budget.reserve('text',Decimal('.1'),controlled=control())
    assert budget.summary()['text']['requests']==4
    with sqlite3.connect(budget.path) as c:
        markers=[json.loads(row[0]) for row in c.execute('select payload from observations')]
    assert len(markers)==4 and all(m['task_id']=='fixture-task' for m in markers)

def test_same_control_cannot_pay_for_second_task_and_other_users_unchanged(tmp_path):
    budget=PreviewBudget(tmp_path);budget.reserve('text',Decimal('.1'),controlled=control())
    with pytest.raises(PreviewBudgetExceeded):budget.reserve('text',Decimal('.1'),controlled=control('another-task'))
    budget.reserve('text',Decimal('.1'));assert budget.summary()['text']['requests']==2

@pytest.mark.parametrize('cap',[0,5,True,None])
def test_invalid_control_never_reserves(tmp_path,cap):
    budget=PreviewBudget(tmp_path)
    with pytest.raises(PreviewBudgetExceeded):budget.reserve('text',Decimal('.1'),controlled={**control(),'max_requests':cap})
    assert budget.summary()['text']['requests']==0
