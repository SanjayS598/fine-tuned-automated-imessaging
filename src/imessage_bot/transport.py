import subprocess


class SendError(RuntimeError):
    def __init__(self,message,uncertain=False):
        super().__init__(message)
        self.uncertain=uncertain

# Arguments are data, never interpolated AppleScript. Sends into the existing
# conversation to preserve its iMessage/SMS service and recipient.
SCRIPT = '''on run argv
    tell application "Messages"
        set targetChat to chat id (item 1 of argv)
        send (item 2 of argv) to targetChat
    end tell
end run'''


def send(chat_guid, text):
    try:
        subprocess.run(["/usr/bin/osascript", "-e", SCRIPT, chat_guid, text],
                       check=True, capture_output=True, text=True, timeout=20)
    except subprocess.CalledProcessError as exc:
        if '-1743' in (exc.stderr or ''):
            raise SendError('macOS blocked Messages automation. Enable Inbox → Messages under System Settings → Privacy & Security → Automation, then try a new send.') from None
        if any(code in (exc.stderr or '') for code in ('-1728','-600')):
            raise SendError('Messages could not find this conversation. Check that Messages is signed in and the conversation is available.') from None
        raise SendError('Messages reported an error and delivery could not be confirmed. Check the conversation before trying again.',uncertain=True) from None
    except subprocess.TimeoutExpired:
        raise SendError('Messages did not confirm acceptance before the timeout. Delivery is uncertain; check Messages before attempting another send.',uncertain=True) from None
