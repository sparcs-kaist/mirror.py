import re
import os
import shutil
import subprocess

def parse_iso_duration(iso8601: str) -> int:
    """Parse an ISO 8601 duration string into total seconds.

    Only supports days, hours, minutes, and seconds.

    Args:
        iso8601(str): ISO 8601 duration string (e.g. "P1DT2H3M4S") or "PUSH" or "".

    Return:
        seconds(int): Total duration in seconds. Returns -1 for "PUSH", 0 for "".
    """
    if not iso8601:
        return 0
    
    if iso8601 == "PUSH":
        return -1

    match = re.fullmatch(
        r'P((?P<years>\d+)Y)?((?P<months>\d+)M)?((?P<weeks>\d+)W)?((?P<days>\d+)D)?(T((?P<hours>\d+)H)?((?P<minutes>\d+)M)?((?P<seconds>\d+)S)?)?',
        iso8601
    )
    if not match:
        raise ValueError("Invalid ISO8601 duration string")
    match = match.groupdict()
    if match["years"] or match["months"] or match["weeks"]:
        raise ValueError("Unsupported ISO8601 duration unit")
    supported = (match["days"], match["hours"], match["minutes"], match["seconds"])
    if not any(supported):
        raise ValueError("Invalid ISO8601 duration string")
    if "T" in iso8601 and not any((match["hours"], match["minutes"], match["seconds"])):
        raise ValueError("Invalid ISO8601 duration string")
    return int(match['days'] or 0)*24*3600 + \
        int(match['hours'] or 0)*3600 + \
        int(match['minutes'] or 0)*60 + \
        int(match['seconds'] or 0)

def format_iso_duration(duration: int) -> str:
    """Format total seconds into an ISO 8601 duration string.

    Only supports days, hours, minutes, and seconds.

    Args:
        duration(int): Duration in seconds. Use -1 for "PUSH".

    Return:
        iso8601(str): ISO 8601 duration string, "PUSH" for -1, or "" for 0.
    """
    if duration == -1:
        return "PUSH"

    if duration < 0:
        raise ValueError("Duration must be a positive integer.")

    if duration == 0:
        return ""

    iso8601 = "P"
    
    days = duration // 86400
    duration %= 86400
    if days > 0:
        iso8601 += f"{days}D"
    
    if duration > 0:
        iso8601 += "T"
        
        hours = duration // 3600
        duration %= 3600
        if hours > 0:
            iso8601 += f"{hours}H"
            
        minutes = duration // 60
        duration %= 60
        if minutes > 0:
            iso8601 += f"{minutes}M"
            
        seconds = duration
        if seconds > 0:
            iso8601 += f"{seconds}S"
            
    return iso8601

def parse_file_mode(value: str) -> int:
    """Parse an octal file-mode string into an integer.

    Accepts forms like "0770", "0o770", or "770"; all interpreted as base-8.

    Args:
        value(str): Octal mode string from configuration.

    Return:
        mode(int): Parsed file mode as an integer (e.g. 0o770).
    """
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError('file mode must be an octal string like "0770"')
    text = value.strip()
    if not text:
        raise ValueError("file mode string is empty")
    try:
        mode = int(text, 8)
    except ValueError as exc:
        raise ValueError(f"invalid octal file mode {value!r}: {exc}") from exc
    if not (0 <= mode <= 0o7777):
        raise ValueError(f"file mode {value!r} out of range (0..0o7777)")
    return mode


def set_rsync_user(url: str, user: str) -> str:
    """Embed a username into an rsync URL.

    Args:
        url(str): Rsync source URL (rsync:// or :: form).
        user(str): Username to embed.

    Return:
        url_with_user(str): URL with the username inserted.
    """

    if not user:
        return url

    if url.startswith("rsync://"):
        return url.replace("rsync://", f"rsync://{user}@", 1)
    elif "::" in url:
        return f"{user}@{url}"
    else:
        raise ValueError("Invalid URL")

def has_root_or_sudo() -> bool:
    """Check that user has root or passwordless sudo permission.

    Return:
        ok(bool): True if EUID is 0 or `sudo -n true` succeeds.
    """
    if os.getuid() == 0:
        return True
    result = subprocess.run(["sudo", "-n", "true"], check=False, capture_output=True)
    return result.returncode == 0

def command_exists(command: str) -> bool:
    """Check whether the given command is available on PATH.

    Args:
        command(str): Command name to look up.

    Return:
        exists(bool): True if the command is found on PATH.
    """
    return shutil.which(command) is not None
