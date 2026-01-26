import socket
import os
import json
import pytest
from mirror.socket import send_fds, recv_fds

def test_large_payload_with_fds():
    """
    Test sending a large JSON payload (> 4096 bytes) along with FDs using send_fds/recv_fds.
    Verifies that recv_fds correctly reassembles the message.
    """
    # Create a socket pair (Unix domain sockets)
    parent_sock, child_sock = socket.socketpair(socket.AF_UNIX)
    
    # Generate a large payload (approx 50KB)
    large_data = {"key": "x" * 50000, "info": "This is a large payload"}
    
    # We will send our own stdin FD as a test
    fd_to_send = os.dup(0) # Duplicate stdin so we don't mess up the actual stdin
    
    try:
        # Send from parent
        send_fds(parent_sock, large_data, [fd_to_send])
        parent_sock.close() # Close sender side after sending
        
        # Receive from child
        # Force a small initial buffer in recv_fds to ensure logic works, 
        # but the function signature allows specifying max_len. 
        # Our implementation uses a loop, so even with default 4096 it should work for 50KB.
        received_msg, received_fds = recv_fds(child_sock)
        
        # Verify Message
        assert received_msg["info"] == "This is a large payload"
        assert len(received_msg["key"]) == 50000
        
        # Verify FDs
        assert len(received_fds) == 1
        assert received_fds[0] > 0
        
        # Verify the received FD is valid
        os.fstat(received_fds[0])
        
        # Clean up received FD
        os.close(received_fds[0])
        
    finally:
        child_sock.close()
        os.close(fd_to_send)

if __name__ == "__main__":
    test_large_payload_with_fds()
