

def test_save_state_writes_only_changed_fields_and_keeps_concurrent_updates():
    import db
    from state import ConversationState
    db.save_state(ConversationState(session_id="race1"))
    mine = db.load_state("race1")                       # תהליך א' (הבוט) טוען
    other = db.load_state("race1")                      # תהליך ב' (בדיקת הנציג) טוען במקביל
    other.pending_questions["new_participation"] = 7
    other.waiting_for_representative = True
    db.save_state(other)
    mine.employee.base_salary = 14200.0                 # התהליך הראשון משנה שדה אחר ושומר אחרון
    target = mine.target
    db.save_state(mine)
    saved = db.load_state("race1")
    assert saved.employee.base_salary == 14200.0                       # השינוי שלי נשמר
    assert saved.pending_questions == {"new_participation": 7} and saved.waiting_for_representative   # ושל האחר לא נדרס
    assert mine.pending_questions == {"new_participation": 7} and mine.target is target   # האובייקט עודכן במקום


def test_save_state_removal_of_a_pending_question_is_kept():
    import db
    from state import ConversationState
    first = ConversationState(session_id="race2")
    first.pending_questions["x"] = 1
    db.save_state(first)
    state = db.load_state("race2")
    state.pending_questions.pop("x")
    db.save_state(state)
    assert db.load_state("race2").pending_questions == {}
