import re
import os

def iso_duration_parser(iso8601: str) -> int: # ISO 8601 Parser
    """
    ISO8601 Durations Parser.
    Only supports days, hours, minutes, and seconds.
    """
    if not iso8601:
        return 0
    
    if iso8601 == "PUSH":
        return -1

    match = re.match(
        r'P((?P<years>\d+)Y)?((?P<months>\d+)M)?((?P<weeks>\d+)W)?((?P<days>\d+)D)?(T((?P<hours>\d+)H)?((?P<minutes>\d+)M)?((?P<seconds>\d+)S)?)?',
        iso8601
    )
    if not match:
        raise ValueError("Invalid ISO8601 duration string")
    match = match.groupdict()
    return int(match['days'] or 0)*24*3600 + \
        int(match['hours'] or 0)*3600 + \
        int(match['minutes'] or 0)*60 + \
        int(match['seconds'] or 0)

def iso_duration_maker(duration: int) -> str:
    """
    ISO8601 Durations Maker.
    Only supports days, hours, minutes, and seconds. (MAX: 31 days)
    """
    if duration == -1:
        return "PUSH"
        
    if duration < 0:
        raise ValueError("Duration must be a positive integer.")
    if duration > 2678399:
        raise ValueError("Duration must be less than 31 days.")
    
    if duration == 0:
        return "" # Return empty string to maintain compatibility with "" in config-example.json

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

def set_rsync_user(url: str, user: str):
    """
    Set rsync user
    Args:
        url (str): URL to set
        user (str): User to set
    Returns:
        str: URL with user
    """

    if not user:
        return url

    if url.startswith("rsync://"):
        return url.replace("rsync://", f"rsync://{user}@", 1)
    elif "::" in url:
        return f"{user}@{url}"
    else:
        raise ValueError("Invalid URL")

def checkPermission() -> bool:
    """
    Check that user has root permission or sudo permission
    Args:
        None
    Returns:
        bool: True if user has root permission or sudo permission
    """
    if os.getuid() == 0:
        return True
    
    return not os.system("sudo -n true")

def is_command_exists(command: str) -> bool:
    """
    Check that command exists
    Args:
        command (str): Command to check
    Returns:
        bool: True if command exists
    """
    return not os.system(f"command -v {command} > /dev/null")
