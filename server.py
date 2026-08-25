from silas import SilasModel
import os
import socket
import threading
import psutil
import json
import re
from datetime import datetime, timedelta


class SilasServer:
    def __init__(self):
        self.reminders = []
        self.reminders_lock = threading.Lock()

        if os.path.exists("reminders.json"):
            with open("reminders.json", "r", encoding="utf-8") as f:
                self.reminders = json.load(f)

        self.silas_model = SilasModel() 

    def ask_silas(self, prompt):
        print(">>> ASK_SILAS CALLED <<<")
        return self.silas_model.chat(prompt)

    def server_status(self):
        cpu = psutil.cpu_percent(interval=1)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        parts = [
            f"CPU is at {cpu} percent",
            f"Memory is at {ram.percent} percent",
            f"Disk is at {disk.percent} percent used",
        ]
        return ". ".join(parts)

if __name__ == "__main__":
    silas = SilasServer()

    def route_message(silas, message):
        low = message.lower()

        if "how are you feeling" in low or "how you feeling" in low or "server status" in low:
            return silas.server_status()
        elif 'time' in low:
            return f"The time is {datetime.now().hour}:{datetime.now().minute} on {datetime.now().date()} Master Kershaw."
        else:
            return silas.ask_silas(message)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 1337))
    s.listen()

    def connection(conn, addr):
        print(f"Connection from: {addr}")
        try:
            while True:
                data = conn.recv(1024)
                if not data:
                    break
                message = data.decode().strip()
                if not message:
                    continue
                print(f"[{addr}] Received: {message!r}")
                try:
                    response = route_message(silas, message)
                except Exception as e:
                    print(f"[{addr}] Handler error: {e}")
                    response = "Sorry, something went wrong processing that."
                print(f"[{addr}] Replying: {response!r}")
                conn.sendall(response.encode())
        except Exception as e:
            print(f"Error from {addr}: {e}")
        finally:
            conn.close()
            print(f"Connection closed: {addr}")

    print("Silas server listening on port 1337...")
    while True:
        conn, addr = s.accept()
        threading.Thread(target=connection, args=(conn, addr), daemon=True).start()