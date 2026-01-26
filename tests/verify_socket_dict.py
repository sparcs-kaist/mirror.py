
import socket
import os
import threading
import time
import json
import struct
import array
from mirror.socket import send_fds, recv_fds

def server_thread(sock_path):
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)
    
    conn, _ = server.accept()
    
    # 1. Receive data (expecting dict) + FDs
    try:
        msg, fds = recv_fds(conn)
        # Verify msg is dict
        is_dict = isinstance(msg, dict)
        response = {
            "received_type": str(type(msg)),
            "is_dict": is_dict,
            "received_data": msg,
            "fds_count": len(fds)
        }
        
        # Echo back
        # Create a dummy FD to send back
        f = open("/dev/null", "r")
        fd_to_send = f.fileno()
        
        send_fds(conn, response, [fd_to_send])
        f.close() # Close in this process, sent to other
        
        for fd in fds:
            os.close(fd)
            
    except Exception as e:
        print(f"Server Error: {e}")
    finally:
        conn.close()
        server.close()
        if os.path.exists(sock_path):
            os.unlink(sock_path)

def test_socket_exchange():
    sock_path = "/tmp/mirror_test_socket.sock"
    
    t = threading.Thread(target=server_thread, args=(sock_path,))
    t.start()
    time.sleep(1) # Wait for server
    
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(sock_path)
        
        # Send a dict and a dummy FD
        f = open("/dev/null", "r")
        fd_to_send = f.fileno()
        data = {"command": "test", "params": [1, 2, 3]}
        
        send_fds(client, data, [fd_to_send])
        f.close()
        
        # Receive response
        response, fds = recv_fds(client)
        
        print("Client Received:", json.dumps(response, indent=2))
        print("Client Received FDs:", fds)
        
        for fd in fds:
            os.close(fd)
            
    except Exception as e:
        print(f"Client Error: {e}")
    finally:
        client.close()
        t.join()

if __name__ == "__main__":
    test_socket_exchange()
