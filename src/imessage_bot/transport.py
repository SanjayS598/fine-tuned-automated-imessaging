import subprocess

# Arguments are data, never interpolated AppleScript. Sends into the existing
# conversation to preserve its iMessage/SMS service and recipient.
SCRIPT = '''on run argv
    tell application "Messages"
        set targetChat to chat id (item 1 of argv)
        send (item 2 of argv) to targetChat
    end tell
end run'''


def send(chat_guid, text):
    subprocess.run(["/usr/bin/osascript", "-e", SCRIPT, chat_guid, text],
                   check=True, capture_output=True, text=True, timeout=20)
