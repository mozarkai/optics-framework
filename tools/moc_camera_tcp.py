import socket
import cv2
import numpy as np

server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_socket.bind(('127.0.0.1', 3000))
server_socket.listen(1)
print("Server listening on 127.0.0.1:3000...")
conn, addr = server_socket.accept()
print(f"Connected by {addr}")

while True:
    data = conn.recv(1024).decode()
    if data.strip() == "screenshot":
        # Use a test image or webcam capture
        img = cv2.imread("test.png")  # Replace with valid image path
        if img is None:
            img = np.zeros((720, 1280, 3), dtype=np.uint8)  # Fallback black image
        _, img_encoded = cv2.imencode('.png', img)
        img_bytes = img_encoded.tobytes()
        length = len(img_bytes)
        conn.sendall(length.to_bytes(8, byteorder='little', signed=False))
        conn.sendall(img_bytes)
    if not data:
        break
conn.close()
server_socket.close()
