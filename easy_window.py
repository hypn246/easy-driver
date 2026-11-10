import ctypes
import json
import os
import platform
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.request
import webbrowser
import winreg
import zipfile
import tkinter as tk
from tkinter import ttk, messagebox

# Windows only. Needs administrator rights.

NO_WINDOW = 0x08000000
WINGET_OK = (0, -1978335135, -1978335189)
HEADERS = {"User-Agent": "Mozilla/5.0"}
LAPTOP_CHASSIS = (8, 9, 10, 14, 30, 31, 32)
KEY_PATTERN = re.compile(r"^([A-Z0-9]{5}-){4}[A-Z0-9]{5}$")
NVIDIA_PAGE = "https://www.nvidia.com/Download/index.aspx"
AMD_PAGE = "https://www.amd.com/en/support/download/drivers.html"

VENDORS = [
    (r"dell", "Dell", "winget", "Dell.CommandUpdate.Universal"),
    (r"lenovo", "Lenovo", "winget", "Lenovo.SystemUpdate"),
    (r"hewlett|\bhp\b", "HP", "page", "https://support.hp.com/drivers"),
    (r"asus", "ASUS", "page", "https://www.asus.com/support/download-center/"),
    (r"acer", "Acer", "page", "https://www.acer.com/support"),
    (r"micro-star|\bmsi\b", "MSI", "page", "https://www.msi.com/support/download"),
    (r"gigabyte", "Gigabyte", "page", "https://www.gigabyte.com/Support/Consumer/Download"),
    (r"asrock", "ASRock", "page", "https://www.asrock.com/support/index.asp"),
]

DETECT_SCRIPT = r"""
$cs = Get-CimInstance Win32_ComputerSystem
$bb = Get-CimInstance Win32_BaseBoard
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$gpu = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })
$chassis = @((Get-CimInstance Win32_SystemEnclosure).ChassisTypes)
[pscustomobject]@{
    maker = $cs.Manufacturer
    model = $cs.Model
    board = ($bb.Manufacturer + ' ' + $bb.Product)
    cpu = $cpu.Name
    gpu = $gpu
    chassis = $chassis
} | ConvertTo-Json -Compress
"""

WU_SCRIPT = r"""
$session = New-Object -ComObject Microsoft.Update.Session
$searcher = $session.CreateUpdateSearcher()
$found = $searcher.Search("IsInstalled=0 and Type='Driver'")
if ($found.Updates.Count -eq 0) {
    Write-Output 'No driver updates found on Windows Update.'
    exit 0
}
$list = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $found.Updates) {
    Write-Output ('Found: ' + $u.Title)
    if (-not $u.EulaAccepted) { $u.AcceptEula() }
    [void]$list.Add($u)
}
$downloader = $session.CreateUpdateDownloader()
$downloader.Updates = $list
Write-Output 'Downloading...'
[void]$downloader.Download()
$installer = $session.CreateUpdateInstaller()
$installer.Updates = $list
Write-Output 'Installing...'
$result = $installer.Install()
Write-Output ('Result code: ' + $result.ResultCode + ' (2 = success). Reboot required: ' + $result.RebootRequired)
"""

msgs = queue.Queue()


def log(text):
    msgs.put(("log", text))


def run(cmd):
    log("> " + (cmd if isinstance(cmd, str) else " ".join(cmd)))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace",
                         creationflags=NO_WINDOW)
    for line in p.stdout:
        line = line.strip()
        if line and line not in ("-", "\\", "|", "/") and "█" not in line and "▒" not in line:
            log("  " + line)
    return p.wait()


def write_ps(script):
    f = tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8-sig")
    f.write("[Console]::OutputEncoding = [Text.Encoding]::UTF8\n" + script)
    f.close()
    return f.name


def run_ps(script):
    path = write_ps(script)
    try:
        return run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path])
    finally:
        os.remove(path)


def ps_output(script):
    path = write_ps(script)
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", creationflags=NO_WINDOW)
        return result.stdout
    finally:
        os.remove(path)


def has_internet():
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=4).close()
        return True
    except OSError:
        return False


def download(url, filename):
    path = os.path.join(tempfile.gettempdir(), filename)
    log("Downloading " + url)
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response, open(path, "wb") as f:
        shutil.copyfileobj(response, f)
    return path


def install_exe(url, filename, args):
    path = download(url, filename)
    return run([path] + args) == 0


def winget_install(winget_id):
    if not shutil.which("winget"):
        log("winget was not found on this PC.")
        return False
    code = run(["winget", "install", "-e", "--id", winget_id, "--silent",
                "--accept-package-agreements", "--accept-source-agreements"])
    return code in WINGET_OK


def install_chrome():
    if winget_install("Google.Chrome"):
        return True
    log("Trying direct download instead...")
    return install_exe("https://dl.google.com/chrome/install/ChromeStandaloneSetup64.exe",
                       "chrome_setup.exe", ["/silent", "/install"])


def install_winrar():
    if winget_install("RARLab.WinRAR"):
        return True
    log("Trying direct download instead...")
    request = urllib.request.Request("https://www.rarlab.com/download.htm", headers=HEADERS)
    html = urllib.request.urlopen(request, timeout=30).read().decode("utf-8", "ignore")
    match = re.search(r"/rar/(winrar-x64-\d+\.exe)", html)
    if not match:
        log("Could not find the WinRAR download link.")
        return False
    return install_exe("https://www.rarlab.com/rar/" + match.group(1), match.group(1), ["/S"])


def install_unikey():
    bits = "win64" if platform.machine().endswith("64") else "win32"
    zip_path = download("https://www.unikey.org/assets/release/unikey46RC2-230919-" + bits + ".zip",
                        "unikey.zip")
    folder = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "UniKey")
    os.makedirs(folder, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(folder)

    exe = ""
    for root, dirs, files in os.walk(folder):
        if "UniKeyNT.exe" in files:
            exe = os.path.join(root, "UniKeyNT.exe")
    if not exe:
        log("UniKeyNT.exe was not found after extracting.")
        return False

    run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, run_key, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "UniKey", 0, winreg.REG_SZ, '"' + exe + '"')

    desktop = os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop", "UniKey.lnk")
    run_ps("$s = (New-Object -ComObject WScript.Shell).CreateShortcut('" + desktop + "')\n"
           "$s.TargetPath = '" + exe + "'\n$s.Save()")
    subprocess.Popen(["explorer.exe", exe])
    log("UniKey installed to " + folder + " and set to start with Windows.")
    return True


def activate_with_key(key):
    slmgr = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "slmgr.vbs")
    run(["cscript", "//nologo", slmgr, "/ipk", key])
    run(["cscript", "//nologo", slmgr, "/ato"])


def detect_device():
    try:
        info = json.loads(ps_output(DETECT_SCRIPT))
    except Exception:
        info = {}
    gpu = info.get("gpu") or []
    if isinstance(gpu, str):
        gpu = [gpu]
    chassis = info.get("chassis") or []
    if isinstance(chassis, int):
        chassis = [chassis]
    return {
        "maker": info.get("maker") or "",
        "model": info.get("model") or "",
        "board": info.get("board") or "",
        "cpu": info.get("cpu") or "",
        "gpu": gpu,
        "laptop": any(c in LAPTOP_CHASSIS for c in chassis),
    }


def find_vendor(info):
    for text in (info["maker"], info["board"]):
        for pattern, name, how, target in VENDORS:
            if re.search(pattern, text.lower()):
                return name, how, target
    return None


def install_drivers(info, laptop):
    kind = "Laptop" if laptop else "PC"
    log("Device type: " + kind + " | " + info["maker"] + " " + info["model"])

    log("Step 1: Windows Update drivers (can take several minutes)...")
    run_ps(WU_SCRIPT)

    log("Step 2: " + ("laptop maker tool" if laptop else "PC / motherboard maker") + "...")
    vendor = find_vendor(info)
    if vendor is None:
        log("Maker not recognized. Search your model on the maker's website: "
            + (info["model"] or info["board"]))
    else:
        name, how, target = vendor
        if how == "winget":
            if winget_install(target) and name == "Dell":
                dcu = r"C:\Program Files\Dell\CommandUpdate\dcu-cli.exe"
                if os.path.exists(dcu):
                    run([dcu, "/applyUpdates", "-silent", "-reboot=disable"])
            elif name == "Lenovo":
                log("Open Lenovo System Update and install all recommended updates.")
        else:
            log(name + ": opening the official driver page. Search for: " + (info["model"] or info["board"]))
            webbrowser.open(target)

    log("Step 3: chipset and graphics...")
    cpu = info["cpu"].lower()
    if "intel" in cpu:
        winget_install("Intel.IntelDriverAndSupportAssistant")
    elif "amd" in cpu and not laptop:
        log("Opening the AMD chipset driver page.")
        webbrowser.open(AMD_PAGE)

    gpu = " ".join(info["gpu"]).lower()
    if "nvidia" in gpu:
        log("NVIDIA graphics found. Opening the NVIDIA driver page.")
        webbrowser.open(NVIDIA_PAGE)
    if "amd" in gpu or "radeon" in gpu:
        log("AMD graphics found. Opening the AMD driver page.")
        webbrowser.open(AMD_PAGE)


class App:
    def __init__(self, root):
        self.root = root
        self.info = None
        self.device = tk.StringVar(value="PC")
        self.use = {name: tk.BooleanVar(value=True) for name in ("Google Chrome", "WinRAR", "UniKey", "Drivers")}
        self.key = tk.StringVar()
        root.title("Easy Window")
        root.geometry("780x680")
        self.build_ui()
        self.root.after(100, self.poll)
        self.check_internet()
        threading.Thread(target=self.load_device, daemon=True).start()

    def build_ui(self):
        pad = {"padx": 12, "pady": 6}

        net = ttk.LabelFrame(self.root, text="1. Internet")
        net.pack(fill="x", **pad)
        self.net_label = ttk.Label(net, text="Checking...")
        self.net_label.pack(side="left", padx=8, pady=8)
        ttk.Button(net, text="Network settings",
                   command=lambda: os.startfile("ms-settings:network")).pack(side="right", padx=4)
        ttk.Button(net, text="Check again", command=self.check_internet).pack(side="right", padx=4)

        dev = ttk.LabelFrame(self.root, text="2. Device type")
        dev.pack(fill="x", **pad)
        row = ttk.Frame(dev)
        row.pack(fill="x")
        ttk.Radiobutton(row, text="PC (desktop)", value="PC", variable=self.device).pack(side="left", padx=8, pady=4)
        ttk.Radiobutton(row, text="Laptop", value="Laptop", variable=self.device).pack(side="left", padx=8)
        self.device_label = ttk.Label(dev, text="Detecting hardware...", wraplength=720)
        self.device_label.pack(fill="x", padx=8, pady=(0, 8))

        soft = ttk.LabelFrame(self.root, text="3. Install")
        soft.pack(fill="x", **pad)
        for name, var in self.use.items():
            ttk.Checkbutton(soft, text=name, variable=var).pack(side="left", padx=8, pady=8)

        act = ttk.LabelFrame(self.root, text="4. Activate Windows with your own product key (optional)")
        act.pack(fill="x", **pad)
        ttk.Label(act, text="Key:").pack(side="left", padx=8, pady=8)
        ttk.Entry(act, textvariable=self.key, width=34).pack(side="left")
        ttk.Button(act, text="Activation settings",
                   command=lambda: os.startfile("ms-settings:activation")).pack(side="right", padx=8)

        self.start_btn = ttk.Button(self.root, text="Start setup", command=self.start)
        self.start_btn.pack(**pad)
        self.bar = ttk.Progressbar(self.root, mode="indeterminate")
        self.bar.pack(fill="x", **pad)

        box = ttk.Frame(self.root)
        box.pack(fill="both", expand=True, **pad)
        self.logbox = tk.Text(box, height=12, state="disabled", wrap="word")
        scroll = ttk.Scrollbar(box, command=self.logbox.yview)
        self.logbox.config(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.logbox.pack(side="left", fill="both", expand=True)

    def write(self, text):
        self.logbox.config(state="normal")
        self.logbox.insert("end", text + "\n")
        self.logbox.see("end")
        self.logbox.config(state="disabled")

    def poll(self):
        while not msgs.empty():
            kind, value = msgs.get()
            if kind == "log":
                self.write(value)
            elif kind == "net":
                self.net_label.config(text="Connected" if value else "Not connected",
                                      foreground="green" if value else "red")
            elif kind == "device":
                self.show_device(value)
            elif kind == "done":
                self.bar.stop()
                self.start_btn.config(state="normal")
        self.root.after(100, self.poll)

    def show_device(self, info):
        if not (info["maker"] or info["model"]):
            self.device_label.config(text="Could not detect hardware. Choose PC or Laptop yourself.")
            return
        self.device.set("Laptop" if info["laptop"] else "PC")
        self.device_label.config(text=info["maker"] + " " + info["model"] + " | " + info["cpu"]
                                 + " | GPU: " + ", ".join(info["gpu"]))

    def check_internet(self):
        self.net_label.config(text="Checking...", foreground="")
        threading.Thread(target=lambda: msgs.put(("net", has_internet())), daemon=True).start()

    def load_device(self):
        self.info = detect_device()
        msgs.put(("device", self.info))

    def start(self):
        chosen = [name for name, var in self.use.items() if var.get()]
        key = self.key.get().strip().upper()
        if not chosen and not key:
            messagebox.showinfo("Easy Window", "Select at least one item.")
            return
        if key and not KEY_PATTERN.match(key):
            messagebox.showerror("Easy Window", "Product key format: XXXXX-XXXXX-XXXXX-XXXXX-XXXXX")
            return
        self.start_btn.config(state="disabled")
        self.bar.start(12)
        laptop = self.device.get() == "Laptop"
        threading.Thread(target=self.run_setup, args=(chosen, key, laptop), daemon=True).start()

    def run_setup(self, chosen, key, laptop):
        try:
            if not has_internet():
                msgs.put(("net", False))
                log("No internet. Connect to Wi-Fi or LAN, then press Start again.")
                return
            msgs.put(("net", True))

            steps = [("Google Chrome", install_chrome), ("WinRAR", install_winrar), ("UniKey", install_unikey)]
            for name, func in steps:
                if name not in chosen:
                    continue
                log("== " + name + " ==")
                try:
                    ok = func()
                except Exception as e:
                    ok = False
                    log("Error: " + str(e))
                log(name + ": " + ("OK" if ok else "FAILED"))

            if key:
                log("== Windows activation ==")
                try:
                    activate_with_key(key)
                except Exception as e:
                    log("Error: " + str(e))

            if "Drivers" in chosen:
                log("== Drivers ==")
                try:
                    install_drivers(self.info or detect_device(), laptop)
                except Exception as e:
                    log("Error: " + str(e))

            log("Finished. Restart Windows to complete driver installation.")
        finally:
            msgs.put(("done", None))


def ensure_admin():
    if ctypes.windll.shell32.IsUserAnAdmin():
        return
    if getattr(sys, "frozen", False):
        params = " ".join(sys.argv[1:])
    else:
        params = " ".join(['"' + sys.argv[0] + '"'] + sys.argv[1:])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    sys.exit()


if __name__ == "__main__":
    ensure_admin()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()
