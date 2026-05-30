"""Quick smoke test for DB writes — run with: python test_db_save.py"""
import os, sys

os.environ.setdefault("DB_PATH", "calls.db")

from memory import init_db, create_user, complete_onboarding, save_session, update_session_transcript, get_user_by_phone, get_session

init_db()

phone = "+15550000001"

# Clean up any leftover test data
import sqlite3
conn = sqlite3.connect(os.environ["DB_PATH"])
conn.execute("DELETE FROM sessions WHERE phone=?", (phone,))
conn.execute("DELETE FROM users WHERE phone=?", (phone,))
conn.commit()
conn.close()

# 1. Create user
user_id = create_user(phone=phone, name="Test User", role="Engineer", time_horizon=5, channel="career")
print(f"create_user → user_id={user_id}")
assert user_id, "create_user returned falsy"

# 2. Complete onboarding
complete_onboarding(user_id, "Test profile summary.")
user = get_user_by_phone(phone)
assert user["onboarding_done"] == 1, f"onboarding_done={user['onboarding_done']}"
print(f"complete_onboarding → onboarding_done={user['onboarding_done']}")

# 3. Save session
session_id = save_session(
    phone=phone, channel="career", archetype="test",
    answers={"q1": "hello"}, action_plan={"day_30": "do something"},
    user_id=user_id,
)
print(f"save_session → session_id={session_id}")
assert session_id, "save_session returned falsy"

# 4. Update transcript
update_session_transcript(session_id, "[Recall]\nHello.\n\n[You]\nHi.")
session = get_session(session_id)
assert session["transcript"].startswith("[Recall]"), "transcript not saved"
assert session["status"] == "completed", f"status={session['status']}"
print(f"update_session_transcript → status={session['status']} transcript_len={len(session['transcript'])}")

print("\nAll DB tests passed.")
