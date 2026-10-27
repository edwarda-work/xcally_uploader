"""Desktop launcher; packaged with Python, Tcl/Tk, and the web application."""
import socket
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox

import uvicorn


def main():
    window = tk.Tk()
    window.title("xCALLY Upload Automation")
    window.geometry("440x180")
    window.resizable(False, False)
    status = tk.StringVar(value="Starting application…")
    tk.Label(window, textvariable=status, wraplength=410, pady=20).pack()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # One server per user machine prevents concurrent campaign workers.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 8765))
        listener.listen(128)
        from main import app
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=8765, loop="asyncio",
            http="h11", ws="none", log_config=None, access_log=False,
        ))
    except Exception as exc:
        listener.close()
        messagebox.showerror("Unable to start xCALLY", f"{exc}\n\nIf xCALLY is already running, use its existing window.")
        window.destroy()
        return

    url = "http://127.0.0.1:8765"
    worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=False)
    worker.start()
    closing = False

    def stop():
        nonlocal closing
        if closing:
            return
        if not messagebox.askokcancel("Stop xCALLY", "Stop the application? Wait for all uploads and campaign updates to finish first."):
            return
        closing = True
        status.set("Stopping; waiting for active work to finish…")
        server.should_exit = True

    def poll():
        if not worker.is_alive():
            listener.close()
            if not closing:
                messagebox.showerror("xCALLY stopped", "The server stopped unexpectedly. Close this window and try again.")
            window.destroy()
            return
        if closing:
            window.after(200, poll)
            return
        if server.started and open_button["state"] == "disabled":
            status.set("Application running. Keep this window open while working.")
            open_button.config(state="normal")
            webbrowser.open(url)
        window.after(200, poll)

    open_button = tk.Button(window, text="Open application", state="disabled", command=lambda: webbrowser.open(url))
    open_button.pack()
    tk.Button(window, text="Stop application", command=stop).pack(pady=8)
    window.protocol("WM_DELETE_WINDOW", stop)
    window.after(200, poll)
    window.mainloop()


if __name__ == "__main__":
    main()
