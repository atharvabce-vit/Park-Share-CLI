import data_store
from utils_module import line


def show_activity(limit=15):
    line()
    print("Live Activity Feed")
    line()
    if not data_store.activity_log:
        print("No activity recorded yet.")
        return
    recent = data_store.activity_log[-limit:]
    for timestamp, text in recent:
        print(timestamp.strftime("%H:%M:%S"), "|", text)
    line()
    print("Showing last", len(recent), "of", len(data_store.activity_log), "events")
