"""Windows GUI launcher for packaged ARCHITECT AI STUDIO."""
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path
from http.server import ThreadingHTTPServer
import server

def main():
    server.setup()
    try:
        httpd = ThreadingHTTPServer(('127.0.0.1', 8765), server.Handler)
    except OSError as exc:
        import tkinter.messagebox
        tkinter.messagebox.showerror('ARCHITECT AI STUDIO', 'Could not start the application on port 8765.\n' + str(exc))
        return
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    webbrowser.open('http://127.0.0.1:8765')
    # Keep the process alive as long as the GUI is running. A tiny native control window enables clean shutdown.
    import tkinter as tk
    root = tk.Tk()
    root.title('ARCHITECT AI STUDIO')
    root.geometry('390x160')
    root.resizable(False,False)
    tk.Label(root, text='ARCHITECT AI STUDIO', font=('Segoe UI', 16, 'bold')).pack(pady=(19,4))
    tk.Label(root,text='Running locally at http://127.0.0.1:8765').pack()
    tk.Button(root, text='Open in Browser', command=lambda: webbrowser.open('http://127.0.0.1:8765')).pack(pady=9)
    def close():
        httpd.shutdown()
        httpd.server_close()
        root.destroy()
    root.protocol('WM_DELETE_WINDOW',close)
    root.mainloop()

if __name__=='__main__':
    main()
