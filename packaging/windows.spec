from pathlib import Path

root = Path(SPECPATH).parent
assets = ["index.html", "agents.js", "campaigns.js", "performance.js", "campaign-controls.js"]
a = Analysis(
    [str(root / "desktop.py")], pathex=[str(root)],
    binaries=[], datas=[(str(root / name), ".") for name in assets],
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="XcallyUploads", debug=False, strip=False, upx=False, console=False,
)
